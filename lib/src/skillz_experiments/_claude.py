from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

from skillz_experiments._candidate import Candidate, make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import Case, digest, mapping, string
from skillz_experiments._contract import resolve
from skillz_experiments._evaluation import fixture_result, usage
from skillz_experiments._evaluator import answer_schema
from skillz_experiments._isolation import listening, probe
from skillz_experiments._runtime import Budget, process

TOOLS = "Bash,Read,Skill"
PROBE_SKILL = "skillz"
AUTHENTICATION = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")


PERMISSION_DENY_ROOTS = ("/home", "/root", "/Users", "/mnt", "/media", "/opt", "/srv", "/workspaces", "/data")
RUNTIME_READ = ("/usr", "/bin", "/lib", "/lib32", "/lib64", "/libx32", "/proc/self", "/etc/ld.so.cache", "/etc/ld.so.conf",
                "/etc/ld.so.conf.d", "/etc/alternatives", "/etc/localtime", "/etc/passwd", "/etc/group", "/etc/nsswitch.conf",
                "/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")
MACOS_READ = ("/System", "/Library")
SEATBELT_RUNTIME = ("/usr", "/bin", "/System", "/Library/Developer/CommandLineTools", "/private/var/db/dyld")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def settings(workspace: Path) -> dict[str, object]:
    """Return the sandbox floor. A missing sandbox stops the run, and no command leaves the sandbox.

    Sandboxed commands cannot read the host from `/`. The narrower allow wins, so they read only the
    workspace and the runtime roots. The Read tool follows permission rules, not the sandbox, so a deny
    rule covers the host roots for that tool.
    """
    runtime = [*RUNTIME_READ, *(MACOS_READ if sys.platform == "darwin" else ())]
    return {"sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                        "network": {"allowedDomains": [], "strictAllowlist": True},
                        "filesystem": {"denyRead": ["/"],
                                       "denyWrite": _unique([f"{workspace}/.agents", f"{workspace.resolve()}/.agents"]),
                                       "allowRead": _unique([str(workspace), str(workspace.resolve()), *runtime])}},
            "disableBundledSkills": True,
            "permissions": {"allow": ["Skill"],
                            "deny": [f"Read(/{root}/**)" for root in _unique([*PERMISSION_DENY_ROOTS, str(Path.home())])]}}


def _events(stdout: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            events.append(mapping(cast(object, json.loads(line))))
        except (ValueError, RecursionError):
            raise RuntimeError("invalid Claude Code JSON event stream") from None
    return events


def _final(events: list[dict[str, object]]) -> dict[str, object] | None:
    return next((event for event in reversed(events) if event.get("type") == "result"), None)


def _loaded_skills(events: list[dict[str, object]]) -> list[str] | None:
    """Return the skill names in every init event, or None when the stream has no init event."""
    inits = [event for event in events if event.get("type") == "system" and event.get("subtype") == "init"]
    if not inits:
        return None
    loaded: set[str] = set()
    for event in inits:
        raw = event.get("skills")
        items = cast(list[object], raw) if isinstance(raw, list) else []
        names = [cast(dict[str, object], item).get("name") if isinstance(item, dict) else item for item in items]
        loaded.update(name for name in names if isinstance(name, str))
    return sorted(loaded)


def _failure(returncode: int, stderr: str, events: list[dict[str, object]], skills: list[str]) -> str | None:
    """Name the isolation failure that a probe run shows, or return None when the run is isolated."""
    final = _final(events)
    errored = returncode != 0 or (final is not None and final.get("is_error") is True)
    detail = (stderr[:8192] + " " + (str(final.get("result", ""))[:2048] if final else "")).lower()
    if returncode != 0 and "sandbox" in detail:
        return "sandbox unavailable"
    if errored and any(word in detail for word in ("login", "api key", "authenticat", "unauthorized", "401", "oauth")):
        return "authentication failed"
    loaded = _loaded_skills(events)
    if loaded is None:
        return "no init event" if returncode == 0 else f"execution failed (exit {returncode})"
    if set(loaded) - set(skills):
        return f"foreign skill in init event: {loaded}"
    if loaded != sorted(skills):
        return f"expected skill missing from init event: {loaded}"
    if errored or final is None:
        return f"execution failed (exit {returncode})"
    return None


def _trace(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """Turn Bash tool calls into completed command events. A tool error means exit code 1."""
    commands: dict[str, str] = {}
    trace: list[dict[str, object]] = []
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in (cast(dict[str, object], item) for item in cast(list[object], content) if isinstance(item, dict)):
            if block.get("type") == "tool_use" and block.get("name") == "Bash" and isinstance(block.get("id"), str):
                command = mapping(block.get("input")).get("command") if isinstance(block.get("input"), dict) else None
                if isinstance(command, str):
                    commands[cast(str, block["id"])] = command
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in commands:
                trace.append({"type": "item.completed", "item": {
                    "type": "command_execution", "command": commands[cast(str, block["tool_use_id"])],
                    "exit_code": 1 if block.get("is_error") is True else 0}})
    return trace


def _token_events(final: dict[str, object]) -> list[dict[str, object]]:
    """Map Claude usage to the shared counts. Input tokens include cache creation and cache reads."""
    raw = final.get("usage")
    if not isinstance(raw, dict) or not raw:
        return []
    counts = mapping(cast(object, raw))

    def count(name: str) -> int:
        value = counts.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    cached = count("cache_read_input_tokens")
    return [{"type": "turn.completed", "usage": {
        "input_tokens": count("input_tokens") + count("cache_creation_input_tokens") + cached,
        "cached_input_tokens": cached, "output_tokens": count("output_tokens")}}]


def _declares(text: str, skill: str) -> bool:
    """Check that the SKILL.md frontmatter names the contract skill and has a description."""
    parts = text.split("---", 2)
    if not text.startswith("---") or len(parts) != 3:
        return False
    fields = {key.strip(): value.strip().strip("\"'") for key, _, value in
              (line.partition(":") for line in parts[1].splitlines())}
    return fields.get("name") == skill and bool(fields.get("description"))


def _bash_control_passed(events: list[dict[str, object]], command_marker: str, control_token: str) -> bool:
    """Accept the control token only from the Bash `tool_result` that answers the `cat` of the control file."""
    uses: set[str] = set()
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if isinstance(message, dict) else None
        for block in cast(list[object], content) if isinstance(content, list) else []:
            item = mapping(block)
            if item.get("type") == "tool_use" and item.get("name") == "Bash":
                command = mapping(item.get("input", {})).get("command")
                if isinstance(command, str) and command_marker in command and isinstance(item.get("id"), str):
                    uses.add(cast(str, item["id"]))
            if item.get("type") == "tool_result" and item.get("tool_use_id") in uses and control_token in json.dumps(item):
                return True
    return False


def _read_failure(events: list[dict[str, object]], sealed_token: str, sealed: str, control_token: str,
                  control_file: str) -> str | None:
    """Judge the read probe. It needs evidence that Bash ran: a command on the sealed path, and the workspace token."""
    text = json.dumps(events)
    if sealed_token in text:
        return "read isolation failed: a host file is readable"
    commands = [cast(str, mapping(item["item"])["command"]) for item in _trace(events)]
    if not any(sealed in command for command in commands):
        return "read probe has no evidence: no Bash command read the sealed host file"
    if not _bash_control_passed(events, control_file, control_token):
        return "read probe has no positive control: Bash cannot read a workspace file"
    return None


def _write_failure(events: list[dict[str, object]], agents_file: str, wrote: bool, controlled: bool) -> str | None:
    """Judge the write probe. It needs a Bash write to `.agents` that fails, and a workspace write that succeeds."""
    if wrote:
        return "write isolation failed: the task model can write to .agents"
    commands = [cast(str, mapping(item["item"])["command"]) for item in _trace(events)]
    if not any(agents_file in command for command in commands):
        return "write probe has no evidence: no Bash command wrote to .agents"
    if not controlled:
        return "write probe has no positive control: Bash cannot write a workspace file"
    return None


def seatbelt_profile(workspace: Path) -> str:
    """Build the macOS `sandbox-exec` profile. Everything is denied unless this profile allows it.

    The profile allows no network. It allows reads of the system runtime and the workspace, and writes
    to the workspace except `.agents`. Later rules win, so the `.agents` deny follows the write allow.
    """
    path = str(workspace)
    rules = ["(version 1)", "(deny default)", "(allow process-fork)", "(allow process-exec)",
             "(allow signal (target self))", "(allow sysctl-read)", "(allow file-read-metadata)",
             *[f'(allow file-read* (subpath "{root}"))' for root in SEATBELT_RUNTIME],
             '(allow file-read* (literal "/dev/null") (literal "/dev/urandom"))',
             f'(allow file-read* (subpath "{path}"))', f'(allow file-write* (subpath "{path}"))',
             f'(deny file-write* (subpath "{path}/.agents"))', '(allow file-write* (literal "/dev/null"))']
    return "\n".join(rules) + "\n"


def sandbox_argv(system: str, tool: str, workspace: Path, argv: list[str], environment: dict[str, str]) -> list[str]:
    """Wrap `argv` for the OS sandbox: `sandbox-exec` on macOS, bubblewrap elsewhere. No network, no host files.

    Under bubblewrap, a shell discards the command's standard error before the command starts. Only
    bubblewrap can then write to the process standard error, so a message that starts with `bwrap:` is
    a setup failure that the command cannot forge.
    """
    assignments = [f"{name}={value}" for name, value in environment.items()]
    if system == "darwin":
        return [tool, "-p", seatbelt_profile(workspace), "/usr/bin/env", "-i", *assignments, *argv]
    path = str(workspace)
    return [tool, "--unshare-all", "--die-with-parent", "--clearenv",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
            "--bind", path, path, "--ro-bind", f"{path}/.agents", f"{path}/.agents", "--chdir", path,
            "/bin/sh", "-c", 'exec "$@" 2>/dev/null', "sh", "/usr/bin/env", "-i", *assignments, *argv]


def _answer(final: dict[str, object]) -> dict[str, object]:
    structured = final.get("structured_output")
    if isinstance(structured, dict):
        return mapping(cast(object, structured))
    text = final.get("result")
    try:
        return mapping(cast(object, json.loads(text))) if isinstance(text, str) else {}
    except (ValueError, RecursionError):
        return {}


@final
class ClaudeCode:
    def __init__(self, model: str, budget: Budget, checkpoint: Callable[[], None], executable: Path | None = None) -> None:
        found = shutil.which("claude") if executable is None else str(executable)
        if found is None:
            raise RuntimeError("Claude Code is unavailable")
        self.executable = Path(found).absolute()
        self.model = string(model, "explicit model")
        self.budget = budget
        self.checkpoint = checkpoint

    def close(self) -> None:
        pass

    def _environment(self, workspace: Path) -> dict[str, str]:
        environment = {"PATH": f"{self.executable.parent}:/usr/bin:/bin", "HOME": str(workspace / "home"),
                       "TMPDIR": str(workspace / "tmp"), "LANG": "C.UTF-8"}
        environment |= {"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1", "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1"}
        return environment | {name: os.environ[name] for name in AUTHENTICATION if name in os.environ}

    def _command(self, settings_path: Path, schema: dict[str, object] | None) -> list[str]:
        command = [str(self.executable), "--restricted", "-p", "--tools", TOOLS, "--strict-mcp-config",
                   "--settings", str(settings_path), "--model", self.model,
                   "--output-format", "stream-json", "--verbose"]
        return command + (["--json-schema", json.dumps(schema)] if schema is not None else [])

    def _run(self, workspace: Path, prompt: str, schema: dict[str, object] | None, timeout: float) -> tuple[int, str, list[dict[str, object]]]:
        _ = shutil.copytree(workspace / ".agents/skills", workspace / ".claude/skills")
        settings_path = workspace.parent / "settings.json"
        _ = settings_path.write_text(json.dumps(settings(workspace)))
        result = process(self._command(settings_path, schema), cwd=workspace, timeout=timeout,
                         environment=self._environment(workspace), input_text=prompt)
        return result.returncode, result.stderr, _events(result.stdout)

    def environment_key(self) -> str:
        """Hash what the live preflight depends on besides the role fingerprint: sandbox settings and process environment."""
        environment = {name: value for name, value in self._environment(Path("/TASK")).items() if name not in AUTHENTICATION}
        return digest({"settings": settings(Path("/TASK")), "environment": environment, "tools": TOOLS,
                       "skill": PROBE_SKILL, "platform": sys.platform})

    def _probe_sandbox(self, workspace: Path, sealed: Path) -> None:
        with listening() as port:
            script = probe(workspace, sealed, Path(__file__).resolve(), port)
            code, output = self.sandbox(workspace, ["/usr/bin/python3", "-c", script])
        if code != 0 or output.strip() != "isolation-ok":
            raise RuntimeError("Claude Code isolation preflight fails: sandbox probe failed; no unsafe fallback")

    def _sealed(self, root: Path) -> tuple[Path, Path, str]:
        workspace = make_workspace(root / "workspace")
        sealed = root / "sealed"
        token = secrets.token_hex(16)
        _ = sealed.write_text(token)
        (workspace / "escape").symlink_to(sealed)
        return workspace, sealed, token

    def check_sandbox(self) -> None:
        """Run the free helper sandbox probe. It makes no model call, so every stage can run it."""
        with tempfile.TemporaryDirectory(prefix="skillz-preflight-") as directory:
            workspace, sealed, _token = self._sealed(Path(directory))
            self._probe_sandbox(workspace, sealed)

    def preflight(self) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="skillz-preflight-") as directory:
            root = Path(directory)
            workspace, sealed, token = self._sealed(root)
            self._probe_sandbox(workspace, sealed)
            skill = workspace / ".agents/skills" / PROBE_SKILL
            skill.mkdir()
            _ = (skill / "SKILL.md").write_text(
                f"---\nname: {PROBE_SKILL}\ndescription: Inspect public fixtures\n---\nInspection probe.\n")
            control = secrets.token_hex(16)
            control_file = workspace / "control.txt"
            _ = control_file.write_text(control)
            agents_file = workspace / ".agents/write-probe"
            write_control = workspace / "write-control.txt"
            write_token = secrets.token_hex(16)
            self.budget.claim()
            self.checkpoint()
            returncode, stderr, events = self._run(
                workspace, f"Run `cat {sealed}` with the Bash tool. Then run `cat {control_file}` with the Bash tool. "
                + f"Then run `printf {write_token} > {agents_file}` with the Bash tool. Then run "
                + f"`printf {write_token} > {write_control}` with the Bash tool. "
                + "Reply with the read outputs, or the word denied for each command that fails.",
                None, min(120, self.budget.remaining()))
            wrote = agents_file.exists()
            controlled = write_control.is_file() and write_control.read_text() == write_token
        reason = _failure(returncode, stderr, events, [PROBE_SKILL])
        if reason is None:
            reason = _read_failure(events, token, str(sealed), control, str(control_file))
        if reason is None:
            reason = _write_failure(events, str(agents_file), wrote, controlled)
        if reason is not None:
            raise RuntimeError(f"Claude Code isolation preflight fails: {reason}; no unsafe fallback")
        return {"adapter": "claude", "model": self.model, "isolation": "passed", "live_calls": 1,
                "environment_key": self.environment_key()}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="skillz-task-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            stage_task(workspace, candidate, case)
            self.budget.claim(holdout=holdout)
            self.checkpoint()
            started = time.monotonic()
            returncode, stderr, events = self._run(workspace, prompt, schema or answer_schema(), self.budget.remaining())
            expected = [] if candidate is None else [candidate.skill]
            final = _final(events)
            failed = bool(returncode) or final is None or final.get("is_error") is True
            loaded = _loaded_skills(events)
            if loaded is None and not failed:
                raise RuntimeError("Claude Code isolation fails: the stream has no init event, so the skill list is unknown; "
                                   + "the invocation counts against the budget")
            if loaded is not None:
                if set(loaded) - set(expected):
                    raise RuntimeError(f"Claude Code isolation fails: skills in init event {loaded}, expected {expected}; "
                                       + "the invocation counts against the budget")
                if candidate is not None and not loaded and not failed:
                    return {"answer": {"result_json": "", "load_marker": ""}, "events": [], "usage": usage([]),
                            "workspace": str(workspace), "latency_seconds": time.monotonic() - started,
                            "output_files": snapshot_outputs(workspace)}
            if failed or final is None:
                reason = _failure(returncode, stderr, events, expected) or "missing-final-response"
                raise RuntimeError(f"Claude Code fails: {reason} (exit {returncode}); the invocation counts against the budget")
            trace = _trace(events) + _token_events(final)
            return {"answer": _answer(final), "events": trace, "usage": usage(trace), "workspace": str(workspace),
                    "latency_seconds": time.monotonic() - started, "output_files": snapshot_outputs(workspace)}

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str]:
        """Run `argv` in the OS sandbox: `sandbox-exec` on macOS, bubblewrap elsewhere. Fail closed without it."""
        system = "darwin" if sys.platform == "darwin" else "linux"
        name = "sandbox-exec" if system == "darwin" else "bwrap"
        tool = shutil.which(name)
        if tool is None:
            raise RuntimeError(f"{name} is unavailable; no unsafe fallback")
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(workspace / "home"),
                       "TMPDIR": str(workspace / "tmp"), "LANG": "C.UTF-8"}
        result = process(sandbox_argv(system, tool, workspace.resolve(), argv, environment), cwd=workspace,
                         timeout=min(20, self.budget.remaining()), environment={"PATH": "/usr/bin:/bin"})
        if result.returncode != 0 and result.stderr.startswith(f"{name}:"):
            raise RuntimeError(f"sandbox setup fails: {result.stderr[:200].strip()}; no unsafe fallback")
        return result.returncode, result.stdout

    def check_candidate(self, candidate: Candidate) -> bool:
        with tempfile.TemporaryDirectory(prefix="skillz-contract-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            rules = resolve(candidate.contract)
            root = workspace / ".agents/skills" / rules.skill
            candidate.materialize(root)
            skill_file = root / "SKILL.md"
            if not skill_file.is_file() or not _declares(skill_file.read_text(), rules.skill):
                return False
            helper = rules.helper
            if helper is None:
                return True
            for fixture in helper.fixtures:
                _ = (workspace / helper.input).write_text(cast(str, fixture["input"]))
                code, stdout = self.sandbox(workspace, [
                    "/usr/bin/python3", "-I", f".agents/skills/{rules.skill}/{helper.path}", helper.input])
                if not fixture_result(fixture, code, stdout):
                    return False
        return True

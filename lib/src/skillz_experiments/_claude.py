from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast, final

from skillz_experiments._candidate import Candidate, make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import Case, CodedError, digest, loads_untrusted, mapping, string
from skillz_experiments._contract import resolve
from skillz_experiments._evaluation import fixture_result, usage
from skillz_experiments._evaluator import answer_schema
from skillz_experiments._isolation import listening, probe, watched
from skillz_experiments._runtime import Budget, process

TOOLS = "Bash,Read,Skill"
PROBE_SKILL = "skillz"
NETWORK_PROBE = "loopback-tcp-http-v2"
# Curl exit codes that show a request attempt. Codes 5 and 6 are name-resolution failures, and 22 needs `-f`.
CURL_ATTEMPTED = frozenset({0, 7, 28, 52, 56, 97})
LISTENER_REACHED = "network isolation failed: the runner-owned listener accepted a connection from the Bash probe"
CONFIG_PREFIX = "skillz-claude-config-"
CREDENTIALS = ".credentials.json"
SANDBOX_HELPERS = ("socat",)
# Agents that Claude Code lists without any user file. The `--tools` list leaves no way to run them.
BUILTIN_AGENTS = frozenset({"general-purpose", "Explore", "Plan", "statusline-setup", "output-style-setup",
                            "claude-code-guide"})
LOGIN_HINT = "run `claude` once and log in"
INSTALL_BUBBLEWRAP = "install bubblewrap (for example `sudo apt install bubblewrap`)"
INSTALL_SOCAT = "install socat (for example `sudo apt install socat`)"
USERNS_FIX = ("allow unprivileged user namespaces with `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`, "
              "or add an AppArmor profile for bwrap")


PERMISSION_DENY_ROOTS = ("/home", "/root", "/Users", "/mnt", "/media", "/opt", "/srv", "/workspaces", "/data")
RUNTIME_READ = ("/usr", "/bin", "/lib", "/lib32", "/lib64", "/libx32", "/proc/self", "/etc/ld.so.cache", "/etc/ld.so.conf",
                "/etc/ld.so.conf.d", "/etc/alternatives", "/etc/localtime", "/etc/passwd", "/etc/group", "/etc/nsswitch.conf",
                "/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")
MACOS_READ = ("/System", "/Library")
SEATBELT_RUNTIME = ("/usr", "/bin", "/System", "/Library/Developer/CommandLineTools", "/private/var/db/dyld")


class NetworkIsolationFailed(CodedError):
    """The live Bash path reaches the network, or the network probe gives no usable result."""

    def __init__(self, reason: str) -> None:
        super().__init__("network-isolation-failed", f"Claude Code isolation preflight fails: {reason}; no unsafe fallback")


class SandboxUnavailable(CodedError):
    """The Bash sandbox cannot start. The message names the cause and the fix. The run never falls back."""

    def __init__(self, cause: str, fix: str) -> None:
        super().__init__("sandbox-unavailable", f"Claude Code sandbox unavailable: {cause}; no unsafe fallback; fix: {fix}")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _sandbox_fix(detail: str) -> str:
    """Pick the fix that matches the sandbox failure text."""
    text = detail.lower()
    if "socat" in text:
        return INSTALL_SOCAT
    if any(word in text for word in ("uid map", "operation not permitted", "permission denied", "namespace", "apparmor")):
        return USERNS_FIX
    if "bubblewrap" in text or "bwrap" in text:
        return INSTALL_BUBBLEWRAP
    return f"{INSTALL_BUBBLEWRAP}; {INSTALL_SOCAT}; {USERNS_FIX}"


def _host_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser().absolute() if configured else Path.home() / ".claude"


@dataclass(frozen=True)
class _Credential:
    """The identity of the linked login file: its resolved path and its device and inode numbers."""
    path: Path
    device: int
    inode: int

    @classmethod
    def find(cls) -> _Credential:
        source = _host_config_dir() / CREDENTIALS
        try:
            target = source.resolve(strict=True)
            info = target.stat()
        except OSError:
            raise CodedError("login-missing", f"no Claude login at {source}; {LOGIN_HINT}") from None
        if not target.is_file():
            raise CodedError("login-missing", f"no Claude login at {source}; {LOGIN_HINT}")
        return cls(target, info.st_dev, info.st_ino)

    def holds(self, link: Path) -> bool:
        """Check that `link` is still a symlink to this file, with the same device and inode."""
        try:
            return link.is_symlink() and Path(os.readlink(link)) == self.path and (
                (info := link.stat()).st_dev, info.st_ino) == (self.device, self.inode)
        except OSError:
            return False


def settings(workspace: Path, out: Path | None = None) -> dict[str, object]:
    """Return the sandbox floor. A missing sandbox stops the run, and no command leaves the sandbox.

    Sandboxed commands cannot read the host from `/`. The narrower allow wins, so they read only the
    workspace and the runtime roots. The Read tool follows permission rules, not the sandbox, so a deny
    rule covers the host roots, `/proc`, and the run directory `out` for that tool.
    """
    runtime = [*RUNTIME_READ, *(MACOS_READ if sys.platform == "darwin" else ())]
    temporary = _unique([tempfile.gettempdir(), str(Path(tempfile.gettempdir()).resolve())])
    hidden = ["/proc", *([] if out is None else [str(out), str(out.resolve())])]
    return {"sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                        "network": {"allowedDomains": [], "strictAllowlist": True},
                        "filesystem": {"denyRead": ["/"],
                                       "denyWrite": _unique([f"{workspace}/.agents", f"{workspace.resolve()}/.agents"]),
                                       "allowRead": _unique([str(workspace), str(workspace.resolve()), *runtime])}},
            "disableBundledSkills": True, "disableAllHooks": True,
            "permissions": {"allow": ["Skill"],
                            "deny": [*[f"Read(/{root}/**)" for root in _unique([*PERMISSION_DENY_ROOTS, str(Path.home()), *hidden])],
                                     *[f"Read(/{root}/{CONFIG_PREFIX}*/**)" for root in temporary]]}}


def _events(stdout: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            events.append(mapping(loads_untrusted(line)))
        except ValueError:
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
        items = cast(list[object], raw) if isinstance(raw, list) else None
        names = [cast(dict[str, object], item).get("name") if isinstance(item, dict) else item for item in items or []]
        if items is None or not all(isinstance(name, str) for name in names):
            raise CodedError("isolation-failed", "Claude Code isolation fails: the init event skills field is not a list of names")
        loaded.update(cast(list[str], names))
    return sorted(loaded)


def _entries(event: dict[str, object], field: str) -> list[str]:
    raw = event.get(field)
    names: list[str] = []
    for item in cast(list[object], raw) if isinstance(raw, list) else []:
        names.append(str(mapping(cast(object, item)).get("name", "")) if isinstance(item, dict) else str(item))
    return names


def _leak(events: list[dict[str, object]], skills: list[str]) -> str | None:
    """Name the entry in an init event that the transport does not configure, or return None.

    The transport configures only the candidate skill. It loads no plugin and no MCP server.
    """
    for event in (event for event in events if event.get("type") == "system" and event.get("subtype") == "init"):
        found = {"skill": [name for name in _entries(event, "skills") if name not in skills],
                 "plugin": _entries(event, "plugins"),
                 "agent": [name for name in _entries(event, "agents") if name not in BUILTIN_AGENTS],
                 "MCP server": _entries(event, "mcp_servers")}
        for kind, names in found.items():
            if names:
                return f"foreign {kind} in init event: {sorted(names)}"
    return None


def _failure(returncode: int, stderr: str, events: list[dict[str, object]], skills: list[str]) -> str | None:
    """Name the isolation failure that a probe run shows, or return None when the run is isolated.

    Raise SandboxUnavailable when the Bash sandbox cannot start.
    """
    final = _final(events)
    errored = returncode != 0 or (final is not None and final.get("is_error") is True)
    detail = (stderr[:8192] + " " + (str(final.get("result", ""))[:2048] if final else "")).lower()
    if returncode != 0 and "sandbox" in detail:
        raise SandboxUnavailable(f"the Bash sandbox cannot start ({' '.join(stderr[:200].split())})", _sandbox_fix(detail))
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

    def count(name: str, *, optional: bool = False) -> int | None:
        if optional and name not in counts:
            return 0
        value = counts.get(name)
        return value if type(value) is int and value >= 0 else None

    input_tokens = count("input_tokens")
    created = count("cache_creation_input_tokens", optional=True)
    cached = count("cache_read_input_tokens", optional=True)
    total = input_tokens + created + cached if (input_tokens is not None and created is not None
                                                 and cached is not None) else None
    return [{"type": "turn.completed", "usage": {
        "input_tokens": total, "cached_input_tokens": cached, "output_tokens": count("output_tokens")}}]


def _declares(text: str, skill: str) -> bool:
    """Check that the SKILL.md frontmatter names the contract skill, has a description, and declares no hooks."""
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return False
    fields: dict[str, str] = {}
    for line in lines[1:lines.index("---", 1)]:
        if not line or line[0].isspace():
            continue
        key, _, value = line.partition(":")
        key = key.strip().removeprefix("? ").strip().strip("\"'").strip().lower()
        fields[key] = value.strip().strip("\"'")
    return (fields.get("name") == skill and bool(fields.get("description"))
            and "hooks" not in fields and "user-invocable" not in fields)


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
    attempts = [mapping(item["item"]) for item in _trace(events)]
    if not any(cast(str, item["command"]).startswith("printf") and agents_file in cast(str, item["command"])
               and item["exit_code"] == 1 for item in attempts):
        return "write probe has no evidence: no failed Bash printf to .agents"
    if not controlled:
        return "write probe has no positive control: Bash cannot write a workspace file"
    return None


def network_commands(port: int, token: str) -> list[str]:
    """Build the live Bash network attempts. Each command must run word for word.

    The first command is a direct loopback TCP attempt. It sends the token if it connects, and prints `denied-<token>`
    if not. The other two send an HTTP request through any configured proxy. `--noproxy ''` clears `NO_PROXY`, so
    curl uses the sandbox proxy for loopback names too. They print `exit-<code>-<token>`. The listener judges the token.
    """
    curl = "/usr/bin/curl --noproxy '' --max-time 5 -sS -o /dev/null"
    return ["/usr/bin/python3 -c \"import socket;c=socket.socket();c.settimeout(5);"
            + f"r=c.connect_ex(('127.0.0.1',{port}));r or c.send(b'{token}');"
            + f"print(('open-' if r==0 else 'denied-')+'{token}')\"",
            *[f"{curl} http://{host}:{port}/{token}; echo exit-$?-{token}" for host in ("127.0.0.1", "localhost")]]


def _results(events: list[dict[str, object]], command: str) -> list[str]:
    """Return the Bash results that answer a tool call whose command equals `command`."""
    uses: set[str] = set()
    results: list[str] = []
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if isinstance(message, dict) else None
        for block in cast(list[object], content) if isinstance(content, list) else []:
            item = mapping(block)
            if item.get("type") == "tool_use" and item.get("name") == "Bash" and isinstance(item.get("id"), str):
                if mapping(item.get("input", {})).get("command") == command:
                    uses.add(cast(str, item["id"]))
            elif item.get("type") == "tool_result" and item.get("tool_use_id") in uses:
                results.append(json.dumps(item))
    return results


def _network_failure(events: list[dict[str, object]], port: int, token: str) -> str | None:
    """Judge the network probe. Each exact command needs output that shows it ran."""
    direct, *proxied = network_commands(port, token)
    outputs = _results(events, direct)
    if not any(f"denied-{token}" in text for text in outputs):
        return f"network probe has no evidence: the direct TCP command output is missing or malformed (output: {outputs!r:.200})"
    for command in proxied:
        codes = [int(code) for text in _results(events, command)
                 for code in cast(list[str], re.findall(rf"exit-(\d+)-{token}", text))]
        if not codes or any(code not in CURL_ATTEMPTED for code in codes):
            return f"network probe has no evidence: the HTTP command output is missing, or curl did not attempt a request (exit codes: {codes})"
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
        return mapping(loads_untrusted(text)) if isinstance(text, str) else {}
    except ValueError:
        return {}


@final
class ClaudeCode:
    def __init__(self, model: str, budget: Budget, checkpoint: Callable[[], None], executable: Path | None = None,
                 out: Path | None = None) -> None:
        self.out = out
        found = shutil.which("claude") if executable is None else str(executable)
        if found is None:
            raise RuntimeError("Claude Code is unavailable")
        self.executable = Path(found).absolute()
        self.model = string(model, "explicit model")
        self.budget = budget
        self.checkpoint = checkpoint
        self.credential: _Credential | None = None
        self._temporary: Path | None = None
        if sys.platform == "darwin":
            self.config_dir = _host_config_dir()
            return
        self.credential = _Credential.find()
        self.config_dir = self._temporary = Path(tempfile.mkdtemp(prefix=CONFIG_PREFIX))
        try:
            (self.config_dir / CREDENTIALS).symlink_to(self.credential.path)
        except OSError:
            shutil.rmtree(self.config_dir, ignore_errors=True)
            raise

    def close(self) -> None:
        """Delete the temporary config directory. Then stop when the credential link no longer holds the same file."""
        directory, self._temporary = self._temporary, None
        if directory is None or self.credential is None:
            return
        changed = not self.credential.holds(directory / CREDENTIALS)
        shutil.rmtree(directory, ignore_errors=True)
        if changed:
            raise CodedError("credential-changed", f"the Claude login file {self.credential.path} changed during the run; "
                             + "Claude Code replaced or moved it. Log in again with `claude`, then start a new run")

    def _environment(self, workspace: Path) -> dict[str, str]:
        environment = {"PATH": f"{self.executable.parent}:/usr/bin:/bin", "HOME": str(workspace / "home"),
                       "TMPDIR": str(workspace / "tmp"), "LANG": "C.UTF-8"}
        environment |= {"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1", "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1"}
        return environment | {"CLAUDE_CONFIG_DIR": str(self.config_dir)}

    def _command(self, settings_path: Path, schema: dict[str, object] | None) -> list[str]:
        command = [str(self.executable), "--restricted", "-p", "--tools", TOOLS, "--strict-mcp-config",
                   "--settings", str(settings_path), "--model", self.model,
                   "--output-format", "stream-json", "--verbose"]
        return command + (["--json-schema", json.dumps(schema)] if schema is not None else [])

    def _run(self, workspace: Path, prompt: str, schema: dict[str, object] | None, timeout: float) -> tuple[int, str, list[dict[str, object]]]:
        _ = shutil.copytree(workspace / ".agents/skills", workspace / ".claude/skills")
        settings_path = workspace.parent / "settings.json"
        _ = settings_path.write_text(json.dumps(settings(workspace, self.out)))
        result = process(self._command(settings_path, schema), cwd=workspace, timeout=timeout,
                         environment=self._environment(workspace), input_text=prompt)
        return result.returncode, result.stderr, _events(result.stdout)

    def environment_key(self) -> str:
        """Hash what the live preflight depends on besides the role fingerprint: sandbox settings and process environment.

        The key leaves out the per-process config directory, so it stays stable across processes.
        """
        environment = {name: value for name, value in self._environment(Path("/TASK")).items() if name != "CLAUDE_CONFIG_DIR"}
        return digest({"settings": settings(Path("/TASK"), self.out), "environment": environment, "tools": TOOLS,
                       "skill": PROBE_SKILL, "platform": sys.platform, "network_probe": NETWORK_PROBE})

    def _probe_sandbox(self, workspace: Path, sealed: Path) -> None:
        if sys.platform != "darwin":
            for helper in SANDBOX_HELPERS:
                if shutil.which(helper) is None:
                    raise SandboxUnavailable(f"{helper} is missing, and the Claude Code sandbox needs it", INSTALL_SOCAT)
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
            network_token = secrets.token_hex(16)
            self.budget.claim()
            self.checkpoint()
            with watched() as (port, connected):
                try:
                    returncode, stderr, events = self._run(
                        workspace, f"Run `cat {sealed}` with the Bash tool. Then run `cat {control_file}` with the Bash tool. "
                        + f"Then run `printf {write_token} > {agents_file}` with the Bash tool. Then run "
                        + f"`printf {write_token} > {write_control}` with the Bash tool. "
                        + "".join(f"Then run `{command}` with the Bash tool. " for command in network_commands(port, network_token))
                        + "Reply with the read outputs, or the word denied for each command that fails.",
                        None, min(120, self.budget.remaining()))
                except Exception:
                    if connected(network_token):
                        raise NetworkIsolationFailed(LISTENER_REACHED) from None
                    raise
                reached = connected(network_token)
            wrote = agents_file.exists()
            controlled = write_control.is_file() and write_control.read_text() == write_token
        if reached:
            raise NetworkIsolationFailed(LISTENER_REACHED)
        if sys.platform == "darwin":
            leak = _leak(events, [PROBE_SKILL])
            if leak is not None:
                raise CodedError("preflight-leak", f"Claude Code isolation preflight fails: {leak}; the real config "
                                 + "directory exposes user entries; no unsafe fallback")
        reason = _failure(returncode, stderr, events, [PROBE_SKILL])
        network = _network_failure(events, port, network_token)
        if reason is None:
            reason = _read_failure(events, token, str(sealed), control, str(control_file))
        if reason is None:
            reason = _write_failure(events, str(agents_file), wrote, controlled)
        if reason is None and network is not None:
            raise NetworkIsolationFailed(network)
        if reason is not None:
            raise RuntimeError(f"Claude Code isolation preflight fails: {reason}; no unsafe fallback")
        return {"adapter": "claude", "model": self.model, "isolation": "passed", "network": "denied", "live_calls": 1,
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
                raise CodedError("isolation-failed", "Claude Code isolation fails: the stream has no init event, "
                                 + "so the skill list is unknown; the invocation counts against the budget")
            if loaded is not None:
                if set(loaded) - set(expected):
                    raise CodedError("isolation-failed", f"Claude Code isolation fails: skills in init event {loaded}, "
                                     + f"expected {expected}; the invocation counts against the budget")
                if candidate is not None and not loaded and not failed:
                    trace = _trace(events) + _token_events(cast(dict[str, object], final))
                    return {"answer": {"result_json": "", "load_marker": ""}, "events": [], "usage": usage(trace),
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
            raise SandboxUnavailable(f"{name} is unavailable", INSTALL_BUBBLEWRAP if system == "linux"
                                     else "run on a macOS host that provides `sandbox-exec`")
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(workspace / "home"),
                       "TMPDIR": str(workspace / "tmp"), "LANG": "C.UTF-8"}
        result = process(sandbox_argv(system, tool, workspace.resolve(), argv, environment), cwd=workspace,
                         timeout=min(20, self.budget.remaining()), environment={"PATH": "/usr/bin:/bin"})
        if result.returncode != 0 and result.stderr.startswith(f"{name}:"):
            raise SandboxUnavailable(f"sandbox setup fails: {result.stderr[:200].strip()}", _sandbox_fix(result.stderr))
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
                (workspace / helper.input).parent.mkdir(parents=True, exist_ok=True)
                _ = (workspace / helper.input).write_text(cast(str, fixture["input"]))
                code, stdout = self.sandbox(workspace, [
                    "/usr/bin/python3", "-I", f".agents/skills/{rules.skill}/{helper.path}", helper.input])
                if not fixture_result(fixture, code, stdout):
                    return False
        return True

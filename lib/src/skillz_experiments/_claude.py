from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

from skillz_experiments._candidate import Candidate, make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import Case, mapping, string
from skillz_experiments._contract import resolve
from skillz_experiments._evaluation import fixture_result, usage
from skillz_experiments._evaluator import answer_schema
from skillz_experiments._runtime import Budget, process

TOOLS = "Bash,Read,Skill"
PROBE_SKILL = "skillz"
AUTHENTICATION = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")


def settings() -> dict[str, object]:
    """Return the sandbox floor. A missing sandbox stops the run, and no command leaves the sandbox."""
    return {"sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                        "network": {"allowedDomains": [], "strictAllowlist": True}},
            "permissions": {"allow": ["Skill"]}}


def _events(stdout: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            events.append(mapping(cast(object, json.loads(line))))
        except ValueError:
            raise RuntimeError("invalid Claude Code JSON event stream") from None
    return events


def _final(events: list[dict[str, object]]) -> dict[str, object] | None:
    return next((event for event in reversed(events) if event.get("type") == "result"), None)


def _loaded_skills(events: list[dict[str, object]]) -> list[str] | None:
    """Return the skill names in the init event, or None when the stream has no init event."""
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            raw = event.get("skills")
            items = cast(list[object], raw) if isinstance(raw, list) else []
            names = [cast(dict[str, object], item).get("name") if isinstance(item, dict) else item for item in items]
            return sorted(name for name in names if isinstance(name, str))
    return None


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
    if loaded != skills:
        return f"foreign skill in init event: {loaded}"
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
    """Check that the SKILL.md frontmatter names the contract skill."""
    parts = text.split("---", 2)
    return text.startswith("---") and len(parts) == 3 and f"name: {skill}" in (line.strip() for line in parts[1].splitlines())


def _answer(final: dict[str, object]) -> dict[str, object]:
    structured = final.get("structured_output")
    if isinstance(structured, dict):
        return mapping(cast(object, structured))
    text = final.get("result")
    try:
        return mapping(cast(object, json.loads(text))) if isinstance(text, str) else {}
    except ValueError:
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
        return environment | {name: os.environ[name] for name in AUTHENTICATION if name in os.environ}

    def _command(self, settings_path: Path, schema: dict[str, object] | None) -> list[str]:
        command = [str(self.executable), "--restricted", "-p", "--tools", TOOLS, "--strict-mcp-config",
                   "--settings", str(settings_path), "--model", self.model,
                   "--output-format", "stream-json", "--verbose"]
        return command + (["--json-schema", json.dumps(schema)] if schema is not None else [])

    def _run(self, workspace: Path, prompt: str, schema: dict[str, object] | None, timeout: float) -> tuple[int, str, list[dict[str, object]]]:
        _ = shutil.copytree(workspace / ".agents/skills", workspace / ".claude/skills")
        settings_path = workspace.parent / "settings.json"
        _ = settings_path.write_text(json.dumps(settings()))
        result = process(self._command(settings_path, schema), cwd=workspace, timeout=timeout,
                         environment=self._environment(workspace), input_text=prompt)
        return result.returncode, result.stderr, _events(result.stdout)

    def preflight(self) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="skillz-preflight-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            skill = workspace / ".agents/skills" / PROBE_SKILL
            skill.mkdir()
            _ = (skill / "SKILL.md").write_text(
                f"---\nname: {PROBE_SKILL}\ndescription: Inspect public fixtures\n---\nInspection probe.\n")
            returncode, stderr, events = self._run(workspace, "Reply with the word ok.", None,
                                                   min(120, self.budget.remaining()))
        reason = _failure(returncode, stderr, events, [PROBE_SKILL])
        if reason is not None:
            raise RuntimeError(f"Claude Code isolation preflight fails: {reason}; no unsafe fallback")
        return {"adapter": "claude", "model": self.model, "isolation": "passed", "live_calls": 1}

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
            loaded = _loaded_skills(events)
            if candidate is not None and loaded != expected:
                raise RuntimeError(f"Claude Code isolation fails: skills in init event {loaded}, expected {expected}; "
                                   + "the invocation counts against the budget")
            final = _final(events)
            if returncode or final is None or final.get("is_error") is True:
                reason = _failure(returncode, stderr, events, expected) or "missing-final-response"
                raise RuntimeError(f"Claude Code fails: {reason} (exit {returncode}); the invocation counts against the budget")
            trace = _trace(events) + _token_events(final)
            return {"answer": _answer(final), "events": trace, "usage": usage(trace), "workspace": str(workspace),
                    "latency_seconds": time.monotonic() - started, "output_files": snapshot_outputs(workspace)}

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str]:
        """Run `argv` in bubblewrap, the mechanism that Claude Code uses on Linux. No network, no host files."""
        bubblewrap = shutil.which("bwrap")
        if bubblewrap is None:
            raise RuntimeError("bubblewrap is unavailable; no unsafe fallback")
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(workspace / "home"),
                       "TMPDIR": str(workspace / "tmp"), "LANG": "C.UTF-8"}
        command = [bubblewrap, "--unshare-all", "--die-with-parent", "--clearenv",
                   "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
                   "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
                   "--bind", str(workspace), str(workspace), "--chdir", str(workspace),
                   *[part for name, value in environment.items() for part in ("--setenv", name, value)], *argv]
        result = process(command, cwd=workspace, timeout=min(20, self.budget.remaining()),
                         environment={"PATH": "/usr/bin:/bin"})
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

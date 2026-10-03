from __future__ import annotations

import json
import shutil
import stat
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate, make_workspace
from skillz_experiments._graders import Sandbox
from skillz_experiments._harness import Configuration
from skillz_experiments._runtime import Budget

FAKE = Path(__file__).parent / "fixtures/fake_claude.py"


def fake_claude(tmp_path: Path, mode: str = "ok") -> Path:
    executable = tmp_path / "bin" / "claude"
    executable.parent.mkdir()
    _ = shutil.copyfile(FAKE, executable)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    _ = executable.with_name("claude.mode").write_text(mode)
    return executable


def harness(tmp_path: Path, executable: Path):  # noqa: ANN201
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(executable)]}))
    return Configuration.load(config, "claude-test-model").create("claude-test-model", Budget(10, 120, 0), lambda: None)


def calls(executable: Path) -> list[dict[str, object]]:
    log = executable.with_name("claude.log")
    return [cast(dict[str, object], json.loads(line)) for line in log.read_text().splitlines()]


def test_argv_or_settings_or_usage_argv_is_restricted_and_tool_limited(tmp_path: Path) -> None:
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        _ = session.transports["task"].invoke("hello")
    finally:
        session.close()
    argv = cast(list[str], calls(executable)[0]["argv"])
    assert argv[:2] == ["--restricted", "-p"]
    assert argv[argv.index("--tools") + 1] == "Bash,Read,Skill"
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--model") + 1] == "claude-test-model"
    assert "--settings" in argv


def test_argv_or_settings_or_usage_settings_hold_the_sandbox_floor(tmp_path: Path) -> None:
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        _ = session.transports["task"].invoke("hello")
    finally:
        session.close()
    settings = cast(dict[str, dict[str, object]], json.loads(cast(str, calls(executable)[0]["settings"])))
    assert settings["sandbox"] == {
        "enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
        "network": {"allowedDomains": [], "strictAllowlist": True}}


def test_argv_or_settings_or_usage_usage_comes_from_the_json_result(tmp_path: Path) -> None:
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        result = session.transports["task"].invoke("hello")
    finally:
        session.close()
    assert result["usage"] == {"input_tokens": 35, "cached_input_tokens": 20, "output_tokens": 7}
    assert result["answer"] == {"result_json": "{}", "load_marker": ""}


@pytest.mark.parametrize(("mode", "reason"), [
    ("auth-fail", "authentication"),
    ("foreign-skill", "foreign skill"),
    ("sandbox-unavailable", "sandbox"),
])
def test_preflight_stops_on_isolation_failure_without_fallback(tmp_path: Path, mode: str, reason: str) -> None:
    executable = fake_claude(tmp_path, mode)
    session = harness(tmp_path, executable)
    try:
        with pytest.raises(RuntimeError, match="isolation") as caught:
            _ = session.preflight()
    finally:
        session.close()
    assert reason in str(caught.value)
    assert "no unsafe fallback" in str(caught.value)
    logged = calls(executable)
    assert len(logged) == 1
    assert all("--restricted" in cast(list[str], call["argv"]) for call in logged)


def test_preflight_passes_when_only_the_probe_skill_loads(tmp_path: Path) -> None:
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        evidence = session.preflight()
    finally:
        session.close()
    roles = cast(dict[str, dict[str, object]], evidence["roles"])
    assert roles["task"]["isolation"] == "passed"
    assert roles["task"]["live_calls"] == 1


def test_invoke_with_a_candidate_stops_when_a_foreign_skill_loads(tmp_path: Path) -> None:
    session = harness(tmp_path, fake_claude(tmp_path, "foreign-skill"))
    candidate = Candidate({"SKILL.md": "---\nname: skillz\ndescription: x\n---\n"}, ("SKILL.md",))
    try:
        with pytest.raises(RuntimeError, match="isolation"):
            _ = session.transports["task"].invoke("hello", candidate)
    finally:
        session.close()


def test_candidate_run_records_bash_commands_as_completed_executions(tmp_path: Path) -> None:
    session = harness(tmp_path, fake_claude(tmp_path))
    candidate = Candidate({"SKILL.md": "---\nname: skillz\ndescription: x\n---\n"}, ("SKILL.md",))
    try:
        result = session.transports["task"].invoke("hello", candidate)
    finally:
        session.close()
    events = cast(list[dict[str, object]], result["events"])
    assert events[0]["item"] == {"type": "command_execution", "command": "cat .agents/skills/skillz/SKILL.md", "exit_code": 0}
    assert cast(dict[str, object], result["answer"])["load_marker"] == candidate.identity


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is unavailable")
def test_sandbox_hides_host_files_and_returns_output(tmp_path: Path) -> None:
    session = harness(tmp_path, fake_claude(tmp_path))
    sealed = tmp_path / "sealed"
    _ = sealed.write_text("secret")
    workspace = make_workspace(tmp_path / "workspace")
    try:
        transport = cast(Sandbox, cast(object, session.transports["task"]))
        code, stdout = transport.sandbox(workspace, ["/usr/bin/python3", "-c", "print('inside')"])
        denied, _ = transport.sandbox(workspace, ["/usr/bin/python3", "-c", f"open({str(sealed)!r}).read()"])
    finally:
        session.close()
    assert (code, stdout.strip()) == (0, "inside")
    assert denied != 0

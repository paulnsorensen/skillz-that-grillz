"""Press attacks on the Claude transport, preflight, and once-per-run reuse (AC-10, AC-11). Fake `claude` only."""
from __future__ import annotations

import json
import shutil
import stat
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments import _claude
from skillz_experiments._candidate import Candidate
from skillz_experiments._claude import ClaudeCode
from skillz_experiments._harness import Configuration, Harness
from skillz_experiments._runtime import Budget

FAKE = Path(__file__).parent / "fixtures/fake_claude.py"
SKILL = "---\nname: skillz\ndescription: x\n---\n"
SCRIPTED = '''#!/usr/bin/env python3
import json, sys
from pathlib import Path
here = Path(__file__).resolve()
argv = sys.argv[1:]
prompt = sys.stdin.read()
with here.with_name("claude.log").open("a") as log:
    log.write(json.dumps({"argv": argv, "prompt": prompt}) + "\\n")
sys.stdout.write(here.with_name("claude.out").read_text())
sys.exit(int(here.with_name("claude.code").read_text()))
'''
INIT_OK = {"type": "system", "subtype": "init", "skills": ["skillz"]}
DONE = {"type": "result", "subtype": "success", "is_error": False, "result": "done", "usage": {"output_tokens": 1},
        "structured_output": {"result_json": "{}", "load_marker": "m"}}


def fake_claude(tmp_path: Path, mode: str = "ok") -> Path:
    executable = tmp_path / "bin" / "claude"
    executable.parent.mkdir(exist_ok=True)
    _ = shutil.copyfile(FAKE, executable)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    _ = executable.with_name("claude.mode").write_text(mode)
    return executable


def scripted(tmp_path: Path, lines: Sequence[object], code: int = 0) -> Path:
    executable = tmp_path / "bin" / "claude"
    executable.parent.mkdir(exist_ok=True)
    _ = executable.write_text(SCRIPTED)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    _ = executable.with_name("claude.out").write_text("".join(
        (line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines))
    _ = executable.with_name("claude.code").write_text(str(code))
    return executable


def harness(tmp_path: Path, executable: Path, budget: Budget | None = None, model: str = "claude-test-model") -> Harness:
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(executable)]}))
    return Configuration.load(config, model).create(model, budget or Budget(10, 120, 0), lambda: None)


def calls(executable: Path) -> list[dict[str, object]]:
    log = executable.with_name("claude.log")
    if not log.exists():
        return []
    return [cast(dict[str, object], json.loads(line)) for line in log.read_text().splitlines()]


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Replace the OS sandbox probe with a recorder that passes, so live-call steps run on any host."""
    seen: list[list[str]] = []

    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str]) -> tuple[int, str]:
        del self, workspace
        seen.append(argv)
        return 0, "isolation-ok\n"
    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)
    return seen


def candidate() -> Candidate:
    return Candidate({"SKILL.md": SKILL}, ("SKILL.md",))


# ---- the sandbox floor on every call (AC-10) ------------------------------------------------------

FLOOR: dict[str, object] = {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
         "network": {"allowedDomains": [], "strictAllowlist": True}}


def assert_floor(call: dict[str, object]) -> None:
    argv = cast(list[str], call["argv"])
    assert argv[:2] == ["--restricted", "-p"]
    assert argv[argv.index("--tools") + 1] == "Bash,Read,Skill"
    assert "--strict-mcp-config" in argv
    settings = cast(dict[str, dict[str, object]], json.loads(cast(str, call["settings"])))
    assert {key: settings["sandbox"][key] for key in FLOOR} == FLOOR
    assert cast(dict[str, dict[str, object]], settings["sandbox"])["filesystem"]["denyRead"] == ["/"]


@pytest.mark.parametrize("schema", [None, {"type": "object"}])
@pytest.mark.parametrize("with_candidate", [True, False])
def test_every_invoke_keeps_the_sandbox_floor_whatever_the_schema_or_candidate(
        tmp_path: Path, schema: dict[str, object] | None, with_candidate: bool) -> None:
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        _ = session.transports["task"].invoke("hello", candidate() if with_candidate else None, schema=schema)
    finally:
        session.close()
    logged = calls(executable)
    assert len(logged) == 1
    assert_floor(logged[0])


def test_every_role_and_every_preflight_call_keeps_the_sandbox_floor(tmp_path: Path, probes: list[list[str]]) -> None:
    del probes
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        _ = session.preflight()
        for name in ("judge", "reflection"):
            _ = session.transports[name].invoke("hello")
    finally:
        session.close()
    logged = calls(executable)
    assert len(logged) == 5
    for call in logged:
        assert_floor(call)


def test_floor_settings_are_unaffected_by_a_hostile_home_directory(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/")
    settings = _claude.settings(tmp_path)
    deny = cast(dict[str, list[str]], settings["permissions"])["deny"]
    assert "Read(///**)" in deny or any("/**" in rule for rule in deny)
    assert cast(dict[str, object], settings["sandbox"])["allowUnsandboxedCommands"] is False
    allowed = cast(dict[str, dict[str, list[str]]], settings["sandbox"])["filesystem"]["allowRead"]
    assert "/" not in allowed


def test_the_claude_child_environment_holds_only_the_allowed_names(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HOST_SECRET", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "ANTHROPIC_BASE_URL",
                 "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, "leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        _ = session.transports["task"].invoke("hello")
    finally:
        session.close()
    environment = cast(dict[str, str], calls(executable)[0]["environment"])
    assert set(environment) - {"LC_CTYPE"} <= {"PATH", "HOME", "TMPDIR", "LANG", "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",
                                               "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS", "ANTHROPIC_API_KEY"}
    assert environment["HOME"] != str(Path.home())


def test_claude_role_rejects_a_shell_string_a_second_argument_and_an_unknown_field(tmp_path: Path) -> None:
    executable = fake_claude(tmp_path)
    documents: list[dict[str, object]] = [
        {"command": f"{executable} --dangerously-skip-permissions"},
        {"command": [str(executable), "--dangerously-skip-permissions"]},
        {"command": []}, {"identity": "x"}, {"extra": 1}]
    for document in documents:
        config = tmp_path / "h.json"
        _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude"} | document))
        with pytest.raises(ValueError):
            _ = Configuration.load(config, "m").create("m", Budget(10, 120, 0), lambda: None)


def test_claude_role_with_a_blank_model_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "h.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "model": "  ",
                                      "command": [str(fake_claude(tmp_path))]}))
    with pytest.raises(ValueError):
        _ = Configuration.load(config, "m")


# ---- preflight stops on every isolation failure, with no fallback (AC-11) -------------------------

@pytest.mark.parametrize("mode", ["auth-fail", "foreign-skill", "sandbox-unavailable", "no-init-auth", "no-init",
                                  "missing-skill", "read-host", "bash-broken", "read-fallback", "skips-cat",
                                  "write-agents", "write-broken"])
def test_preflight_stops_on_every_isolation_failure_with_one_restricted_call_and_no_retry(
        tmp_path: Path, probes: list[list[str]], mode: str) -> None:
    del probes
    executable = fake_claude(tmp_path, mode)
    session = harness(tmp_path, executable)
    try:
        with pytest.raises(RuntimeError, match="no unsafe fallback"):
            _ = session.preflight()
    finally:
        session.close()
    logged = calls(executable)
    assert len(logged) == 1 and "--restricted" in cast(list[str], logged[0]["argv"])
    assert not any("--dangerously-skip-permissions" in cast(list[str], call["argv"]) for call in logged)


@pytest.mark.parametrize(("mode", "reason"), [
    ("write-agents", "write isolation failed"),
    ("write-broken", "no positive control")])
def test_preflight_stops_when_the_agents_write_succeeds_or_the_write_control_fails(
        tmp_path: Path, probes: list[list[str]], mode: str, reason: str) -> None:
    del probes
    session = harness(tmp_path, fake_claude(tmp_path, mode))
    try:
        with pytest.raises(RuntimeError, match=reason):
            _ = session.preflight()
    finally:
        session.close()


def test_settings_deny_writes_into_the_workspace_agents_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = cast(dict[str, dict[str, dict[str, list[str]]]], _claude.settings(workspace))["sandbox"]["filesystem"]
    assert f"{workspace}/.agents" in sandbox["denyWrite"]
    assert f"{workspace.resolve()}/.agents" in sandbox["denyWrite"]


def nested(depth: int = 200_000) -> str:
    return "[" * depth


def test_invoke_event_stream_with_deeply_nested_json_is_an_invalid_stream_not_a_recursion_error(tmp_path: Path) -> None:
    session = harness(tmp_path, scripted(tmp_path, [INIT_OK, nested(), DONE]))
    try:
        with pytest.raises(RuntimeError, match="invalid Claude Code JSON event stream"):
            _ = session.transports["task"].invoke("hello", candidate())
    finally:
        session.close()


def test_invoke_answer_is_empty_when_the_result_text_is_deeply_nested_json(tmp_path: Path) -> None:
    session = harness(tmp_path, scripted(tmp_path, [INIT_OK, DONE | {"structured_output": None, "result": nested()}]))
    try:
        result = session.transports["task"].invoke("hello", candidate())
    finally:
        session.close()
    assert result["answer"] == {}


@pytest.mark.parametrize("result", [(1, ""), (0, ""), (0, "isolation-ok extra"),
                                    (0, "ISOLATION-OK"), (2, "isolation-ok"), (-9, "isolation-ok")])
def test_preflight_stops_before_any_live_call_when_the_sandbox_probe_does_not_pass_exactly(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: tuple[int, str]) -> None:
    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str]) -> tuple[int, str]:
        del self, workspace, argv
        return result
    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        with pytest.raises(RuntimeError, match="no unsafe fallback"):
            _ = session.preflight()
    finally:
        session.close()
    assert calls(executable) == []


def test_preflight_without_the_os_sandbox_tool_fails_closed_before_any_live_call(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_tool(name: str) -> None:
        del name
    monkeypatch.setattr(shutil, "which", no_tool)
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        with pytest.raises(RuntimeError, match="no unsafe fallback"):
            _ = session.preflight()
    finally:
        session.close()
    assert calls(executable) == []


def test_a_failed_preflight_leaves_no_partial_pass_in_a_second_attempt(tmp_path: Path, probes: list[list[str]]) -> None:
    del probes
    executable = fake_claude(tmp_path, "foreign-skill")
    session = harness(tmp_path, executable)
    try:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="foreign skill"):
                _ = session.preflight()
    finally:
        session.close()
    assert len(calls(executable)) == 2


def test_preflight_budget_exhaustion_stops_before_the_live_call(tmp_path: Path, probes: list[list[str]]) -> None:
    del probes
    from skillz_experiments._runtime import BudgetExhausted
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable, Budget(1, 120, 0))
    try:
        with pytest.raises(BudgetExhausted):
            _ = session.preflight()
    finally:
        session.close()
    assert len(calls(executable)) == 1


# ---- hostile claude output --------------------------------------------------------------------------

@pytest.mark.parametrize(("lines", "code", "reason"), [
    ([INIT_OK, DONE | {"is_error": True}], 0, "fails"),
    ([INIT_OK, DONE], 1, "fails"),
    ([INIT_OK], 0, "execution failed"),
    ([INIT_OK, "not json"], 0, "invalid Claude Code JSON"),
    ([INIT_OK, [1, 2]], 0, "invalid Claude Code JSON"),
    ([INIT_OK, "{\"type\": "], 0, "invalid Claude Code JSON"),
    ([DONE], 0, "isolation fails"),
    ([{"type": "system", "subtype": "init", "skills": ["skillz", "intruder"]}, DONE], 0, "isolation fails"),
    ([{"type": "system", "subtype": "init", "skills": ["skillz", {"name": "intruder"}]}, DONE], 0, "isolation fails"),
    ([{"type": "system", "subtype": "init", "skills": [{"name": "intruder"}]}, DONE], 0, "isolation fails"),
    ([{"type": "system", "subtype": "init", "skills": ["Skillz"]}, DONE], 0, "isolation fails"),
    ([{"type": "system", "subtype": "init", "skills": ["skillz", "skillz-evil"]}, DONE], 0, "isolation fails"),
])
def test_invoke_fails_closed_on_a_hostile_event_stream_and_counts_the_call(
        tmp_path: Path, lines: list[object], code: int, reason: str) -> None:
    executable = scripted(tmp_path, lines, code)
    budget = Budget(10, 120, 0)
    session = harness(tmp_path, executable, budget)
    try:
        with pytest.raises(RuntimeError, match=reason):
            _ = session.transports["task"].invoke("hello", candidate())
    finally:
        session.close()
    assert budget.calls == 1


def test_invoke_with_a_later_foreign_init_event_is_stopped_too(tmp_path: Path) -> None:
    lines = [INIT_OK, {"type": "system", "subtype": "init", "skills": ["skillz", "intruder"]}, DONE]
    session = harness(tmp_path, scripted(tmp_path, lines))
    try:
        with pytest.raises(RuntimeError, match="isolation"):
            _ = session.transports["task"].invoke("hello", candidate())
    finally:
        session.close()


@pytest.mark.parametrize("usage", [{"input_tokens": -5, "output_tokens": True}, {"input_tokens": "9"},
                                   {"input_tokens": 1.5, "cache_read_input_tokens": None}, [], "x", None])
def test_invoke_usage_never_reports_negative_or_non_integer_tokens(tmp_path: Path, usage: object) -> None:
    session = harness(tmp_path, scripted(tmp_path, [INIT_OK, DONE | {"usage": usage}]))
    try:
        result = session.transports["task"].invoke("hello", candidate())
    finally:
        session.close()
    counts = cast(dict[str, object], result["usage"])
    assert all(value is None or (isinstance(value, int) and value >= 0) for value in counts.values())


def test_invoke_answer_is_empty_when_structured_output_and_result_text_are_hostile(tmp_path: Path) -> None:
    for final in (DONE | {"structured_output": [1]}, DONE | {"structured_output": None, "result": "[1]"},
                  DONE | {"structured_output": None, "result": 5}, DONE | {"structured_output": None, "result": "{"}):
        session = harness(tmp_path, scripted(tmp_path, [INIT_OK, final]))
        try:
            result = session.transports["task"].invoke("hello", candidate())
        finally:
            session.close()
        assert result["answer"] == {}


def test_invoke_trace_ignores_tool_results_that_answer_no_recorded_bash_call(tmp_path: Path) -> None:
    forged = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "ghost", "is_error": False}]}}
    session = harness(tmp_path, scripted(tmp_path, [INIT_OK, forged, DONE]))
    try:
        result = session.transports["task"].invoke("hello", candidate())
    finally:
        session.close()
    assert not [e for e in cast(list[dict[str, object]], result["events"]) if e["type"] == "item.completed"]


def test_invoke_trace_does_not_count_a_read_tool_call_as_a_bash_execution(tmp_path: Path) -> None:
    use = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "r", "name": "Read",
                                                         "input": {"command": "cat .agents/skills/skillz/SKILL.md"}}]}}
    done = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "r", "is_error": False}]}}
    session = harness(tmp_path, scripted(tmp_path, [INIT_OK, use, done, DONE]))
    try:
        result = session.transports["task"].invoke("hello", candidate())
    finally:
        session.close()
    assert not [e for e in cast(list[dict[str, object]], result["events"]) if e["type"] == "item.completed"]


def test_invoke_without_a_candidate_stops_when_a_foreign_skill_loads(tmp_path: Path) -> None:
    executable = fake_claude(tmp_path, "foreign-skill")
    session = harness(tmp_path, executable)
    try:
        with pytest.raises(RuntimeError, match="isolation"):
            _ = session.transports["judge"].invoke("grade this")
    finally:
        session.close()


# ---- once-per-run reuse never survives a changed environment --------------------------------------

def recorded_pass(tmp_path: Path, executable: Path) -> dict[str, object]:
    session = harness(tmp_path, executable)
    try:
        return session.preflight()
    finally:
        session.close()


def test_reuse_makes_no_live_call_charges_no_budget_and_still_runs_the_free_sandbox_probe(
        tmp_path: Path, probes: list[list[str]]) -> None:
    executable = fake_claude(tmp_path)
    recorded = recorded_pass(tmp_path, executable)
    before_calls, before_probes = len(calls(executable)), len(probes)
    budget = Budget(10, 120, 0)
    session = harness(tmp_path, executable, budget)
    try:
        again = session.preflight(recorded)
    finally:
        session.close()
    assert len(calls(executable)) == before_calls and budget.calls == 0
    assert len(probes) == before_probes + len(session.transports)
    assert again["environment_hash"] == recorded["environment_hash"]


@pytest.mark.parametrize("change", ["tools", "probe-skill", "settings", "platform", "environment"])
def test_reuse_fails_before_any_live_call_when_the_runtime_environment_changed(
        tmp_path: Path, probes: list[list[str]], monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    del probes
    executable = fake_claude(tmp_path)
    recorded = recorded_pass(tmp_path, executable)
    logged = len(calls(executable))
    if change == "tools":
        monkeypatch.setattr(_claude, "TOOLS", "Bash,Read,Skill,Write")
    elif change == "probe-skill":
        monkeypatch.setattr(_claude, "PROBE_SKILL", "other")
    elif change == "platform":
        monkeypatch.setattr(sys, "platform", "darwin")
    elif change == "settings":
        original = _claude.settings

        def weaker(workspace: Path) -> dict[str, object]:
            document = original(workspace)
            cast(dict[str, object], document["sandbox"])["allowUnsandboxedCommands"] = True
            return document
        monkeypatch.setattr(_claude, "settings", weaker)
    else:
        original_environment = ClaudeCode._environment  # pyright: ignore[reportPrivateUsage]

        def changed(self: ClaudeCode, workspace: Path) -> dict[str, str]:
            return original_environment(self, workspace) | {"CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "0"}
        monkeypatch.setattr(ClaudeCode, "_environment", changed)
    session = harness(tmp_path, executable)
    try:
        with pytest.raises(ValueError, match="differs from the frozen record"):
            _ = session.preflight(recorded)
    finally:
        session.close()
    assert len(calls(executable)) == logged


def test_reuse_fails_when_the_executable_bytes_changed_between_runs(tmp_path: Path, probes: list[list[str]]) -> None:
    del probes
    executable = fake_claude(tmp_path)
    recorded = recorded_pass(tmp_path, executable)
    _ = executable.write_text(executable.read_text() + "\n# tampered\n")
    session = harness(tmp_path, executable)
    try:
        with pytest.raises(ValueError, match="differs from the frozen record"):
            _ = session.preflight(recorded)
    finally:
        session.close()


def test_reuse_fails_when_the_model_changed_between_runs(tmp_path: Path, probes: list[list[str]]) -> None:
    del probes
    executable = fake_claude(tmp_path)
    recorded = recorded_pass(tmp_path, executable)
    config = tmp_path / "other.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "model": "another-model",
                                      "command": [str(executable)]}))
    session = Configuration.load(config, "m").create("m", Budget(10, 120, 0), lambda: None)
    try:
        with pytest.raises(ValueError, match="differs from the frozen record"):
            _ = session.preflight(recorded)
    finally:
        session.close()


def test_a_mutated_executable_stops_preflight_evaluate_and_invoke_within_one_run(
        tmp_path: Path, probes: list[list[str]]) -> None:
    del probes
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        _ = executable.write_text(executable.read_text() + "\n# tampered\n")
        with pytest.raises(ValueError, match="differs from the frozen record"):
            _ = session.preflight()
        with pytest.raises(ValueError, match="differs from the frozen record"):
            _ = session.invoke("hello")
    finally:
        session.close()
    assert calls(executable) == []


@pytest.mark.parametrize("tamper", ["isolation-failed", "isolation-missing", "no-keys", "role-missing", "kept-not-object"])
def test_a_tampered_record_never_grants_a_reuse_pass_and_runs_the_live_probe_again(
        tmp_path: Path, probes: list[list[str]], tamper: str) -> None:
    del probes
    executable = fake_claude(tmp_path)
    recorded = cast(dict[str, object], json.loads(json.dumps(recorded_pass(tmp_path, executable))))
    roles = cast(dict[str, object], recorded["roles"])
    if tamper == "isolation-failed":
        for role in roles.values():
            cast(dict[str, object], role)["isolation"] = "failed"
    elif tamper == "isolation-missing":
        for role in roles.values():
            _ = cast(dict[str, object], role).pop("isolation")
    elif tamper == "no-keys":
        recorded["reuse_keys"] = {}
    elif tamper == "role-missing":
        roles.clear()
    else:
        for name in roles:
            roles[name] = "passed"
    logged = len(calls(executable))
    session = harness(tmp_path, executable)
    try:
        evidence = session.preflight(recorded)
    finally:
        session.close()
    assert len(calls(executable)) == logged + len(session.transports)
    assert cast(int, evidence["live_calls"]) == len(session.transports)


def test_reuse_with_a_failing_sandbox_probe_stops_and_makes_no_live_call(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probes: list[list[str]]) -> None:
    del probes
    executable = fake_claude(tmp_path)
    recorded = recorded_pass(tmp_path, executable)
    logged = len(calls(executable))

    def broken(self: ClaudeCode, workspace: Path, argv: list[str]) -> tuple[int, str]:
        del self, workspace, argv
        return 1, ""
    monkeypatch.setattr(ClaudeCode, "sandbox", broken)
    session = harness(tmp_path, executable)
    try:
        with pytest.raises(RuntimeError, match="no unsafe fallback"):
            _ = session.preflight(recorded)
    finally:
        session.close()
    assert len(calls(executable)) == logged


def test_reuse_key_for_one_role_does_not_unlock_another_role_with_a_different_key(
        tmp_path: Path, probes: list[list[str]]) -> None:
    del probes
    executable = fake_claude(tmp_path)
    recorded = cast(dict[str, object], json.loads(json.dumps(recorded_pass(tmp_path, executable))))
    keys = cast(dict[str, str], recorded["reuse_keys"])
    keys["judge"] = "0" * 64
    session = harness(tmp_path, executable)
    try:
        with pytest.raises(ValueError, match="differs from the frozen record"):
            _ = session.preflight(recorded)
    finally:
        session.close()


def test_a_candidate_that_does_not_load_still_reports_the_usage_of_the_charged_call(tmp_path: Path) -> None:
    executable = scripted(tmp_path, [{"type": "system", "subtype": "init", "skills": []}, DONE])
    session = harness(tmp_path, executable)
    try:
        result = session.transports["task"].invoke("hello", candidate())
    finally:
        session.close()
    assert cast(dict[str, object], result["usage"])["output_tokens"] == 1


def test_environment_key_names_the_present_credential_variables_but_never_their_values(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _claude.AUTHENTICATION:
        monkeypatch.delenv(name, raising=False)
    executable = fake_claude(tmp_path)

    def key() -> str:
        return ClaudeCode("m", Budget(10, 120, 0), lambda: None, executable).environment_key()
    none = key()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-one")
    api = key()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-two")
    assert key() == api != none
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret-one")
    assert key() not in (none, api)

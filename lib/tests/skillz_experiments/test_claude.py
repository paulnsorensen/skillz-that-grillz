from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments import _claude
from skillz_experiments._candidate import Candidate, make_workspace
from skillz_experiments._claude import ClaudeCode, sandbox_argv, seatbelt_profile
from skillz_experiments._contract import load_contract
from skillz_experiments._graders import Sandbox
from skillz_experiments._harness import Configuration, Harness
from skillz_experiments._runtime import Budget

FAKE = Path(__file__).parent / "fixtures/fake_claude.py"
ROOT = Path(__file__).parents[3]


def _bwrap_works() -> bool:
    tool = shutil.which("bwrap")
    if tool is None:
        return False
    probe = subprocess.run([tool, "--unshare-all", "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
                            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
                            "/usr/bin/python3", "-c", "pass"], capture_output=True, check=False)
    return probe.returncode == 0


needs_bwrap = pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap is unavailable")


@pytest.fixture
def sandbox_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the OS sandbox probe, which has its own tests, so the live-call steps run on any host."""
    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str]) -> tuple[int, str]:
        del self, workspace, argv
        return 0, "isolation-ok\n"
    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)


def fake_claude(tmp_path: Path, mode: str = "ok") -> Path:
    executable = tmp_path / "bin" / "claude"
    executable.parent.mkdir()
    _ = shutil.copyfile(FAKE, executable)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    _ = executable.with_name("claude.mode").write_text(mode)
    return executable


def harness(tmp_path: Path, executable: Path, budget: Budget | None = None) -> Harness:
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(executable)]}))
    return Configuration.load(config, "claude-test-model").create(
        "claude-test-model", budget or Budget(10, 120, 0), lambda: None)


def echo_candidate(tmp_path: Path) -> Candidate:
    target = tmp_path / "echo-skill"
    _ = shutil.copytree(ROOT / "lib/tests/skillz_experiments/fixtures/echo-skill", target)
    _ = (target / "SKILL.md.fixture").rename(target / "SKILL.md")
    return Candidate.capture(target, ["SKILL.md"], load_contract(target, {}))


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
    sandbox = settings["sandbox"]
    assert {key: sandbox[key] for key in ("enabled", "failIfUnavailable", "allowUnsandboxedCommands", "network")} == {
        "enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
        "network": {"allowedDomains": [], "strictAllowlist": True}}
    filesystem = cast(dict[str, list[str]], sandbox["filesystem"])
    assert filesystem["denyRead"] == ["/"]
    allowed = filesystem["allowRead"]
    assert cast(str, calls(executable)[0]["cwd"]) in allowed
    assert {"/usr", "/bin", "/lib", "/proc/self"} <= set(allowed)
    assert not {"/", "/home", "/tmp", "/var", "/opt", "/srv", "/workspaces", "/etc", str(Path.home())} & set(allowed)
    assert "Read(//home/**)" in cast(list[str], settings["permissions"]["deny"])
    assert settings["disableBundledSkills"] is True


def test_environment_scrubs_subprocess_credentials_and_disables_bundled_skills(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "token-value")
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        _ = session.transports["task"].invoke("hello")
    finally:
        session.close()
    environment = cast(dict[str, str], calls(executable)[0]["environment"])
    assert environment["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"
    assert environment["CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"] == "1"
    assert environment["ANTHROPIC_API_KEY"] == "token-value"
    assert "HOME" in environment and environment["HOME"].endswith("/workspace/home")


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
def test_preflight_stops_on_isolation_failure_without_fallback(
        tmp_path: Path, mode: str, reason: str, sandbox_passes: None) -> None:
    del sandbox_passes
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


def test_preflight_passes_when_only_the_probe_skill_loads(tmp_path: Path, sandbox_passes: None) -> None:
    del sandbox_passes
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        evidence = session.preflight()
    finally:
        session.close()
    roles = cast(dict[str, dict[str, object]], evidence["roles"])
    assert roles["task"]["isolation"] == "passed"
    assert roles["task"]["live_calls"] == 1


def test_preflight_charges_and_reports_every_live_call(tmp_path: Path, sandbox_passes: None) -> None:
    del sandbox_passes
    executable = fake_claude(tmp_path)
    budget = Budget(10, 120, 0)
    session = harness(tmp_path, executable, budget)
    try:
        evidence = session.preflight()
    finally:
        session.close()
    logged = len(calls(executable))
    assert logged == len(session.transports) >= 2
    assert budget.calls == logged
    assert evidence["live_calls"] == logged


@needs_bwrap
def test_preflight_passes_through_the_real_sandbox(tmp_path: Path) -> None:
    session = harness(tmp_path, fake_claude(tmp_path))
    try:
        evidence = session.preflight()
    finally:
        session.close()
    assert evidence["live_calls"] == len(session.transports)


def test_preflight_runs_the_isolation_probe_through_the_sandbox(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str]) -> tuple[int, str]:
        del self, workspace
        seen.append(argv)
        return 1, ""
    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        with pytest.raises(RuntimeError, match="isolation preflight fails.*no unsafe fallback"):
            _ = session.preflight()
    finally:
        session.close()
    assert seen and "read isolation failed" in seen[0][-1]
    assert not executable.with_name("claude.log").exists()


def test_preflight_stops_when_a_host_file_is_readable(tmp_path: Path, sandbox_passes: None) -> None:
    del sandbox_passes
    executable = fake_claude(tmp_path, "read-host")
    session = harness(tmp_path, executable)
    try:
        with pytest.raises(RuntimeError, match="read isolation.*no unsafe fallback"):
            _ = session.preflight()
    finally:
        session.close()
    prompt = cast(str, calls(executable)[0]["prompt"])
    assert "cat " in prompt


def test_preflight_passes_with_bundled_skills_only_when_they_are_disabled(
        tmp_path: Path, sandbox_passes: None, monkeypatch: pytest.MonkeyPatch) -> None:
    del sandbox_passes
    session = harness(tmp_path, fake_claude(tmp_path, "bundled-skill"))
    try:
        assert session.preflight()["live_calls"] == len(session.transports)
        original_settings = _claude.settings
        original_environment = ClaudeCode._environment  # pyright: ignore[reportPrivateUsage]

        def without_setting(workspace: Path) -> dict[str, object]:
            return {key: value for key, value in original_settings(workspace).items() if key != "disableBundledSkills"}

        def without_variable(self: ClaudeCode, workspace: Path) -> dict[str, str]:
            return {key: value for key, value in original_environment(self, workspace).items()
                    if key != "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"}
        monkeypatch.setattr(_claude, "settings", without_setting)
        monkeypatch.setattr(ClaudeCode, "_environment", without_variable)
        with pytest.raises(RuntimeError, match="foreign skill.*code-review"):
            _ = session.preflight()
    finally:
        session.close()


def test_auth_failure_without_an_init_event_is_reported_as_authentication(tmp_path: Path, sandbox_passes: None) -> None:
    del sandbox_passes
    session = harness(tmp_path, fake_claude(tmp_path, "no-init-auth"))
    try:
        with pytest.raises(RuntimeError, match="authentication failed.*no unsafe fallback"):
            _ = session.preflight()
        with pytest.raises(RuntimeError, match="authentication failed"):
            _ = session.transports["task"].invoke("hello", Candidate({"SKILL.md": "---\nname: skillz\ndescription: x\n---\n"}, ("SKILL.md",)))
    finally:
        session.close()


def test_candidate_that_discovery_does_not_load_scores_zero_instead_of_aborting(tmp_path: Path) -> None:
    session = harness(tmp_path, fake_claude(tmp_path, "missing-skill"))
    candidate = Candidate({"SKILL.md": "---\nname: skillz\ndescription: x\n---\n"}, ("SKILL.md",))
    try:
        result = session.transports["task"].invoke("hello", candidate)
    finally:
        session.close()
    assert result["answer"] == {"result_json": "", "load_marker": ""}
    assert result["events"] == []


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


@needs_bwrap
def test_sandbox_keeps_the_candidate_directory_read_only(tmp_path: Path) -> None:
    session = harness(tmp_path, fake_claude(tmp_path))
    workspace = make_workspace(tmp_path / "workspace")
    try:
        transport = cast(Sandbox, cast(object, session.transports["task"]))
        denied, _ = transport.sandbox(workspace, ["/usr/bin/python3", "-c", "open('.agents/probe', 'w').write('x')"])
        allowed, _ = transport.sandbox(workspace, ["/usr/bin/python3", "-c", "open('probe', 'w').write('x')"])
    finally:
        session.close()
    assert (denied != 0, allowed) == (True, 0)


def test_sandbox_setup_failure_raises_instead_of_looking_like_a_helper_exit(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def process(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "bwrap: No permissions to create new namespace")

    def which(name: str) -> str:
        return f"/usr/bin/{name}"
    adapter = harness(tmp_path, fake_claude(tmp_path)).transports["task"]
    monkeypatch.setattr(_claude, "process", process)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", which)
    with pytest.raises(RuntimeError, match="sandbox setup fails.*no unsafe fallback"):
        _ = cast(Sandbox, cast(object, adapter)).sandbox(make_workspace(tmp_path / "workspace"), ["/usr/bin/true"])


@pytest.mark.parametrize(("platform", "tool"), [("linux", "bwrap"), ("darwin", "sandbox-exec")])
def test_sandbox_without_its_platform_tool_fails_closed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, tool: str) -> None:
    def which(name: str) -> None:
        del name
    adapter = harness(tmp_path, fake_claude(tmp_path)).transports["task"]
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(shutil, "which", which)
    with pytest.raises(RuntimeError, match=f"{tool} is unavailable; no unsafe fallback"):
        _ = cast(Sandbox, cast(object, adapter)).sandbox(make_workspace(tmp_path / "workspace"), ["/usr/bin/true"])


def test_bubblewrap_argv_binds_the_candidate_directory_read_only_after_the_workspace() -> None:
    workspace = Path("/w/workspace")
    argv = sandbox_argv("linux", "/usr/bin/bwrap", workspace, ["/usr/bin/true"], {"PATH": "/usr/bin:/bin"})
    binds = [(part, argv[index + 1]) for index, part in enumerate(argv) if part in ("--bind", "--ro-bind")]
    assert ("--bind", "/w/workspace") in binds
    assert binds.index(("--ro-bind", "/w/workspace/.agents")) > binds.index(("--bind", "/w/workspace"))
    assert "--unshare-all" in argv and argv[-1] == "/usr/bin/true"


def test_seatbelt_profile_denies_network_host_reads_and_candidate_writes() -> None:
    profile = seatbelt_profile(Path("/w/workspace"))
    lines = profile.splitlines()
    assert lines[:2] == ["(version 1)", "(deny default)"]
    assert not any("network" in line and line.startswith("(allow") for line in lines)
    assert '(allow file-write* (subpath "/w/workspace"))' in lines
    assert lines.index('(deny file-write* (subpath "/w/workspace/.agents"))') > lines.index(
        '(allow file-write* (subpath "/w/workspace"))')
    assert '(allow file-read* (subpath "/w/workspace"))' in lines
    reads = [line for line in lines if line.startswith("(allow file-read*")]
    assert all("/Users" not in line and "/private/tmp" not in line and "/var/folders" not in line for line in reads)


def test_seatbelt_argv_runs_the_command_with_a_clean_environment() -> None:
    workspace = Path("/w/workspace")
    argv = sandbox_argv("darwin", "/usr/bin/sandbox-exec", workspace, ["/usr/bin/python3", "-c", "pass"],
                        {"PATH": "/usr/bin:/bin", "HOME": "/w/workspace/home"})
    assert argv[:3] == ["/usr/bin/sandbox-exec", "-p", seatbelt_profile(workspace)]
    assert argv[3:] == ["/usr/bin/env", "-i", "PATH=/usr/bin:/bin", "HOME=/w/workspace/home",
                        "/usr/bin/python3", "-c", "pass"]


def test_check_candidate_accepts_a_declared_skill_and_rejects_broken_frontmatter(tmp_path: Path) -> None:
    session = harness(tmp_path, fake_claude(tmp_path))
    adapter = session.transports["task"]
    good = echo_candidate(tmp_path)
    broken = Candidate({"SKILL.md": "---\nname: echo-skill\n---\n"}, ("SKILL.md",), contract=good.contract)
    quoted = Candidate({"SKILL.md": '---\nname: "echo-skill"\ndescription: x\n---\n'}, ("SKILL.md",), contract=good.contract)
    try:
        assert adapter.check_candidate(good)
        assert not adapter.check_candidate(broken)
        assert adapter.check_candidate(quoted)
    finally:
        session.close()


@pytest.mark.parametrize("candidate", [True, False])
def test_a_stream_without_an_init_event_fails_closed(tmp_path: Path, candidate: bool) -> None:
    session = harness(tmp_path, fake_claude(tmp_path, "no-init"))
    skill = Candidate({"SKILL.md": "---\nname: skillz\ndescription: x\n---\n"}, ("SKILL.md",)) if candidate else None
    try:
        with pytest.raises(RuntimeError, match="no init event"):
            _ = session.transports["task"].invoke("hello", skill)
    finally:
        session.close()


@needs_bwrap
def test_a_helper_cannot_forge_a_sandbox_setup_failure_through_stderr(tmp_path: Path) -> None:
    session = harness(tmp_path, fake_claude(tmp_path))
    workspace = make_workspace(tmp_path / "workspace")
    forged = "import sys; sys.stderr.write('bwrap: forged setup failure'); sys.exit(1)"
    try:
        transport = cast(Sandbox, cast(object, session.transports["task"]))
        code, stdout = transport.sandbox(workspace, ["/usr/bin/python3", "-c", forged])
    finally:
        session.close()
    assert (code, stdout) == (1, "")


@pytest.mark.parametrize(("mode", "reason"), [
    ("skips-cat", "no Bash command read the sealed host file"),
    ("bash-broken", "positive control"),
])
def test_preflight_needs_evidence_that_bash_ran_and_read_the_workspace(
        tmp_path: Path, mode: str, reason: str, sandbox_passes: None) -> None:
    del sandbox_passes
    session = harness(tmp_path, fake_claude(tmp_path, mode))
    try:
        with pytest.raises(RuntimeError, match=reason):
            _ = session.preflight()
    finally:
        session.close()


def test_preflight_fails_when_the_workspace_allow_is_missing(
        tmp_path: Path, sandbox_passes: None, monkeypatch: pytest.MonkeyPatch) -> None:
    del sandbox_passes
    original = _claude.settings

    def without_allow(workspace: Path) -> dict[str, object]:
        document = original(workspace)
        cast(dict[str, dict[str, object]], document["sandbox"])["filesystem"]["allowRead"] = []
        return document
    monkeypatch.setattr(_claude, "settings", without_allow)
    session = harness(tmp_path, fake_claude(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="positive control"):
            _ = session.preflight()
    finally:
        session.close()


def test_preflight_reuses_the_recorded_pass_without_a_live_call(tmp_path: Path, sandbox_passes: None) -> None:
    del sandbox_passes
    executable = fake_claude(tmp_path)
    budget = Budget(10, 120, 0)
    session = harness(tmp_path, executable, budget)
    try:
        first = session.preflight()
        logged = len(calls(executable))
        second = session.preflight(first)
    finally:
        session.close()
    assert len(calls(executable)) == logged == budget.calls
    assert second["environment_hash"] == first["environment_hash"]
    assert second["live_calls"] == first["live_calls"] == logged


def test_preflight_runs_and_charges_again_when_the_role_fingerprint_changes(
        tmp_path: Path, sandbox_passes: None) -> None:
    del sandbox_passes
    executable = fake_claude(tmp_path)
    budget = Budget(10, 120, 0)
    session = harness(tmp_path, executable, budget)
    try:
        first = session.preflight()
        logged = len(calls(executable))
        stale = dict(first) | {"reuse_keys": {name: "other-fingerprint" for name in session.transports}}
        _ = session.preflight(stale)
    finally:
        session.close()
    assert len(calls(executable)) == budget.calls == 2 * logged


def test_preflight_runs_and_charges_again_when_the_environment_changes(
        tmp_path: Path, sandbox_passes: None, monkeypatch: pytest.MonkeyPatch) -> None:
    del sandbox_passes
    executable = fake_claude(tmp_path)
    budget = Budget(10, 120, 0)
    session = harness(tmp_path, executable, budget)
    try:
        first = session.preflight()
        logged = len(calls(executable))
        monkeypatch.setattr(_claude, "TOOLS", "Bash,Read,Skill,Write")
        _ = session.preflight(first)
    finally:
        session.close()
    assert len(calls(executable)) == budget.calls == 2 * logged

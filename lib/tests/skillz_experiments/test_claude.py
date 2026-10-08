from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

import pytest

from skillz_experiments import _claude, _codex
from skillz_experiments._candidate import Candidate, make_workspace
from skillz_experiments._cases import CodedError
from skillz_experiments._claude import (ClaudeCode, ClaudeOptions, NetworkIsolationFailed, resolve_read_roots, sandbox_argv,
                                        seatbelt_profile)
from skillz_experiments._contract import load_contract, parse
from skillz_experiments._evaluator import Transport
from skillz_experiments._graders import Sandbox
from skillz_experiments._harness import Configuration, Harness
from skillz_experiments._runtime import Budget

FAKE = Path(__file__).parent / "fixtures/fake_claude.py"
ROOT = Path(__file__).parents[3]
TOKENS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")


def _bwrap_works() -> bool:
    tool = shutil.which("bwrap")
    if tool is None:
        return False
    probe = subprocess.run([tool, "--unshare-all", "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
                            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
                            "/usr/bin/python3", "-c", "pass"], capture_output=True, check=False)
    return probe.returncode == 0


needs_bwrap = pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap is unavailable")
pytestmark = pytest.mark.usefixtures("host_login")


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
    return Candidate.capture(target, ["SKILL.md"], load_contract(target))


def calls(executable: Path) -> list[dict[str, object]]:
    log = executable.with_name("claude.log")
    return [cast(dict[str, object], json.loads(line)) for line in log.read_text().splitlines()]


def test_partial_claude_usage_stays_unknown() -> None:
    from skillz_experiments._claude import _token_events  # pyright: ignore[reportPrivateUsage]
    from skillz_experiments._evaluation import usage

    assert usage(_token_events({"usage": {"input_tokens": 2}})) == {
        "input_tokens": 2, "cached_input_tokens": 0, "output_tokens": None}
    assert usage(_token_events({"usage": {"input_tokens": 2, "output_tokens": 3,
                                          "cache_read_input_tokens": "bad"}})) == {
        "input_tokens": None, "cached_input_tokens": None, "output_tokens": 3}
    assert usage(_token_events({"usage": {"input_tokens": 2, "output_tokens": 3,
                                          "cache_read_input_tokens": 5,
                                          "cache_creation_input_tokens": 7}})) == {
        "input_tokens": 14, "cached_input_tokens": 5, "output_tokens": 3}


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
    assert "--effort" not in argv


def _claude_config(tmp_path: Path, executable: Path, **options: object) -> Configuration:
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(executable)], **options}))
    return Configuration.load(config, "claude-test-model")


def test_a_claude_role_effort_reaches_every_call_and_the_fingerprint(tmp_path: Path) -> None:
    executable = fake_claude(tmp_path)
    configuration = _claude_config(tmp_path, executable, effort="xhigh")
    session = configuration.create("claude-test-model", Budget(10, 120, 0), lambda: None)
    try:
        _ = session.transports["judge"].invoke("hello")
    finally:
        session.close()
    argv = cast(list[str], calls(executable)[0]["argv"])
    assert argv[argv.index("--effort") + 1] == "xhigh"
    plain = _claude_config(tmp_path, executable)
    assert configuration.roles["judge"].fingerprint() != plain.roles["judge"].fingerprint()


@pytest.mark.parametrize("options", [{"effort": "turbo"}, {"sandbox_read": ["relative/browsers"]},
                                     {"sandbox_read": "/usr"}, {"sandbox_seconds": 0}, {"sandbox_seconds": 601},
                                     {"sandbox_seconds": 2.5}])
def test_invalid_claude_options_stop_the_configuration(tmp_path: Path, options: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="effort|sandbox_read|sandbox_seconds"):
        _ = _claude_config(tmp_path, fake_claude(tmp_path), **options)


def test_claude_options_are_refused_for_another_adapter(tmp_path: Path) -> None:
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "command", "identity": "w",
                                      "command": [str(fake_claude(tmp_path))], "effort": "high"}))
    with pytest.raises(ValueError, match="only to a claude role"):
        _ = Configuration.load(config, "m")
    with pytest.raises(ValueError, match="only to --harness claude"):
        _ = Configuration.single("codex", "m", ClaudeOptions(effort="high"))


def test_sandbox_read_roots_that_overlap_the_login_temp_home_or_run_directory_are_refused(
        tmp_path: Path, host_login: Path) -> None:
    out = tmp_path / "run"
    (out / "inner").mkdir(parents=True)
    (tmp_path / "scratch/other").mkdir()
    for root in (host_login.parent, host_login.parents[1], tmp_path, tmp_path / "scratch/other", out / "inner"):
        with pytest.raises(ValueError, match="overlaps"):
            _ = resolve_read_roots((str(root),), out)
    with pytest.raises(ValueError, match="does not exist"):
        _ = resolve_read_roots((str(tmp_path / "missing"),), out)
    quoted = tmp_path / 'we"ird'
    quoted.mkdir()
    with pytest.raises(ValueError, match="quote"):
        _ = resolve_read_roots((str(quoted),), out)
    browsers = tmp_path / "browsers"
    browsers.mkdir()
    assert resolve_read_roots((str(browsers),), out) == (str(browsers.resolve()),)
    with pytest.raises(ValueError, match="overlaps"):
        _ = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path),
                       options=ClaudeOptions(sandbox_read=(str(host_login.parent),)))


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
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "token-value")
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        _ = session.transports["task"].invoke("hello")
    finally:
        session.close()
    environment = cast(dict[str, str], calls(executable)[0]["environment"])
    assert environment["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"
    assert environment["CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"] == "1"
    assert not set(TOKENS) & set(environment)
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

        def without_setting(workspace: Path, out: Path | None = None) -> dict[str, object]:
            return {key: value for key, value in original_settings(workspace, out).items() if key != "disableBundledSkills"}

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


def test_preflight_runs_a_network_attempt_through_bash_and_records_the_denial(tmp_path: Path, sandbox_passes: None) -> None:
    del sandbox_passes
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        evidence = session.preflight()
    finally:
        session.close()
    assert "connect_ex(('127.0.0.1'," in cast(str, calls(executable)[0]["prompt"])
    assert cast(dict[str, dict[str, object]], evidence["roles"])["task"]["network"] == "denied"


@pytest.mark.parametrize("mode", ["net-open", "net-http-open", "net-lie-connect"])
def test_preflight_fails_with_a_code_when_the_listener_receives_the_token(
        tmp_path: Path, mode: str, sandbox_passes: None) -> None:
    del sandbox_passes
    session = harness(tmp_path, fake_claude(tmp_path, mode))
    try:
        with pytest.raises(NetworkIsolationFailed, match="network isolation failed.*no unsafe fallback") as caught:
            _ = session.preflight()
    finally:
        session.close()
    assert caught.value.code == "network-isolation-failed"


@pytest.mark.parametrize("mode", ["net-skipped", "net-garbled", "net-lie", "net-no-curl", "net-exit-134", "net-exit-2",
                                  "net-exit-5", "net-exit-6", "net-exit-22"])
def test_preflight_fails_closed_when_the_network_output_is_missing_malformed_or_not_from_the_command(
        tmp_path: Path, mode: str, sandbox_passes: None) -> None:
    del sandbox_passes
    session = harness(tmp_path, fake_claude(tmp_path, mode))
    try:
        with pytest.raises(NetworkIsolationFailed, match="network probe has no evidence.*no unsafe fallback") as caught:
            _ = session.preflight()
    finally:
        session.close()
    assert caught.value.code == "network-isolation-failed"


@pytest.mark.parametrize("code", [0, 28, 56])
def test_preflight_passes_when_curl_exits_with_a_code_that_shows_an_attempt(
        tmp_path: Path, code: int, sandbox_passes: None) -> None:
    del sandbox_passes
    session = harness(tmp_path, fake_claude(tmp_path, f"net-exit-{code}"))
    try:
        evidence = session.preflight()
    finally:
        session.close()
    assert cast(dict[str, dict[str, object]], evidence["roles"])["task"]["network"] == "denied"


def test_a_network_leak_keeps_its_code_when_the_run_then_fails(
        tmp_path: Path, sandbox_passes: None, monkeypatch: pytest.MonkeyPatch) -> None:
    del sandbox_passes
    original = cast(Callable[..., object], getattr(_claude, "process"))

    def run_then_time_out(*args: object, **kwargs: object) -> object:
        _ = original(*args, **kwargs)
        raise TimeoutError("deadline")
    monkeypatch.setattr(_claude, "process", run_then_time_out)
    session = harness(tmp_path, fake_claude(tmp_path, "net-open"))
    try:
        with pytest.raises(NetworkIsolationFailed, match="network isolation failed") as caught:
            _ = session.preflight()
    finally:
        session.close()
    assert caught.value.code == "network-isolation-failed"


def test_a_network_leak_keeps_its_code_when_an_earlier_check_also_fails(
        tmp_path: Path, sandbox_passes: None, monkeypatch: pytest.MonkeyPatch) -> None:
    del sandbox_passes

    def write_fails(events: list[dict[str, object]], agents_file: str, wrote: bool, controlled: bool) -> str:
        del events, agents_file, wrote, controlled
        return "write isolation failed: test"
    monkeypatch.setattr(_claude, "_write_failure", write_fails)
    session = harness(tmp_path, fake_claude(tmp_path, "net-open"))
    try:
        with pytest.raises(NetworkIsolationFailed, match="network isolation failed") as caught:
            _ = session.preflight()
    finally:
        session.close()
    assert caught.value.code == "network-isolation-failed"


def test_preflight_sends_the_http_probe_through_the_proxy_for_loopback_names(tmp_path: Path, sandbox_passes: None) -> None:
    del sandbox_passes
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    try:
        _ = session.preflight()
    finally:
        session.close()
    prompt = cast(str, calls(executable)[0]["prompt"])
    assert prompt.count("/usr/bin/curl --noproxy '' ") == 2
    assert "http://localhost:" in prompt and "http://127.0.0.1:" in prompt


def test_environment_key_changes_when_the_network_probe_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path))
    before = session.environment_key()
    monkeypatch.setattr(_claude, "NETWORK_PROBE", "other")
    assert session.environment_key() != before


def test_environment_key_changes_when_the_network_settings_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path))
    before = session.environment_key()
    original = _claude.settings

    def open_network(workspace: Path, out: Path | None = None) -> dict[str, object]:
        changed = original(workspace, out)
        changed["sandbox"] = cast(dict[str, object], changed["sandbox"]) | {
            "network": {"allowedDomains": ["example.com"], "strictAllowlist": True}}
        return changed
    monkeypatch.setattr(_claude, "settings", open_network)
    assert session.environment_key() != before


def test_invoke_with_a_candidate_stops_when_a_foreign_skill_loads(tmp_path: Path) -> None:
    session = harness(tmp_path, fake_claude(tmp_path, "foreign-skill"))
    candidate = Candidate({"SKILL.md": "---\nname: skillz\ndescription: x\n---\n"}, ("SKILL.md",))
    try:
        with pytest.raises(CodedError, match="isolation") as caught:
            _ = session.transports["task"].invoke("hello", candidate)
        assert caught.value.code == "isolation-failed"
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
    with pytest.raises(CodedError, match="sandbox setup fails.*no unsafe fallback"):
        _ = cast(Sandbox, cast(object, adapter)).sandbox(make_workspace(tmp_path / "workspace"), ["/usr/bin/true"])


@pytest.mark.parametrize(("platform", "tool"), [("linux", "bwrap"), ("darwin", "sandbox-exec")])
def test_sandbox_without_its_platform_tool_fails_closed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, tool: str) -> None:
    def which(name: str) -> None:
        del name
    adapter = harness(tmp_path, fake_claude(tmp_path)).transports["task"]
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(shutil, "which", which)
    with pytest.raises(CodedError, match=f"{tool} is unavailable; no unsafe fallback"):
        _ = cast(Sandbox, cast(object, adapter)).sandbox(make_workspace(tmp_path / "workspace"), ["/usr/bin/true"])


def test_bubblewrap_argv_binds_the_candidate_directory_read_only_after_the_workspace() -> None:
    workspace = Path("/w/workspace")
    argv = sandbox_argv("linux", "/usr/bin/bwrap", workspace, ["/usr/bin/true"], {"PATH": "/usr/bin:/bin"})
    binds = [(part, argv[index + 1]) for index, part in enumerate(argv) if part in ("--bind", "--ro-bind")]
    assert ("--bind", "/w/workspace") in binds
    assert binds.index(("--ro-bind", "/w/workspace/.agents")) > binds.index(("--bind", "/w/workspace"))
    assert "--unshare-all" in argv and argv[-1] == "/usr/bin/true"


def test_bubblewrap_argv_mounts_each_read_root_read_only_at_its_own_path() -> None:
    argv = sandbox_argv("linux", "/usr/bin/bwrap", Path("/w/workspace"), ["/usr/bin/true"], {}, ("/opt/pw-browsers",))
    index = argv.index("/opt/pw-browsers")
    assert argv[index - 1:index + 2] == ["--ro-bind", "/opt/pw-browsers", "/opt/pw-browsers"]
    assert argv.count("/opt/pw-browsers") == 2 and "--unshare-all" in argv


def test_seatbelt_profile_allows_reads_only_of_each_read_root() -> None:
    lines = seatbelt_profile(Path("/w/workspace"), ("/opt/pw-browsers",)).splitlines()
    assert '(allow file-read* (subpath "/opt/pw-browsers"))' in lines
    assert not any("/opt/pw-browsers" in line for line in lines if "write" in line or "network" in line)


@needs_bwrap
def test_a_bubblewrap_command_reads_a_read_root_and_serves_itself_on_loopback(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The capture case: read a browser root, start a server inside the sandbox, and reach it on loopback."""
    monkeypatch.setattr(sys, "platform", "linux")
    browsers = tmp_path / "browsers"
    browsers.mkdir()
    _ = (browsers / "chrome").write_text("browser")
    adapter = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path),
                         options=ClaudeOptions(sandbox_read=(str(browsers),)))
    script = ("import pathlib,socket\n"
              f"root=pathlib.Path({str(browsers.resolve())!r})\n"
              "assert (root/'chrome').read_text()=='browser'\n"
              "try: (root/'written').write_text('x')\n"
              "except OSError: pass\n"
              "else: raise SystemExit('read root is writable')\n"
              "server=socket.create_server(('127.0.0.1',0))\n"
              "client=socket.create_connection(server.getsockname(),timeout=2)\n"
              "peer=server.accept()[0];client.sendall(b'ping')\n"
              "print(peer.recv(4).decode())\n")
    try:
        code, output = adapter.sandbox(make_workspace(tmp_path / "workspace"), ["/usr/bin/python3", "-c", script])
    finally:
        adapter.close()
    assert (code, output.strip()) == (0, "ping")
    assert not (browsers / "written").exists()


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


def test_nested_helper_fixture_runs_through_claude_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contract = parse({"schema_version": 1, "status": "approved", "skill": "echo-skill",
                      "invocation": "$echo-skill run", "kinds": {"echo": {"grader": "exact-json"}},
                      "helper": {"path": "scripts/echo.py", "input": "fixtures/nested/input.md",
                                 "fixtures": [{"input": "hi", "returncode": 0, "output": {"ok": True}}]}}, "skill")
    candidate = Candidate({"SKILL.md": "---\nname: echo-skill\ndescription: echo\n---\n",
                           "scripts/echo.py": "print(1)\n"}, ("SKILL.md", "scripts/echo.py"), contract)

    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str]) -> tuple[int, str]:
        del self
        assert argv[-1] == "fixtures/nested/input.md"
        assert (workspace / argv[-1]).read_text() == "hi"
        return 0, '{"ok": true}'

    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)
    adapter = harness(tmp_path, fake_claude(tmp_path))
    try:
        assert adapter.transports["task"].check_candidate(candidate)
    finally:
        adapter.close()


def test_check_candidate_rejects_a_skill_that_declares_hooks(tmp_path: Path) -> None:
    session = harness(tmp_path, fake_claude(tmp_path))
    adapter = session.transports["task"]
    good = echo_candidate(tmp_path)
    hooked = Candidate({"SKILL.md": "---\nname: echo-skill\ndescription: x\nhooks:\n  PreToolUse: []\n---\n"},
                       ("SKILL.md",), contract=good.contract)
    try:
        assert not adapter.check_candidate(hooked)
    finally:
        session.close()


@pytest.mark.parametrize("extra", [
    '"hooks":', "'hooks':", "? hooks", "Hooks:", "user-invocable: false",
])
def test_check_candidate_rejects_hidden_forms_of_unsupported_frontmatter_keys(tmp_path: Path, extra: str) -> None:
    session = harness(tmp_path, fake_claude(tmp_path))
    adapter = session.transports["task"]
    good = echo_candidate(tmp_path)
    hooked = Candidate({"SKILL.md": f"---\nname: echo-skill\ndescription: x\n{extra}\n  x: y\n---\n"},
                       ("SKILL.md",), contract=good.contract)
    try:
        assert not adapter.check_candidate(hooked)
    finally:
        session.close()


def test_check_candidate_reads_the_frontmatter_end_as_a_line_and_allows_nested_hooks(tmp_path: Path) -> None:
    session = harness(tmp_path, fake_claude(tmp_path))
    adapter = session.transports["task"]
    good = echo_candidate(tmp_path)
    inline = Candidate({"SKILL.md": "---\nname: echo-skill\ndescription: run a---b\nhooks: x\n---\n"},
                       ("SKILL.md",), contract=good.contract)
    nested = Candidate({"SKILL.md": "---\nname: echo-skill\ndescription: x\nmetadata:\n  hooks: x\n---\n"},
                       ("SKILL.md",), contract=good.contract)
    try:
        assert not adapter.check_candidate(inline)
        assert adapter.check_candidate(nested)
    finally:
        session.close()


@pytest.mark.parametrize("candidate", [True, False])
def test_a_stream_without_an_init_event_fails_closed(tmp_path: Path, candidate: bool) -> None:
    session = harness(tmp_path, fake_claude(tmp_path, "no-init"))
    skill = Candidate({"SKILL.md": "---\nname: skillz\ndescription: x\n---\n"}, ("SKILL.md",)) if candidate else None
    try:
        with pytest.raises(CodedError, match="no init event"):
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
    ("read-fallback", "positive control"),
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

    def without_allow(workspace: Path, out: Path | None = None) -> dict[str, object]:
        document = original(workspace, out)
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


def test_preflight_fails_without_a_charge_when_the_role_fingerprint_changes(
        tmp_path: Path, sandbox_passes: None) -> None:
    del sandbox_passes
    executable = fake_claude(tmp_path)
    budget = Budget(10, 120, 0)
    session = harness(tmp_path, executable, budget)
    try:
        first = session.preflight()
        logged = len(calls(executable))
        stale = dict(first) | {"reuse_keys": {name: "other-fingerprint" for name in session.transports}}
        with pytest.raises(ValueError, match="runtime environment differs"):
            _ = session.preflight(stale)
    finally:
        session.close()
    assert len(calls(executable)) == budget.calls == logged


def test_preflight_fails_without_a_charge_when_the_environment_changes(
        tmp_path: Path, sandbox_passes: None, monkeypatch: pytest.MonkeyPatch) -> None:
    del sandbox_passes
    executable = fake_claude(tmp_path)
    budget = Budget(10, 120, 0)
    session = harness(tmp_path, executable, budget)
    try:
        first = session.preflight()
        logged = len(calls(executable))
        monkeypatch.setattr(_claude, "TOOLS", "Bash,Read,Skill,Write")
        with pytest.raises(ValueError, match="runtime environment differs"):
            _ = session.preflight(first)
    finally:
        session.close()
    assert len(calls(executable)) == budget.calls == logged


def test_preflight_runs_the_free_sandbox_probe_when_it_reuses_a_pass(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[int] = []

    def sandbox(self: object, workspace: Path, argv: list[str]) -> tuple[int, str]:
        del self, workspace, argv
        runs.append(1)
        return (0, "isolation-ok\n") if len(runs) <= len(session.transports) else (1, "")
    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable, Budget(10, 120, 0))
    try:
        first = session.preflight()
        logged = len(calls(executable))
        with pytest.raises(RuntimeError, match="sandbox probe failed"):
            _ = session.preflight(first)
    finally:
        session.close()
    assert len(calls(executable)) == logged


def test_settings_allow_only_the_minimal_device_nodes(tmp_path: Path) -> None:
    filesystem = cast(dict[str, dict[str, list[str]]], _claude.settings(tmp_path)["sandbox"])["filesystem"]
    allowed = set(filesystem["allowRead"])
    assert {"/dev/null", "/dev/zero", "/dev/random", "/dev/urandom"} <= allowed
    assert not {"/dev", "/dev/tty"} & allowed


def test_codex_event_stream_with_deeply_nested_json_is_an_invalid_stream_not_a_recursion_error() -> None:
    with pytest.raises(RuntimeError, match="invalid Codex JSON event stream"):
        _ = _codex._events("[" * 200_000)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("umask_022")
def test_linux_environment_links_only_the_credential_and_forwards_no_token(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host_login: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    for name in TOKENS:
        monkeypatch.setenv(name, "token-value")
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    config_dir = cast(ClaudeCode, session.transports["task"]).config_dir
    try:
        _ = session.transports["task"].invoke("hello")
        assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    finally:
        session.close()
    call = calls(executable)[0]
    environment = cast(dict[str, str], call["environment"])
    workspace = Path(cast(str, call["cwd"]))
    assert not set(TOKENS) & set(environment)
    assert Path(environment["HOME"]) == workspace / "home"
    assert Path(environment["CLAUDE_CONFIG_DIR"]) == config_dir
    assert config_dir.parent == Path(tempfile.gettempdir()) and config_dir.name.startswith("skillz-claude-config-")
    assert not config_dir.is_relative_to(workspace)
    assert call["config_entries"] == {".credentials.json": str(host_login.resolve())}
    settings = cast(dict[str, dict[str, list[str]]], json.loads(cast(str, call["settings"])))
    assert f"Read(/{tempfile.gettempdir()}/skillz-claude-config-*/**)" in settings["permissions"]["deny"]


def test_the_login_comes_from_the_host_config_dir_when_it_is_set(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "other-config"
    other.mkdir()
    _ = (other / ".credentials.json").write_text("{}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(other))
    adapter = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path))
    try:
        assert os.readlink(adapter.config_dir / ".credentials.json") == str((other / ".credentials.json").resolve())
    finally:
        adapter.close()


def test_a_missing_login_is_coded_with_a_fix_hint(tmp_path: Path, host_login: Path) -> None:
    host_login.unlink()
    with pytest.raises(CodedError, match="run `claude` once and log in") as caught:
        _ = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path))
    assert caught.value.code == "login-missing"
    assert not list(Path(tempfile.gettempdir()).glob("skillz-claude-config-*"))


def test_close_deletes_the_config_dir(tmp_path: Path) -> None:
    adapter = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path))
    directory = adapter.config_dir
    assert [path.name for path in directory.iterdir()] == [".credentials.json"]
    adapter.close()
    assert not directory.exists()
    adapter.close()


def _login(token: str = "a", **extra: str) -> str:
    return json.dumps({"claudeAiOauth": {"accessToken": token, "refreshToken": f"r-{token}"}, **extra})


@pytest.mark.parametrize(("change", "code"), [
    ("regular-file", "credential-changed"), ("repointed", "credential-changed"), ("removed", "credential-changed"),
    ("target-replaced", "credential-rotated"), ("target-replaced-by-a-logout", "credential-changed")])
def test_close_reports_how_the_login_changed_and_deletes_the_config_dir(
        tmp_path: Path, host_login: Path, monkeypatch: pytest.MonkeyPatch, change: str, code: str) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _ = host_login.write_text(_login())
    original = host_login.read_text()
    adapter = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path))
    directory = adapter.config_dir
    link = directory / ".credentials.json"
    other = tmp_path / "other.json"
    _ = other.write_text("{\"other\": true}" if change.endswith("logout") else _login("other"))
    if change == "regular-file":
        link.unlink()
        _ = link.write_text("not json")
    elif change == "repointed":
        link.unlink()
        link.symlink_to(other)
    elif change == "removed":
        link.unlink()
    else:
        os.replace(other, host_login)
    with pytest.raises(CodedError) as caught:
        adapter.close()
    assert caught.value.code == code
    if code == "credential-changed":
        assert "Log in again" in str(caught.value)
    assert not directory.exists()
    if not change.startswith("target-replaced"):
        assert host_login.read_text() == original


def test_close_copies_a_refreshed_login_back_while_the_real_file_is_unchanged(
        tmp_path: Path, host_login: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _ = host_login.write_text(_login())
    adapter = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path))
    directory = adapter.config_dir
    link = directory / ".credentials.json"
    link.unlink()
    _ = link.write_text(_login("refreshed"))
    with pytest.raises(CodedError) as caught:
        adapter.close()
    assert caught.value.code == "credential-refreshed"
    real = host_login.resolve()
    assert real.read_text() == _login("refreshed") and stat.S_IMODE(real.stat().st_mode) == 0o600
    assert not directory.exists() and not list(real.parent.glob(".*.skillz-*"))


@pytest.mark.parametrize("refreshed", ["{\"token\": \"refreshed\"}", json.dumps({"claudeAiOauth": {"accessToken": "a"}}),
                                       json.dumps({"claudeAiOauth": {"accessToken": "", "refreshToken": "r"}}),
                                       _login("refreshed", extra="key"), "[]"])
def test_close_refuses_a_refreshed_file_that_is_not_the_same_kind_of_login(
        tmp_path: Path, host_login: Path, monkeypatch: pytest.MonkeyPatch, refreshed: str) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _ = host_login.write_text(_login())
    adapter = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path))
    link = adapter.config_dir / ".credentials.json"
    link.unlink()
    _ = link.write_text(refreshed)
    with pytest.raises(CodedError) as caught:
        adapter.close()
    assert caught.value.code == "credential-changed" and host_login.read_text() == _login()


def test_close_never_overwrites_a_login_that_another_process_also_replaced(
        tmp_path: Path, host_login: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _ = host_login.write_text(_login())
    adapter = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path))
    link = adapter.config_dir / ".credentials.json"
    link.unlink()
    _ = link.write_text(_login("ours"))
    newer = tmp_path / "newer.json"
    _ = newer.write_text(_login("theirs"))
    os.replace(newer, host_login.resolve())
    with pytest.raises(CodedError) as caught:
        adapter.close()
    assert caught.value.code == "credential-changed"
    assert host_login.resolve().read_text() == _login("theirs")


def test_every_claude_role_of_a_harness_shares_one_config_dir_and_the_login_closes_once(
        tmp_path: Path, host_login: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _ = host_login.write_text(_login())
    session = harness(tmp_path, fake_claude(tmp_path))
    transports = [cast(ClaudeCode, transport) for transport in session.transports.values()]
    directories = {transport.config_dir for transport in transports}
    assert len(directories) == 1 and len({transport.credential for transport in transports}) == 1
    directory = next(iter(directories))
    link = directory / ".credentials.json"
    link.unlink()
    _ = link.write_text(_login("refreshed"))
    with pytest.raises(CodedError) as caught:
        session.close()
    assert caught.value.code == "credential-refreshed"
    assert host_login.resolve().read_text() == _login("refreshed") and not directory.exists()
    session.close()


@final
class _FailingClose:
    def __init__(self, error: Exception) -> None:
        self.error: Exception = error

    def close(self) -> None:
        raise self.error


@pytest.mark.parametrize(("codes", "winner"), [
    (["credential-refreshed", "credential-changed"], "credential-changed"),
    (["credential-changed", "credential-rotated"], "credential-changed"),
    (["credential-rotated", "other"], "other"),
    (["other", "isolation-failed", "credential-refreshed"], "isolation-failed")])
def test_harness_close_closes_every_transport_and_raises_the_most_severe_error(
        tmp_path: Path, codes: list[str], winner: str) -> None:
    session = harness(tmp_path, fake_claude(tmp_path))
    directory = cast(ClaudeCode, session.transports["task"]).config_dir
    session.transports = cast("dict[str, Transport]", {
        f"role{index}": _FailingClose(CodedError(code, code)) for index, code in enumerate(codes)})
    with pytest.raises(CodedError) as caught:
        session.close()
    assert caught.value.code == winner
    assert not directory.exists()


def test_macos_uses_the_real_config_dir_and_preflight_rejects_foreign_init_entries(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sandbox_passes: None) -> None:
    del sandbox_passes
    monkeypatch.setattr(sys, "platform", "darwin")
    real = tmp_path / "real-config"
    real.mkdir()
    _ = (real / ".credentials.json").write_text("{}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(real))
    executable = fake_claude(tmp_path)
    session = harness(tmp_path, executable)
    adapter = cast(ClaudeCode, session.transports["task"])
    try:
        assert adapter.config_dir == real and adapter.credential is None
        evidence = session.preflight()
        first = calls(executable)[0]
        assert cast(dict[str, str], first["environment"])["CLAUDE_CONFIG_DIR"] == str(real)
        assert first["config_entries"] == {".credentials.json": "file"}
        assert evidence["live_calls"] == len(session.transports)
        for mode, kind in [("foreign-skill", "skill"), ("foreign-plugin", "plugin"), ("foreign-mcp", "MCP server"),
                           ("foreign-agent", "agent")]:
            _ = executable.with_name("claude.mode").write_text(mode)
            with pytest.raises(CodedError, match=f"foreign {kind}") as caught:
                _ = session.preflight()
            assert caught.value.code == "preflight-leak"
    finally:
        session.close()
    assert real.is_dir() and not list(Path(tempfile.gettempdir()).glob("skillz-claude-config-*"))


@pytest.mark.parametrize(("memory", "stage"), [("CLAUDE.md", "preflight"), ("rules", "preflight"),
                                               ("CLAUDE.md", "resume"), ("none", "resume")])
def test_macos_stops_before_any_call_when_user_memory_would_load(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sandbox_passes: None, memory: str, stage: str) -> None:
    """A first preflight and the free probe on every resume both check memory. An empty `rules` directory passes."""
    del sandbox_passes
    monkeypatch.setattr(sys, "platform", "darwin")
    real = tmp_path / "real-config"
    real.mkdir()
    _ = (real / ".credentials.json").write_text("{}")
    (real / "rules").mkdir()
    if memory == "CLAUDE.md":
        _ = (real / "CLAUDE.md").write_text("Always answer in French.")
    elif memory == "rules":
        _ = (real / "rules" / "style.md").write_text("Always answer in French.")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(real))
    executable = fake_claude(tmp_path)
    budget = Budget(10, 120, 0)
    adapter = ClaudeCode("m", budget, lambda: None, executable)
    check = adapter.preflight if stage == "preflight" else adapter.check_sandbox
    try:
        if memory == "none":
            _ = check()
            return
        with pytest.raises(CodedError, match="user memory") as caught:
            _ = check()
    finally:
        adapter.close()
    assert caught.value.code == "preflight-leak" and memory in str(caught.value)
    assert budget.calls == 0 and not executable.with_name("claude.log").exists()


def test_close_reports_credential_changed_and_leaves_no_temporary_when_the_replace_fails(
        tmp_path: Path, host_login: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _ = host_login.write_text(_login())
    real = host_login.resolve()
    original = real.read_text()
    adapter = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path))
    link = adapter.config_dir / ".credentials.json"
    link.unlink()
    _ = link.write_text(_login("new"))

    def refuse(source: object, destination: object) -> None:
        del source, destination
        raise OSError("replace refused")

    monkeypatch.setattr("skillz_experiments._claude.os.replace", refuse)
    with pytest.raises(CodedError) as caught:
        adapter.close()
    assert caught.value.code == "credential-changed" and real.read_text() == original
    assert not list(real.parent.glob(".*.skillz-*"))


def test_the_real_login_is_not_replaced_when_a_newer_login_lands_during_the_write(
        tmp_path: Path, host_login: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _ = host_login.write_text(_login())
    real = host_login.resolve()
    adapter = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path))
    link = adapter.config_dir / ".credentials.json"
    link.unlink()
    _ = link.write_text(_login("ours"))
    newer = tmp_path / "newer.json"
    _ = newer.write_text(_login("theirs"))
    real_fsync = os.fsync

    def fsync_then_race(descriptor: int) -> None:
        real_fsync(descriptor)
        if real.read_text() == _login():
            os.replace(newer, real)

    monkeypatch.setattr("skillz_experiments._claude.os.fsync", fsync_then_race)
    with pytest.raises(CodedError) as caught:
        adapter.close()
    assert caught.value.code == "credential-changed" and real.read_text() == _login("theirs")
    assert not list(real.parent.glob(".*.skillz-*"))


def test_user_memory_counts_only_markdown_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    _ = (rules / ".DS_Store").write_text("x")
    _ = (rules / "README.txt").write_text("x")
    assert _claude._user_memory(tmp_path) == []  # pyright: ignore[reportPrivateUsage]
    _ = (rules / "style.md").write_text("x")
    assert _claude._user_memory(tmp_path) == ["rules"]  # pyright: ignore[reportPrivateUsage]

    def unreadable(self: Path, pattern: str) -> list[Path]:
        del self, pattern
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "rglob", unreadable)
    with pytest.raises(CodedError, match="cannot be scanned") as caught:
        _ = _claude._user_memory(tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert caught.value.code == "preflight-leak"


def test_macos_checks_user_memory_before_every_invocation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host_login: Path) -> None:
    del host_login
    monkeypatch.setattr(sys, "platform", "darwin")
    real = tmp_path / "real-config"
    real.mkdir()
    _ = (real / ".credentials.json").write_text("{}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(real))
    executable = fake_claude(tmp_path)
    budget = Budget(10, 120, 0)
    adapter = ClaudeCode("m", budget, lambda: None, executable)
    _ = (real / "CLAUDE.md").write_text("Always answer in French.")
    with pytest.raises(CodedError, match="user memory CLAUDE.md in the real config directory") as caught:
        _ = adapter.invoke("hello")
    assert caught.value.code == "preflight-leak" and budget.calls == 0
    assert not executable.with_name("claude.log").exists()


@pytest.mark.parametrize(("case", "fix", "task_calls"), [
    ("bwrap-missing", "install bubblewrap", 0),
    ("userns-restricted", "kernel.apparmor_restrict_unprivileged_userns=0", 0),
    ("socat-missing", "install socat", 0),
    ("live-sandbox-failure", "install bubblewrap", 1),
])
def test_sandbox_unavailable_is_coded_with_a_fix_and_spends_no_task_call(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, fix: str, task_calls: int) -> None:
    real_which = shutil.which
    missing = {"bwrap-missing": "bwrap", "socat-missing": "socat"}.get(case)

    def which(name: str) -> str | None:
        return None if name == missing else real_which(name) or f"/usr/bin/{name}"

    def process(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "bwrap: setting up uid map: Permission denied")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", which)
    if case == "socat-missing":
        monkeypatch.setattr(_claude, "SANDBOX_HELPERS", ("socat",))
    if case == "userns-restricted":
        monkeypatch.setattr(_claude, "process", process)
    if case == "live-sandbox-failure":
        def sandbox(self: ClaudeCode, workspace: Path, argv: list[str]) -> tuple[int, str]:
            del self, workspace, argv
            return 0, "isolation-ok\n"
        monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)
    executable = fake_claude(tmp_path, "sandbox-unavailable" if case == "live-sandbox-failure" else "ok")
    budget = Budget(10, 120, 0)
    session = harness(tmp_path, executable, budget)
    try:
        with pytest.raises(CodedError) as caught:
            _ = session.preflight()
    finally:
        session.close()
    assert caught.value.code == "sandbox-unavailable"
    assert fix in str(caught.value) and "no unsafe fallback" in str(caught.value)
    assert budget.calls == task_calls == (len(calls(executable)) if task_calls else 0)


def test_settings_deny_reading_proc_and_the_run_directory(tmp_path: Path) -> None:
    out = tmp_path / "run"
    out.mkdir()
    deny = cast(dict[str, list[str]], _claude.settings(tmp_path / "workspace", out)["permissions"])["deny"]
    assert {"Read(//proc/**)", f"Read(/{out}/**)", f"Read(/{out.resolve()}/**)"} <= set(deny)
    assert f"Read(/{out}/**)" not in cast(dict[str, list[str]], _claude.settings(tmp_path / "workspace")["permissions"])["deny"]


def test_environment_key_changes_with_the_run_directory(tmp_path: Path) -> None:
    executable = fake_claude(tmp_path)
    first = ClaudeCode("m", Budget(10, 120, 0), lambda: None, executable, tmp_path / "a")
    second = ClaudeCode("m", Budget(10, 120, 0), lambda: None, executable, tmp_path / "b")
    try:
        assert first.environment_key() != second.environment_key()
    finally:
        first.close()
        second.close()

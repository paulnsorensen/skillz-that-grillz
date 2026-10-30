"""The free host checks: one pass, no model call, before the first question."""
from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments.run import _run
from skillz_experiments.harness import _codex, _doctor
from skillz_experiments.harness._claude import _sandbox, _transport
from skillz_experiments.harness._claude._transport import ClaudeCode
from skillz_experiments._cli import main
from skillz_experiments._cases import CodedError
from skillz_experiments.run import Stop, run

FAKE = Path(__file__).parent / "fixtures/fake_claude.py"
REAL_SANDBOX = ClaudeCode.sandbox
pytestmark = pytest.mark.usefixtures("host_login")


@pytest.fixture
def host_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], Path]:
    """Put only a fake `claude` and a stub `bwrap` on PATH, and stand in for the sandbox probe."""
    directory = tmp_path / "host-bin"
    directory.mkdir()
    bwrap = directory / "bwrap"
    _ = bwrap.write_text("#!/bin/sh\nexit 0\n")
    bwrap.chmod(0o755)
    monkeypatch.setenv("PATH", f"{directory}:/usr/bin:/bin")
    monkeypatch.setattr(_sandbox, "LIVE_TOOLS", (sys.executable,))

    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str], seconds: int | None = None) -> tuple[int, str, str]:
        del self, workspace, argv, seconds
        return 0, "isolation-ok\n", ""
    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)

    def install(mode: str) -> Path:
        executable = directory / "claude"
        _ = shutil.copyfile(FAKE, executable)
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        _ = executable.with_name("claude.mode").write_text(mode)
        return executable
    return install


def rows(report: dict[str, object]) -> dict[str, dict[str, object]]:
    return {cast(str, row["check"]): row for row in cast(list[dict[str, object]], report["checks"])}


def test_a_ready_host_passes_with_no_model_call(host_bin: Callable[[str], Path]) -> None:
    executable = host_bin("ok")
    report = _doctor.doctor("claude")
    checks = rows(report)
    assert report["ok"] is True and report["live_calls"] == 0
    assert checks["sandbox-probe"]["status"] == "pass" and checks["skill-inventory"]["status"] == "pass"
    assert checks["live-isolation"]["status"] == "needs-live"
    logged = [cast(dict[str, object], json.loads(line))["argv"]
              for line in executable.with_name("claude.log").read_text().splitlines()]
    assert logged == [["--version"]]
    assert executable.with_name("claude.inventory.log").exists()


def _ordered(report: dict[str, object]) -> list[tuple[str, str]]:
    return [(cast(str, row["check"]), cast(str, row["status"])) for row in cast(list[dict[str, object]], report["checks"])]


def test_the_claude_doctor_keeps_the_layer_1_rows_in_order(
        host_bin: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _ = host_bin("ok")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_transport, "nested_userns_blocked", lambda: False)
    assert _ordered(_doctor.doctor("claude")) == [
        ("claude", "pass"), ("tool:bwrap", "pass"), (f"tool:{sys.executable}", "pass"), ("apparmor", "info"),
        ("login", "pass"), ("credential", "pass"), ("sandbox-probe", "pass"), ("skill-inventory", "pass"),
        ("live-isolation", "needs-live")]


def test_an_account_skill_is_reported_as_turned_off(host_bin: Callable[[str], Path]) -> None:
    _ = host_bin("account-skill")
    report = _doctor.doctor("claude")
    assert report["ok"] is True
    assert "turned off ['anthropic-skills:pdf']" in cast(str, rows(report)["skill-inventory"]["detail"])


def test_a_skill_that_cannot_be_turned_off_fails_the_host(host_bin: Callable[[str], Path]) -> None:
    _ = host_bin("plugin-skill")
    report = _doctor.doctor("claude")
    row = rows(report)["skill-inventory"]
    assert report["ok"] is False and row["status"] == "fail" and "plug:tool" in cast(str, row["detail"]) and row["fix"]


def test_every_independent_problem_is_reported_in_one_pass(
        host_bin: Callable[[str], Path], host_login: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = host_bin("ok")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_sandbox, "SANDBOX_HELPERS", ("socat-missing-for-test",))
    monkeypatch.setattr(_sandbox, "LIVE_TOOLS", ("/nonexistent/curl",))
    host_login.unlink()
    report = _doctor.doctor("claude")
    failed = {name for name, row in rows(report).items() if row["status"] == "fail"}
    assert report["ok"] is False
    assert failed == {"tool:socat-missing-for-test", "tool:/nonexistent/curl", "login", "credential"}
    assert all(rows(report)[name].get("fix") for name in failed)


def test_a_missing_tool_does_not_hide_the_skill_inventory(
        host_bin: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _ = host_bin("ok")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_sandbox, "SANDBOX_HELPERS", ("socat-missing-for-test",))
    checks = rows(_doctor.doctor("claude"))
    assert checks["tool:socat-missing-for-test"]["status"] == "fail"
    assert checks["skill-inventory"]["status"] == "pass" and "sandbox-probe" not in checks


@pytest.mark.ac("gh-332:AC-1")
def test_a_tool_row_detail_says_whether_the_tool_was_found(
        host_bin: Callable[[str], Path], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    _ = host_bin("ok")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_sandbox, "SANDBOX_HELPERS", ("socat-missing-for-test",))
    monkeypatch.setattr(_sandbox, "LIVE_TOOLS", (sys.executable, "/nonexistent/curl"))
    assert main(["doctor", "--harness", "claude"]) == 0
    report = cast(dict[str, object], json.loads(capsys.readouterr().out))
    checks = rows(report)
    assert report["ok"] is False
    assert checks["tool:bwrap"]["status"] == "pass" and checks["tool:bwrap"]["detail"] == "`bwrap` found"
    assert checks[f"tool:{sys.executable}"]["detail"] == f"`{sys.executable}` found"
    assert checks["tool:socat-missing-for-test"]["status"] == "fail"
    assert checks["tool:socat-missing-for-test"]["detail"] == "`socat-missing-for-test` not found on PATH"
    assert checks["tool:/nonexistent/curl"]["status"] == "fail"
    assert checks["tool:/nonexistent/curl"]["detail"] == "`/nonexistent/curl` not found"


def test_a_failed_probe_names_its_fix_and_leaves_the_inventory_row(
        host_bin: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _ = host_bin("ok")

    def leaking(self: ClaudeCode, workspace: Path, argv: list[str], seconds: int | None = None) -> tuple[int, str, str]:
        del self, workspace, argv, seconds
        return 0, "leaked\n", ""
    monkeypatch.setattr(ClaudeCode, "sandbox", leaking)
    checks = rows(_doctor.doctor("claude"))
    assert checks["sandbox-probe"]["status"] == "fail" and checks["sandbox-probe"]["fix"]
    assert checks["skill-inventory"]["status"] == "pass"


# The stderr of bwrap 0.13 when the kernel or an outer sandbox refuses a new user namespace (#582).
USERNS_STDERR = ("bwrap: No permissions to create a new namespace, likely because the kernel does not allow "
                 "non-privileged user namespaces.")
FULL_RANGE = "         0          0 4294967295\n"


@pytest.mark.parametrize(("uid_map", "limit", "apparmor", "case"), [
    pytest.param("         0       1000          1\n", "0\n", None, "nested",
                 marks=pytest.mark.ac("gh-582:AC-1"), id="nested-sandbox"),
    pytest.param("         0       1000          1\n", "243190\n", None, "nested",
                 marks=pytest.mark.ac("gh-582:AC-1"), id="nested-userns-only"),
    pytest.param(FULL_RANGE, "0\n", "1\n", "nested", marks=pytest.mark.ac("gh-582:AC-1"), id="zero-limit"),
    pytest.param(FULL_RANGE, "243190\n", "1\n", "apparmor", marks=pytest.mark.ac("gh-582:AC-2"), id="ubuntu-restricted"),
    pytest.param(FULL_RANGE, "243190\n", "0\n", "other", marks=pytest.mark.ac("gh-582:AC-3"), id="apparmor-off"),
    pytest.param(FULL_RANGE, "243190\n", None, "other", marks=pytest.mark.ac("gh-582:AC-3"), id="no-apparmor"),
    pytest.param(None, None, None, "other", marks=pytest.mark.ac("gh-582:AC-3"), id="unreadable-proc"),
])
def test_a_userns_probe_failure_names_the_fix_for_the_proc_state(
        host_bin: Callable[[str], Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        uid_map: str | None, limit: str | None, apparmor: str | None, case: str) -> None:
    _ = host_bin("ok")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ClaudeCode, "sandbox", REAL_SANDBOX)

    def refused(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", USERNS_STDERR)
    monkeypatch.setattr(_transport, "sandboxed", refused)
    proc = tmp_path / "proc"
    files = {"self/uid_map": uid_map, "sys/user/max_user_namespaces": limit,
             "sys/kernel/apparmor_restrict_unprivileged_userns": apparmor}
    for relative, text in files.items():
        if text is not None:
            (proc / relative).parent.mkdir(parents=True, exist_ok=True)
            _ = (proc / relative).write_text(text)
    monkeypatch.setattr(_sandbox, "PROC", proc)
    row = rows(_doctor.doctor("claude"))["sandbox-probe"]
    fix = cast(str, row["fix"])
    assert row["status"] == "fail" and "No permissions to create a new namespace" in cast(str, row["detail"])
    assert ("another sandbox" in fix and "a normal shell" in fix and "sandbox.excludedCommands" in fix) == (case == "nested")
    assert ("apparmor" in fix.lower()) == (case == "apparmor")
    if case == "apparmor":
        assert fix.startswith("add an AppArmor profile for `/usr/bin/bwrap`")
        assert fix.index("userns,") < fix.index("sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0")


def test_a_credential_notice_at_close_is_info_and_another_close_error_fails_with_a_fix(
        host_bin: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _ = host_bin("ok")

    def closing(code: str) -> Callable[[ClaudeCode], None]:
        def close(self: ClaudeCode) -> None:
            del self
            raise CodedError(code, "the login changed")
        return close
    monkeypatch.setattr(ClaudeCode, "close", closing("credential-refreshed"))
    notice = _doctor.doctor("claude")
    monkeypatch.setattr(ClaudeCode, "close", closing("credential-changed"))
    broken = _doctor.doctor("claude")
    assert notice["ok"] is True and rows(notice)["login-close"]["status"] == "info"
    assert broken["ok"] is False and rows(broken)["login-close"]["fix"]


def test_a_setup_error_that_is_not_coded_is_labelled_claude_setup(
        host_bin: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _ = host_bin("ok")

    def broken(*args: object, **kwargs: object) -> ClaudeCode:
        del args, kwargs
        raise OSError("no space left")
    monkeypatch.setattr(_transport, "ClaudeCode", broken)
    report = _doctor.doctor("claude")
    row = rows(report)["claude-setup"]
    assert report["ok"] is False and row["status"] == "fail" and "TMPDIR" in cast(str, row["fix"])
    assert "login" not in rows(report)


def test_the_credential_row_names_the_subscription_login_and_needs_no_api_key(
        host_bin: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _ = host_bin("ok")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")  # macOS reads the Keychain, which holds no login file to link
    report = _doctor.doctor("claude")
    assert report["ok"] is True
    assert rows(report)["credential"]["status"] == "pass" and rows(report)["credential"]["detail"] == "subscription login"


def test_a_missing_login_reports_the_credential_kind_missing(
        host_bin: Callable[[str], Path], host_login: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = host_bin("ok")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-login")
    host_login.unlink()
    row = rows(_doctor.doctor("claude"))["credential"]
    assert row["status"] == "fail" and row["detail"] == "missing" and row["fix"]


@pytest.mark.parametrize("kind, ok", [("subscription login", True), ("missing", False)])
def test_the_codex_doctor_reports_the_credential_kind(monkeypatch: pytest.MonkeyPatch, kind: str, ok: bool) -> None:
    class FakeCodex:
        credential_kind: str = kind

        def __init__(self, *args: object) -> None:
            del args

        def preflight(self) -> dict[str, object]:
            return {}

        def close(self) -> None:
            pass
    monkeypatch.setattr(_codex, "Codex", FakeCodex)
    report = _doctor.doctor("codex")
    assert report["ok"] is ok and rows(report)["credential"]["detail"] == kind
    assert _ordered(report) == [("codex-preflight", "pass"), ("credential", "pass" if ok else "fail")]


def test_the_doctor_accepts_no_isolation_option(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor", "--isolation", "claude"]) != 0
    assert "isolation" in capsys.readouterr().err


def test_a_missing_claude_executable_fails_with_its_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    report = _doctor.doctor("claude")
    assert report["ok"] is False and rows(report)["claude"]["fix"]


def test_a_blocked_nested_namespace_is_explained(host_bin: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _ = host_bin("ok")
    monkeypatch.setattr(_transport, "nested_userns_blocked", lambda: True)
    report = _doctor.doctor("claude")
    assert report["ok"] is True
    assert "allowAllUnixSockets" in cast(str, rows(report)["nested-namespace"]["detail"])


def test_a_failed_host_check_stops_a_fresh_run_before_cases_missing(
        tmp_path: Path, make_target: Callable[..., Path], monkeypatch: pytest.MonkeyPatch) -> None:
    report: dict[str, object] = {"ok": False, "harness": "claude", "checks": [{"check": "login", "status": "fail", "detail": "x"}],
              "live_calls": 0}
    asked: list[str] = []

    def failing(selections: Sequence[tuple[str, str | None]]) -> dict[str, object]:
        asked.extend(harness for harness, _ in selections)
        return report
    monkeypatch.setattr(_run, "host_report", failing)
    with pytest.raises(Stop) as stopped:
        _ = run(make_target(tmp_path), tmp_path / "out", "m", live=True)
    assert stopped.value.code == "host-not-ready"
    assert stopped.value.data["checks"] == report["checks"]
    assert asked == ["claude"]

def test_a_ready_host_reaches_cases_missing_with_the_report(
        tmp_path: Path, make_target: Callable[..., Path]) -> None:
    with pytest.raises(Stop) as stopped:
        _ = run(make_target(tmp_path), tmp_path / "out", "m", live=True)
    assert stopped.value.code == "cases-missing"
    assert cast(dict[str, object], stopped.value.data["doctor"])["ok"] is True


def test_the_doctor_command_prints_the_report(host_bin: Callable[[str], Path], capsys: pytest.CaptureFixture[str]) -> None:
    _ = host_bin("ok")
    assert main(["doctor", "--harness", "claude"]) == 0
    report = cast(dict[str, object], json.loads(capsys.readouterr().out))
    assert report["ok"] is True and report["live_calls"] == 0


def test_a_claude_credential_is_unverified_when_the_login_has_no_file() -> None:
    transport = object.__new__(ClaudeCode)
    transport.credential = None
    assert transport.credential_kind == "unverified (macOS Keychain)"


def test_a_missing_claude_executable_still_reports_the_credential_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    row = rows(_doctor.doctor("claude"))["credential"]
    assert row["detail"] == "subscription login" and row["status"] == "pass"


@pytest.mark.parametrize("failure", ["setup", "preflight"])
def test_an_early_codex_failure_still_reports_the_credential_kind(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    class BrokenCodex:
        credential_kind: str = "api key"

        def __init__(self, *args: object) -> None:
            del args
            if failure == "setup":
                raise OSError("no codex")

        def preflight(self) -> dict[str, object]:
            raise CodedError("login-missing", "no login")

        def close(self) -> None:
            pass
    monkeypatch.setattr(_codex, "Codex", BrokenCodex)
    def api_key(home: Path) -> str:
        del home
        return "api key"
    monkeypatch.setattr(_codex, "credential_kind", api_key)
    report = _doctor.doctor("codex")
    row = rows(report)["credential"]
    assert report["ok"] is False and str(row["detail"]).startswith("api key") and row["status"] == "info"


def test_a_mixed_claude_and_codex_selection_gives_one_flat_prefixed_checks_list(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(harness: str, executable: str | None = None) -> dict[str, object]:
        del executable
        return {"ok": harness == "claude", "harness": harness, "live_calls": 0,
                "checks": [{"check": "credential", "status": "pass" if harness == "claude" else "fail", "detail": harness}]}
    monkeypatch.setattr(_doctor, "doctor", fake)
    report = _doctor.host_report([("claude", "/bin/claude"), ("codex", None)])
    assert report["ok"] is False and report["harness"] == "claude+codex"
    assert [row["check"] for row in cast(list[dict[str, object]], report["checks"])] == ["claude:credential", "codex:credential"]
    assert "reports" not in report


def test_two_claude_executables_prefix_their_checks_with_the_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(harness: str, executable: str | None = None) -> dict[str, object]:
        del harness, executable
        return {"ok": True, "harness": "claude", "live_calls": 0, "checks": [{"check": "claude", "status": "pass", "detail": "v"}]}
    monkeypatch.setattr(_doctor, "doctor", fake)
    report = _doctor.host_report([("claude", "/a/claude"), ("claude", "/b/claude")])
    assert [row["check"] for row in cast(list[dict[str, object]], report["checks"])] == [
        "claude(/a/claude):claude", "claude(/b/claude):claude"]


@pytest.mark.ac("gh-295:AC-5")
def test_a_broken_claude_shim_fails_the_claude_row_through_the_run_executable_and_environment(
        host_bin: Callable[[str], Path]) -> None:
    executable = host_bin("version-broken")
    report = _doctor.doctor("claude")
    row = rows(report)["claude"]
    assert report["ok"] is False and row["status"] == "fail" and row["fix"]
    assert "exited 3" in cast(str, row["detail"]) and "broken shim" in cast(str, row["detail"])
    first = cast(dict[str, object], json.loads(executable.with_name("claude.log").read_text().splitlines()[0]))
    environment = cast(dict[str, str], first["environment"])
    assert first["argv"] == ["--version"]
    assert environment["PATH"].startswith(f"{executable.parent}:")
    assert environment["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "0" and environment["CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"] == "1"

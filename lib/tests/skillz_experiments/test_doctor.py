"""The free host checks: one pass, no model call, before the first question."""
from __future__ import annotations

import json
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments import _claude, _doctor, _workflow
from skillz_experiments._claude import ClaudeCode
from skillz_experiments._cli import main
from skillz_experiments._workflow import Stop, run

FAKE = Path(__file__).parent / "fixtures/fake_claude.py"
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
    monkeypatch.setattr(_doctor, "LIVE_TOOLS", (sys.executable,))

    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str]) -> tuple[int, str]:
        del self, workspace, argv
        return 0, "isolation-ok\n"
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
    assert checks["sandbox-and-skills"]["status"] == "pass"
    assert checks["live-isolation"]["status"] == "needs-live"
    logged = [cast(dict[str, object], json.loads(line))["argv"]
              for line in executable.with_name("claude.log").read_text().splitlines()]
    assert logged == [["--version"]]
    assert executable.with_name("claude.inventory.log").exists()


def test_an_account_skill_is_reported_as_turned_off(host_bin: Callable[[str], Path]) -> None:
    _ = host_bin("account-skill")
    report = _doctor.doctor("claude")
    assert report["ok"] is True
    assert "turned off ['anthropic-skills:pdf']" in cast(str, rows(report)["sandbox-and-skills"]["detail"])


def test_a_skill_that_cannot_be_turned_off_fails_the_host(host_bin: Callable[[str], Path]) -> None:
    _ = host_bin("plugin-skill")
    report = _doctor.doctor("claude")
    row = rows(report)["sandbox-and-skills"]
    assert report["ok"] is False and row["status"] == "fail" and "plug:tool" in cast(str, row["detail"])


def test_every_independent_problem_is_reported_in_one_pass(
        host_bin: Callable[[str], Path], host_login: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = host_bin("ok")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_claude, "SANDBOX_HELPERS", ("socat-missing-for-test",))
    monkeypatch.setattr(_doctor, "LIVE_TOOLS", ("/nonexistent/curl",))
    host_login.unlink()
    report = _doctor.doctor("claude")
    failed = {name for name, row in rows(report).items() if row["status"] == "fail"}
    assert report["ok"] is False
    assert failed == {"tool:socat-missing-for-test", "tool:/nonexistent/curl", "login"}
    assert all(rows(report)[name].get("fix") for name in failed)


def test_a_missing_claude_executable_fails_with_its_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    report = _doctor.doctor("claude")
    assert report["ok"] is False and rows(report)["claude"]["fix"]


def test_a_blocked_nested_namespace_is_explained(host_bin: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _ = host_bin("ok")
    monkeypatch.setattr(_claude, "nested_userns_blocked", lambda: True)
    report = _doctor.doctor("claude")
    assert report["ok"] is True
    assert "allowAllUnixSockets" in cast(str, rows(report)["nested-namespace"]["detail"])


def test_a_failed_host_check_stops_a_fresh_run_before_cases_missing(
        tmp_path: Path, make_target: Callable[..., Path], monkeypatch: pytest.MonkeyPatch) -> None:
    report: dict[str, object] = {"ok": False, "harness": "claude", "checks": [{"check": "login", "status": "fail", "detail": "x"}],
              "live_calls": 0}

    def failing(harness: str, isolation: str = "claude") -> dict[str, object]:
        del harness, isolation
        return report
    monkeypatch.setattr(_workflow, "doctor", failing)
    with pytest.raises(Stop) as stopped:
        _ = run(make_target(tmp_path), tmp_path / "out", "m", live=True)
    assert stopped.value.code == "host-not-ready"
    assert stopped.value.data["checks"] == report["checks"]


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

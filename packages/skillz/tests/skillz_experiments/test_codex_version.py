"""Codex needs a minimum version, records the observed one, and stops a resume on another (F-1, AC-1 to AC-4)."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments.harness import API_KEY, MISSING_CREDENTIAL, SUBSCRIPTION_LOGIN, Configuration, Harness, _codex
from skillz_experiments.harness._codex import Codex, supported
from skillz_experiments._runtime import Budget


@pytest.fixture(autouse=True)
def _no_admin_roots() -> None:
    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")


def fake_codex(tmp_path: Path, banner: str) -> Path:
    log = tmp_path / "calls.log"
    fake = tmp_path / "bin/codex"
    fake.parent.mkdir(exist_ok=True)
    _ = fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\n[ "$1" = --version ] && {{ echo "{banner}"; exit 0; }}\nexit 1\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake


def adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, banner: str,
            login: dict[str, object] | None = None) -> Codex:
    fake = fake_codex(tmp_path, banner)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "host"))
    (tmp_path / "host").mkdir(exist_ok=True)
    _ = (tmp_path / "host/auth.json").write_text(json.dumps({"tokens": {"id_token": "t"}} if login is None else login))
    return Codex("m", Budget(10, 120, 0), lambda: None)


@pytest.mark.parametrize("banner, ok", [
    ("codex-cli 0.154.0", True), ("codex-cli 0.160.2", True), ("codex-cli 1.0.0", True),
    ("codex-cli 0.155.0-alpha.1", True), ("codex-cli 0.154.0\n", True),
    ("codex-cli 0.153.9", False), ("codex-cli 0.154.0-alpha.1", False), ("codex-cli 0.9.0", False),
    ("codex 0.200.0", False), ("", False), ("codex-cli latest", False), ("codex-cli 0.154", False),
])
def test_minimum_version_parsing(banner: str, ok: bool) -> None:
    assert supported(banner) is ok


@pytest.mark.parametrize("banner", ["codex-cli 0.153.9", "garbled output"])
def test_an_old_or_unparsable_version_stops_before_any_probe(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, banner: str) -> None:
    codex = adapter(tmp_path, monkeypatch, banner)
    try:
        with pytest.raises(CodedError, match="0.154.0") as caught:
            _ = codex.preflight()
    finally:
        codex.close()
    assert caught.value.code == "codex-version-unsupported"
    assert (tmp_path / "calls.log").read_text().splitlines() == ["--version"]


def test_a_newer_version_passes_the_version_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex = adapter(tmp_path, monkeypatch, "codex-cli 0.160.0")
    try:
        with pytest.raises(RuntimeError, match="isolation preflight fails"):
            _ = codex.preflight()
    finally:
        codex.close()
    assert (tmp_path / "calls.log").read_text().splitlines()[0] == "--version"
    assert len((tmp_path / "calls.log").read_text().splitlines()) > 1


def test_preflight_evidence_records_the_observed_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex = adapter(tmp_path, monkeypatch, "codex-cli 0.160.0")
    def probe(*_args: object) -> str:
        return "print('isolation-ok')"

    def discovered(*_args: object) -> bool:
        return True

    monkeypatch.setattr(_codex, "probe", probe)
    monkeypatch.setattr(Codex, "_discover", discovered)
    fake = tmp_path / "bin/codex"
    _ = fake.write_text('#!/bin/sh\n[ "$1" = --version ] && echo "codex-cli 0.160.0"\n[ "$1" = sandbox ] && echo isolation-ok\nexit 0\n')
    try:
        evidence = codex.preflight()
    finally:
        codex.close()
    assert evidence["codex_version"] == "codex-cli 0.160.0"


def test_exec_and_app_server_run_with_strict_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex = adapter(tmp_path, monkeypatch, "codex-cli 0.160.0")
    seen: list[list[str]] = []
    def discovered(command: list[str], *_rest: object) -> bool:
        seen.append(command)
        return True

    monkeypatch.setattr(_codex, "discover", discovered)
    try:
        execution = codex._execution_command(tmp_path, tmp_path / "s.json", tmp_path / "o.json")  # pyright: ignore[reportPrivateUsage]
        assert codex._discover(tmp_path, "skillz")  # pyright: ignore[reportPrivateUsage]
    finally:
        codex.close()
    assert execution[1:3] == ["exec", "--strict-config"]
    assert seen[0][1:3] == ["app-server", "--strict-config"]


def harness(monkeypatch: pytest.MonkeyPatch, observed: str) -> Harness:
    def preflight(self: Codex) -> dict[str, object]:
        return {"codex_version": observed, "model": "m", "isolation": "passed", "credential": self.credential_kind,
                "live_calls": 0}

    monkeypatch.setattr(Codex, "preflight", preflight)
    return Configuration.single("codex", "m").create("m", Budget(10, 120, 0), lambda: None)


def recorded_from(monkeypatch: pytest.MonkeyPatch, observed: str) -> dict[str, object]:
    session = harness(monkeypatch, observed)
    try:
        return session.preflight()
    finally:
        session.close()


def test_a_resume_on_the_recorded_version_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = recorded_from(monkeypatch, "codex-cli 0.160.0")
    session = harness(monkeypatch, "codex-cli 0.160.0")
    try:
        again = session.preflight(recorded)
    finally:
        session.close()
    assert again["environment_hash"] == recorded["environment_hash"]


def test_another_version_changes_the_environment_hash_and_the_harness_reports_it(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = recorded_from(monkeypatch, "codex-cli 0.160.0")
    session = harness(monkeypatch, "codex-cli 0.161.0")
    try:
        again = session.preflight(recorded)
    finally:
        session.close()
    assert again["environment_hash"] != recorded["environment_hash"]
    assert session.codex_version == "codex-cli 0.161.0"


@pytest.mark.parametrize("login, kind", [
    ({"tokens": {"id_token": "t", "access_token": "a"}, "OPENAI_API_KEY": None}, SUBSCRIPTION_LOGIN),
    ({"OPENAI_API_KEY": "sk-secret"}, API_KEY),
    ({"OPENAI_API_KEY": None, "tokens": {}}, MISSING_CREDENTIAL),
    ({}, MISSING_CREDENTIAL),
])
def test_the_credential_kind_follows_the_field_names_of_auth_json(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, login: dict[str, object], kind: str) -> None:
    codex = adapter(tmp_path, monkeypatch, "codex-cli 0.160.0", login)
    try:
        assert codex.credential_kind == kind
    finally:
        codex.close()


def test_a_missing_login_stops_the_preflight_before_the_version_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex = adapter(tmp_path, monkeypatch, "codex-cli 0.160.0")
    (tmp_path / "host/auth.json").unlink()
    try:
        with pytest.raises(CodedError, match="codex login") as caught:
            _ = codex.preflight()
    finally:
        codex.close()
    assert caught.value.code == "login-missing"
    assert not (tmp_path / "calls.log").exists()

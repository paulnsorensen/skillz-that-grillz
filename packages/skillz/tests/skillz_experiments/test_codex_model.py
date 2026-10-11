"""The Codex transport accepts a minimum version and stops a run at the first rejected model."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments.harness._codex import Codex, supported_version
from skillz_experiments._budget import Budget


@pytest.mark.parametrize("text", ["codex-cli 0.154.0", "codex-cli 0.160.0\n", "codex-cli 0.154.1", "codex-cli 1.0.0"])
def test_the_minimum_and_newer_versions_are_accepted(text: str) -> None:
    assert supported_version(text) == text.strip()


@pytest.mark.parametrize("text", ["codex-cli 0.153.9", "codex-cli 0.99.0", "", "codex 0.160.0", "codex-cli 0.160",
                                  "codex-cli 0.160.0-alpha.1"])
def test_an_older_or_unparseable_version_is_rejected_with_the_minimum(text: str) -> None:
    with pytest.raises(RuntimeError, match=r"unsupported Codex version; expected codex-cli 0\.154\.0 or newer; found " + re.escape(repr(text.strip()))):
        _ = supported_version(text)


def test_the_found_text_in_a_version_error_is_cut_to_80_characters() -> None:
    with pytest.raises(RuntimeError) as caught:
        _ = supported_version("x" * 200)
    assert "x" * 80 in str(caught.value) and "x" * 81 not in str(caught.value)


def test_a_rejected_model_stops_with_model_unsupported_after_one_call(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")
    host = tmp_path / "host-codex"
    host.mkdir()
    _ = (host / "auth.json").write_text("{}")
    log = tmp_path / "codex.log"
    fake = tmp_path / "bin/codex"
    fake.parent.mkdir()
    message = "The 'gpt-6-sol' model is not supported when using Codex with a ChatGPT account."
    _ = fake.write_text("#!/bin/sh\n" + f"echo call >> {log}\n"
                        + "cat >/dev/null\n"
                        + "echo '{\"type\":\"error\",\"message\":\"" + message + "\"}'\n"
                        + "exit 1\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(host))
    adapter = Codex("gpt-6-sol", Budget(10, 120, 0), lambda: None)
    try:
        with pytest.raises(CodedError) as caught:
            _ = adapter.invoke("hello")
    finally:
        adapter.close()
    assert caught.value.code == "model-unsupported"
    assert "Codex rejected the model 'gpt-6-sol' or a feature: " in str(caught.value) and "model is not supported when using Codex" in str(caught.value)
    assert log.read_text().count("call") == 1


@pytest.mark.parametrize("text", ["codex-cli 0.154.0\n", "codex-cli 0.154.0\r\n", "  codex-cli 1.0.0  ", "codex-cli 0.200.0",
                                  "codex-cli 0.1000.0"])
def test_whitespace_around_a_valid_version_is_trimmed_and_numeric_order_is_used(text: str) -> None:
    assert supported_version(text) == text.strip()


@pytest.mark.parametrize("text", ["codex-cli 0.99.999", "codex-cli 0.153.999", "codex-cli 0.154.0 extra", "codex-cli  0.160.0",
                                  "codex-cli v0.160.0", "codex-cli 0.160.0.1", "codex-cli -1.0.0", "\n",
                                  "Codex-CLI 0.160.0", "codex-cli 0.160.0\ncodex-cli 0.160.0"])
def test_a_malformed_or_older_version_is_rejected(text: str) -> None:
    with pytest.raises(RuntimeError, match="unsupported Codex version"):
        _ = supported_version(text)


def test_a_non_ascii_digit_version_is_rejected() -> None:
    """Arabic-Indic digits are not a version the CLI prints; the parser must not read them as 0.154.0."""
    with pytest.raises(RuntimeError, match="unsupported Codex version"):
        _ = supported_version("codex-cli ٠.١٥٤.٠")


def _fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[str]) -> Path:
    host = tmp_path / "host-codex"
    host.mkdir()
    _ = (host / "auth.json").write_text("{}")
    log = tmp_path / "codex.log"
    fake = tmp_path / "bin/codex"
    fake.parent.mkdir()
    body = "#!/bin/sh\n" + f"echo call >> {log}\n" + "cat >/dev/null\n"
    body += "".join(f"echo '{event}'\n" for event in events)
    _ = fake.write_text(body + "exit 1\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(host))
    return log


def _rejected(model: str) -> CodedError:
    adapter = Codex(model, Budget(10, 120, 0), lambda: None)
    try:
        with pytest.raises(CodedError) as caught:
            _ = adapter.invoke("hello")
    finally:
        adapter.close()
    return caught.value


def test_the_provider_message_is_cut_to_300_characters_and_names_the_rejection(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")
    long = "not supported " + "z" * 600
    _ = _fake_codex(tmp_path, monkeypatch, ['{"type":"error","message":"' + long + '"}'])
    error = _rejected("gpt-6-sol")
    assert error.code == "model-unsupported" and "Codex rejected the model 'gpt-6-sol' or a feature: " in str(error)
    assert "z" * 286 in str(error) and "z" * 287 not in str(error)


def test_only_the_first_provider_message_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")
    _ = _fake_codex(tmp_path, monkeypatch, ['{"type":"error","message":"first: model not supported"}',
                                            '{"type":"turn.failed","error":{"message":"second-message"}}'])
    error = _rejected("gpt-6-sol")
    assert "first: model not supported" in str(error) and "second-message" not in str(error)


def test_a_non_matching_first_error_event_is_not_reported_as_the_rejection(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")
    _ = _fake_codex(tmp_path, monkeypatch, ['{"type":"error","message":"warning: unrelated notice"}',
                                            '{"type":"turn.failed","error":{"message":"model not supported here"}}'])
    error = _rejected("gpt-6-sol")
    assert error.code == "model-unsupported"
    assert "model not supported here" in str(error) and "unrelated notice" not in str(error)


def test_model_not_found_maps_to_model_unsupported_after_one_call(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")
    log = _fake_codex(tmp_path, monkeypatch, ['{"type":"turn.failed","error":{"message":"model_not_found: gpt-0"}}'])
    error = _rejected("gpt-0")
    assert error.code == "model-unsupported" and "model_not_found" in str(error)
    assert log.read_text().count("call") == 1


def test_a_rate_limit_failure_is_not_a_model_rejection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")
    _ = _fake_codex(tmp_path, monkeypatch, ['{"type":"error","message":"429 rate limit"}'])
    adapter = Codex("gpt-6-sol", Budget(10, 120, 0), lambda: None)
    try:
        with pytest.raises(RuntimeError, match="rate-limited") as caught:
            _ = adapter.invoke("hello")
    finally:
        adapter.close()
    assert not isinstance(caught.value, CodedError)

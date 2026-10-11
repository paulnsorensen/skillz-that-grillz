"""The Codex transport accepts a minimum version and stops a run at the first rejected model."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._codex import Codex, supported_version
from skillz_experiments._runtime import Budget


@pytest.mark.parametrize("text", ["codex-cli 0.154.0", "codex-cli 0.160.0\n", "codex-cli 0.154.1", "codex-cli 1.0.0"])
def test_the_minimum_and_newer_versions_are_accepted(text: str) -> None:
    assert supported_version(text) == text.strip()


@pytest.mark.parametrize("text", ["codex-cli 0.153.9", "codex-cli 0.99.0", "", "codex 0.160.0", "codex-cli 0.160",
                                  "codex-cli 0.160.0-alpha.1"])
def test_an_older_or_unparseable_version_is_rejected_with_the_minimum(text: str) -> None:
    with pytest.raises(RuntimeError, match=r"unsupported Codex version; expected codex-cli 0\.154\.0"):
        _ = supported_version(text)


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
    assert "gpt-6-sol" in str(caught.value) and "not supported" in str(caught.value)
    assert log.read_text().count("call") == 1

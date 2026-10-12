"""Verify joined CLI runs use private fixture credentials."""
from __future__ import annotations

from pathlib import Path

import pytest

from support import make_scenario, run_scenario


@pytest.mark.ac("skillz-experiments-end-to-end:AC-2")
@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_external_auth_override_does_not_replace_private_home(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: str) -> None:
    claude_config = tmp_path / "external-claude"
    claude_config.mkdir()
    _ = (claude_config / "sentinel.json").write_text('{"sentinel": "external-claude"}')
    codex_home = tmp_path / "external-codex"
    codex_home.mkdir()
    _ = (codex_home / "auth.json").write_text('{"sentinel": "external-codex"}')
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_config))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    scenario = make_scenario(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    result = run_scenario(scenario, home, harness=harness)

    assert result["phase"] == "complete"
    assert (claude_config / "sentinel.json").read_text() == '{"sentinel": "external-claude"}'
    assert (codex_home / "auth.json").read_text() == '{"sentinel": "external-codex"}'

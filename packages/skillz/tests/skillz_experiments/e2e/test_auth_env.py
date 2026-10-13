"""Verify joined CLI runs use private fixture credentials."""
from __future__ import annotations

from pathlib import Path

import pytest

from support import (CLAUDE_CREDENTIAL, CODEX_CREDENTIAL, make_scenario, read_events, run_scenario, snapshot,
                     write_credential)


@pytest.mark.ac("skillz-experiments-end-to-end:AC-2")
@pytest.mark.parametrize("harness, variable, key", [("claude", "CLAUDE_CONFIG_DIR", "config_dir"),
                                                  ("codex", "CODEX_HOME", "codex_home")])
def test_external_auth_override_does_not_replace_private_home(
        tmp_path: Path, harness: str, variable: str, key: str) -> None:
    external = tmp_path / "external"
    claude_config = external / "claude"
    codex_home = external / "codex"
    write_credential(claude_config / ".credentials.json", CLAUDE_CREDENTIAL)
    write_credential(codex_home / "auth.json", CODEX_CREDENTIAL)
    before = snapshot(external)
    scenario = make_scenario(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    overrides = {"CLAUDE_CONFIG_DIR": str(claude_config), "CODEX_HOME": str(codex_home)}

    result = run_scenario(scenario, home, harness=harness, env_overrides=overrides)

    assert result["phase"] == "complete"
    seen = {entry[key] for entry in read_events(scenario.out)}
    assert seen and None not in seen, variable
    assert all(not Path(str(value)).is_relative_to(external) for value in seen), seen
    assert snapshot(external) == before

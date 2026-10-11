"""The agent-session-analytics package references and the skillz copies stay byte-identical."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
PACKAGED = REPO / "packages" / "agent-session-analytics" / "src" / "agent_session_analytics" / "references"
SKILL = REPO / "skills" / "skillz" / "references"
NAMES = ("canonical-schema.md", "harness-coverage.md", "query-conventions.md")


@pytest.mark.parametrize("name", NAMES)
def test_skillz_copy_matches_the_package_reference(name: str) -> None:
    assert (SKILL / name).read_bytes() == (PACKAGED / name).read_bytes()

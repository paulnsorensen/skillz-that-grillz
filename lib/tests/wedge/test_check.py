"""AC-W5: wedge check verifies a skill's lock and launcher without building."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from wedge._lock import check, launcher_path

FIXTURE_NAME = "cheese-cave"


@pytest.mark.ac("AC-W5")
def test_fresh_fixture_passes(fixture_skill_dir: Path) -> None:
    assert check([fixture_skill_dir]) == []


@pytest.mark.ac("AC-W5")
def test_stale_cli_source_fails_with_one_issue(
    tmp_path: Path, copy_locked_fixture: Callable[[Path], Path]
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    source = skill.parent.parent.parent / "fromargs" / "examples" / "cheese_cave.py"
    source.write_text(source.read_text() + "\n# touched\n")

    issues = check([skill])

    assert len(issues) == 1
    assert "stale" in issues[0].reason


@pytest.mark.ac("AC-W5")
def test_hand_edited_launcher_fails(
    tmp_path: Path, copy_locked_fixture: Callable[[Path], Path]
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    launcher = launcher_path(skill, FIXTURE_NAME)
    launcher.write_text(launcher.read_text() + "\n# tampered\n")

    issues = check([skill])

    assert any("launcher" in issue.reason for issue in issues)

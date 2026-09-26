"""AC-W5: wedge check verifies a skill's lock and launcher without building."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, cast

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
    _ = source.write_text(source.read_text() + "\n# touched\n")

    issues = check([skill])

    assert len(issues) == 1
    assert "stale" in issues[0].reason


@pytest.mark.ac("AC-W5")
def test_hand_edited_launcher_fails(
    tmp_path: Path, copy_locked_fixture: Callable[[Path], Path]
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    launcher = launcher_path(skill, FIXTURE_NAME)
    _ = launcher.write_text(launcher.read_text() + "\n# tampered\n")

    issues = check([skill])

    assert any("launcher" in issue.reason for issue in issues)


@pytest.mark.ac("AC-W5")
@pytest.mark.parametrize("mode", [0o644, 0o001])
def test_non_executable_launcher_fails(
    tmp_path: Path, copy_locked_fixture: Callable[[Path], Path], mode: int
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    launcher = launcher_path(skill, FIXTURE_NAME)
    _ = launcher.chmod(mode)

    issues = check([skill])

    assert any("not executable" in issue.reason for issue in issues)


@pytest.mark.ac("AC-W5")
@pytest.mark.parametrize(
    "field,value",
    [("name", "'../unsafe'"), ("entry", "1"), ("source", "false"), ("repo", "42")],
)
def test_malformed_config_fields_are_rejected(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    field: str,
    value: str,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    config = skill / "wedge.toml"
    valid = (
        'name = "cheese-cave"\n'
        'entry = "cheese_cave:main"\n'
        'source = "../../../fromargs/examples/cheese_cave.py"\n'
        'repo = "paulnsorensen/skillz-that-grillz"\n'
    )
    _ = config.write_text(
        valid.replace(
            next(line for line in valid.splitlines() if line.startswith(f"{field} =")),
            f"{field} = {value}",
        )
        + "\n"
    )

    issues = check([skill])

    assert len(issues) == 1
    assert "must be" in issues[0].reason or "safe" in issues[0].reason


@pytest.mark.ac("AC-W5")
@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "other-name"),
        ("asset", "other.pyz"),
        ("format", 5),
        ("repo", "other-owner/other-repo"),
        ("key", "not-a-digest"),
    ],
)
def test_invalid_lock_metadata_is_rejected(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    field: str,
    value: object,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    lock_file = skill / "scripts" / f"{FIXTURE_NAME}.wedge.json"
    lock_data = cast(dict[str, object], json.loads(lock_file.read_text()))
    lock_data[field] = value
    _ = lock_file.write_text(json.dumps(lock_data))

    issues = check([skill])

    assert len(issues) == 1
    assert "invalid lock" in issues[0].reason or "repository" in issues[0].reason

"""AC-W9: the CLI's directory resolution rejects a contradictory or bad command."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import fromargs
import pytest

from wedge._cli import check_cmd, lock_cmd

SKILLS = Path("skills")


@pytest.mark.ac("AC-W9")
def test_positional_dirs_and_root_are_exclusive(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)

    with pytest.raises(fromargs.CliError, match="exclusive"):
        _ = check_cmd(skill_dir=[str(SKILLS / "hello")], root=[str(SKILLS)])


@pytest.mark.ac("AC-W9")
def test_a_missing_root_is_an_error_even_when_another_root_finds_skills(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)

    with pytest.raises(fromargs.CliError, match="skills-typo is not a directory"):
        _ = check_cmd(root=[str(SKILLS), "skills-typo"])


@pytest.mark.ac("AC-W9")
def test_duplicate_skill_directories_collapse_to_one(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)
    hello = SKILLS / "hello"

    checked = check_cmd(skill_dir=[str(hello), str(consumer / hello)])

    assert checked["checked"] == [str(hello)]


@pytest.mark.ac("AC-W9")
@pytest.mark.parametrize("jobs", [0, -1])
def test_jobs_below_one_is_an_error(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch, jobs: int
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    monkeypatch.chdir(consumer)

    with pytest.raises(fromargs.CliError, match="--jobs must be at least 1"):
        _ = lock_cmd(root=[str(SKILLS)], jobs=jobs)

"""AC-W10: a ``wedge.toml`` in a discovery root supplies defaults for every skill beside it."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from wedge._config import ConfigError, load_config
from wedge._key import compute_key
from wedge._lock import check, lock

SKILLS = Path("skills")


def _split_defaults(consumer: Path) -> Path:
    """Move ``hello``'s ``project`` and ``repo`` into ``skills/wedge.toml``."""
    skill = consumer / SKILLS / "hello"
    lines = (skill / "wedge.toml").read_text().splitlines()
    own = [line for line in lines if line.startswith(("name =", "entry =", "source ="))]
    shared = [line for line in lines if line.startswith(("project =", "repo ="))]
    assert len(own) == 3 and len(shared) == 2, lines
    _ = (skill / "wedge.toml").write_text("\n".join(own) + "\n")
    _ = (consumer / SKILLS / "wedge.toml").write_text("\n".join(shared) + "\n")
    return skill


@pytest.mark.ac("AC-W10")
def test_shared_defaults_fill_in_what_a_skill_omits(
    tmp_path: Path, copy_consumer: Callable[[Path], Path]
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    before = load_config(consumer / SKILLS / "hello")

    skill = _split_defaults(consumer)

    assert load_config(skill) == before


@pytest.mark.ac("AC-W10")
def test_a_skill_overrides_a_shared_default(
    tmp_path: Path, copy_consumer: Callable[[Path], Path]
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    skill = _split_defaults(consumer)
    own = skill / "wedge.toml"
    _ = own.write_text(own.read_text() + 'repo = "other-owner/other-repo"\n')

    assert load_config(skill).repo == "other-owner/other-repo"


@pytest.mark.ac("AC-W10")
def test_shared_defaults_may_not_name_a_skill(
    tmp_path: Path, copy_consumer: Callable[[Path], Path]
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    _ = (consumer / SKILLS / "wedge.toml").write_text('name = "hello"\nentry = "hello:main"\n')

    with pytest.raises(ConfigError, match="must not set entry, name"):
        _ = load_config(consumer / SKILLS / "hello")


@pytest.mark.ac("AC-W10")
def test_shared_defaults_are_part_of_the_key(
    tmp_path: Path, copy_consumer: Callable[[Path], Path]
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    skill = _split_defaults(consumer)
    # The committed lock predates the defaults file, so the key moved.
    assert any("stale" in issue.reason for issue in check([skill]))

    locked = lock(skill)

    assert check([skill]) == []
    shared = consumer / SKILLS / "wedge.toml"
    _ = shared.write_text(shared.read_text() + "# a comment changes the digest\n")
    assert compute_key(skill, load_config(skill)) != locked.key

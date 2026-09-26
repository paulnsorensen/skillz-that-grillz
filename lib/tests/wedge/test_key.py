"""AC-W1: the content key is stable across checkout paths and reacts only to
build-input bytes. AC-W9: a bad ``wedge.toml`` layout is a config error."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from wedge._config import ConfigError, load_config
from wedge._key import compute_key
from wedge._resolve import export_requirements, parse_requirements


def _key(skill: Path) -> str:
    return compute_key(skill, load_config(skill))


@pytest.mark.ac("AC-W1")
def test_key_is_stable_across_checkout_paths(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill_a = copy_repo_subset(tmp_path / "checkout-a")
    skill_b = copy_repo_subset(tmp_path / "checkout-b")
    assert _key(skill_a) == _key(skill_b)


@pytest.mark.ac("AC-W1")
@pytest.mark.parametrize(
    "relative",
    [
        "lib/fromargs/examples/cheese_cave.py",
        "lib/fromargs/src/fromargs/_errors.py",
        "lib/uv.lock",
        "lib/examples/skills/cheese-cave/wedge.toml",
    ],
)
def test_key_changes_with_each_build_input(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path], relative: str
) -> None:
    root = tmp_path / "checkout"
    skill = copy_repo_subset(root)
    before = _key(skill)
    target = root / relative
    target.write_text(target.read_text() + "\n# touched\n")
    assert _key(skill) != before


@pytest.mark.ac("AC-W1")
def test_key_is_unchanged_by_pycache(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    root = tmp_path / "checkout"
    skill = copy_repo_subset(root)
    before = _key(skill)
    cache_dir = root / "lib" / "fromargs" / "src" / "fromargs" / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "_errors.cpython-313.pyc").write_bytes(b"garbage")
    assert _key(skill) == before


@pytest.mark.ac("AC-W1")
def test_export_excludes_local_fromargs_but_keeps_transitive_dependencies(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    root = tmp_path / "checkout"
    copy_repo_subset(root)

    requirements = export_requirements(root / "lib")
    names = {name for name, _version, _marker in parse_requirements(requirements)}

    assert "fromargs" not in names
    assert "shiv" not in names
    assert {"cyclopts", "attrs", "docstring-parser"} <= names


@pytest.mark.ac("AC-W1")
def test_key_ignores_files_outside_the_configured_inputs(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    root = tmp_path / "checkout"
    skill = copy_repo_subset(root)
    before = _key(skill)
    (root / "lib" / "unrelated.py").write_text("print('not bundled')\n")
    assert _key(skill) == before


@pytest.mark.ac("AC-W9")
@pytest.mark.parametrize(
    ("line", "message"),
    [
        ('include = ["../../../../outside"]', "inside the project"),
        ('include = ["../../../fromargs/src/missing"]', "does not exist"),
        ('include = ["../../../fromargs/examples/cheese_cave.py"]', "share top-level names"),
    ],
)
def test_bad_include_is_a_config_error(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path], line: str, message: str
) -> None:
    root = tmp_path / "checkout"
    skill = copy_repo_subset(root)
    (root / "outside").mkdir()
    toml = skill / "wedge.toml"
    kept = [entry for entry in toml.read_text().splitlines() if not entry.startswith("include")]
    toml.write_text("\n".join([*kept, line]) + "\n")
    with pytest.raises(ConfigError, match=message):
        _key(skill)


@pytest.mark.ac("AC-W9")
def test_project_without_uv_lock_is_a_config_error(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    root = tmp_path / "checkout"
    skill = copy_repo_subset(root)
    (root / "lib" / "uv.lock").unlink()
    with pytest.raises(ConfigError, match="has no uv.lock"):
        _key(skill)


@pytest.mark.ac("AC-W9")
@pytest.mark.parametrize(
    ("repo_line", "message"),
    [(None, "missing required key 'repo'"), ('repo = "not-a-slug"', "owner/name")],
)
def test_repo_is_required_and_validated(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path], repo_line: str | None, message: str
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    toml = skill / "wedge.toml"
    kept = [entry for entry in toml.read_text().splitlines() if not entry.startswith("repo")]
    toml.write_text("\n".join(kept + ([repo_line] if repo_line else [])) + "\n")
    with pytest.raises(ConfigError, match=message):
        load_config(skill)

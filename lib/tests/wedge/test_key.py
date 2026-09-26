"""AC-W1: the content key is stable across checkout paths and reacts only to
build-input bytes."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from wedge._config import load_config
from wedge._key import compute_key, find_repo_root
from wedge._resolve import export_requirements, parse_requirements


@pytest.mark.ac("AC-W1")
def test_key_is_stable_across_checkout_paths(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill_a = copy_repo_subset(tmp_path / "checkout-a")
    skill_b = copy_repo_subset(tmp_path / "checkout-b")
    key_a = compute_key(skill_a, load_config(skill_a), find_repo_root(skill_a))
    key_b = compute_key(skill_b, load_config(skill_b), find_repo_root(skill_b))
    assert key_a == key_b


@pytest.mark.ac("AC-W1")
def test_key_changes_with_cli_source(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    config = load_config(skill)
    root = find_repo_root(skill)
    before = compute_key(skill, config, root)
    source = root / "lib" / "fromargs" / "examples" / "cheese_cave.py"
    source.write_text(source.read_text() + "\n# touched\n")
    after = compute_key(skill, config, root)
    assert before != after


@pytest.mark.ac("AC-W1")
def test_key_changes_with_a_fromargs_file(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    config = load_config(skill)
    root = find_repo_root(skill)
    before = compute_key(skill, config, root)
    target = root / "lib" / "fromargs" / "src" / "fromargs" / "_errors.py"
    target.write_text(target.read_text() + "\n# touched\n")
    after = compute_key(skill, config, root)
    assert before != after


@pytest.mark.ac("AC-W1")
def test_key_changes_with_uv_lock(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    config = load_config(skill)
    root = find_repo_root(skill)
    before = compute_key(skill, config, root)
    lock = root / "lib" / "fromargs" / "uv.lock"
    lock.write_text(lock.read_text() + "\n# touched\n")
    after = compute_key(skill, config, root)
    assert before != after


@pytest.mark.ac("AC-W1")
def test_key_changes_with_wedge_toml(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    config = load_config(skill)
    root = find_repo_root(skill)
    before = compute_key(skill, config, root)
    toml = skill / "wedge.toml"
    toml.write_text(toml.read_text() + "\n# touched\n")
    after = compute_key(skill, config, root)
    assert before != after


@pytest.mark.ac("AC-W1")
def test_key_is_unchanged_by_pycache(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    config = load_config(skill)
    root = find_repo_root(skill)
    before = compute_key(skill, config, root)
    cache_dir = root / "lib" / "fromargs" / "src" / "fromargs" / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "_errors.cpython-313.pyc").write_bytes(b"garbage")
    after = compute_key(skill, config, root)
    assert before == after


@pytest.mark.ac("AC-W1")
def test_export_is_the_fromargs_closure_without_builder_dependencies(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    root = find_repo_root(skill)

    requirements = export_requirements(root)
    names = {name for name, _version, _marker in parse_requirements(requirements)}

    assert "fromargs" not in names
    assert not {"shiv", "pip", "setuptools", "click"} & names
    assert {"cyclopts", "attrs", "docstring-parser"} <= names

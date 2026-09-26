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
    _ = source.write_text(source.read_text() + "\n# touched\n")
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
    _ = target.write_text(target.read_text() + "\n# touched\n")
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
    _ = lock.write_text(lock.read_text() + "\n# touched\n")
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
    _ = toml.write_text(toml.read_text() + "\n# touched\n")
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
    _ = cache_dir.mkdir()
    _ = (cache_dir / "_errors.cpython-313.pyc").write_bytes(b"garbage")
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


@pytest.mark.ac("AC-W1")
def test_key_rejects_symlink_source_file(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    source = skill.parent.parent.parent / "fromargs" / "examples" / "cheese_cave.py"
    target = source.with_name("real_source.py")
    _ = source.rename(target)
    _ = source.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        _ = compute_key(skill, load_config(skill), find_repo_root(skill))


@pytest.mark.ac("AC-W1")
def test_key_rejects_symlink_source_directory(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    source = skill.parent.parent.parent / "fromargs" / "src" / "fromargs"
    target = source.with_name("real_fromargs")
    _ = source.rename(target)
    _ = source.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        _ = compute_key(skill, load_config(skill), find_repo_root(skill))



@pytest.mark.ac("AC-W1")
def test_key_rejects_intermediate_source_symlink(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    root = find_repo_root(skill)
    source_root = root / "lib" / "fromargs" / "examples"
    alias = skill / "alias"
    _ = alias.symlink_to(source_root, target_is_directory=True)
    config_path = skill / "wedge.toml"
    _ = config_path.write_text(
        config_path.read_text().replace(
            "../../../fromargs/examples/cheese_cave.py", "alias/cheese_cave.py"
        )
    )

    with pytest.raises(ValueError, match="symlink"):
        _ = compute_key(skill, load_config(skill), root)



@pytest.mark.ac("AC-W1")
def test_key_rejects_intermediate_symlink_with_relative_skill_dir(
    tmp_path: Path,
    copy_repo_subset: Callable[[Path], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    _ = copy_repo_subset(checkout)
    monkeypatch.chdir(checkout)
    relative_skill = Path("lib/examples/skills/cheese-cave")
    root = find_repo_root(relative_skill)
    alias = relative_skill / "alias"
    _ = alias.symlink_to(
        root / "lib" / "fromargs" / "examples", target_is_directory=True
    )
    config_path = relative_skill / "wedge.toml"
    _ = config_path.write_text(
        config_path.read_text().replace(
            "../../../fromargs/examples/cheese_cave.py", "alias/cheese_cave.py"
        )
    )

    with pytest.raises(ValueError, match="symlink"):
        _ = compute_key(relative_skill, load_config(relative_skill), root)


@pytest.mark.ac("AC-W1")
def test_key_rejects_sibling_alias_source_symlink(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    root = find_repo_root(skill)
    alias_target = skill.parent / "real_alias"
    _ = alias_target.mkdir()
    _ = (alias_target / "module.py").write_text("value = 1\n")
    _ = (skill.parent / "alias").symlink_to(alias_target, target_is_directory=True)
    config_path = skill / "wedge.toml"
    _ = config_path.write_text(
        config_path.read_text().replace(
            "../../../fromargs/examples/cheese_cave.py", "../alias/module.py"
        )
    )

    with pytest.raises(ValueError, match="symlink"):
        _ = compute_key(skill, load_config(skill), root)

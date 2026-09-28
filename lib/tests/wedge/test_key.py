"""AC-W1: the content key is stable across checkout paths and reacts only to
build-input bytes. AC-W9: a bad ``wedge.toml`` layout is a config error."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from wedge._config import ConfigError, WedgeConfig, load_config
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
        "lib/fromargs/uv.lock",
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
    _ = target.write_text(target.read_text() + "\n# touched\n")
    assert _key(skill) != before


@pytest.mark.ac("AC-W1")
def test_key_is_unchanged_by_pycache(tmp_path: Path, copy_repo_subset: Callable[[Path], Path]) -> None:
    root = tmp_path / "checkout"
    skill = copy_repo_subset(root)
    before = _key(skill)
    cache_dir = root / "lib" / "fromargs" / "src" / "fromargs" / "__pycache__"
    _ = cache_dir.mkdir()
    _ = (cache_dir / "_errors.cpython-313.pyc").write_bytes(b"garbage")
    assert _key(skill) == before


@pytest.mark.ac("AC-W1")
def test_export_is_the_project_closure_without_builder_dependencies(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    root = tmp_path / "checkout"
    _ = copy_repo_subset(root)
    requirements = export_requirements(root / "lib" / "fromargs")
    names = {name for name, _version, _marker in parse_requirements(requirements)}
    assert "fromargs" not in names
    assert not {"shiv", "pip", "setuptools", "click"} & names
    assert {"cyclopts", "attrs", "docstring-parser"} <= names


@pytest.mark.ac("AC-W1")
@pytest.mark.parametrize("variable", ["FORCE_COLOR", "CLICOLOR_FORCE"])
def test_export_is_plain_text_when_the_caller_forces_color(
    tmp_path: Path,
    copy_repo_subset: Callable[[Path], Path],
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
) -> None:
    # Playwright and many CI runners set FORCE_COLOR; an ANSI escape in the
    # export reaches parse_requirements as an unsupported requirement.
    root = tmp_path / "checkout"
    _ = copy_repo_subset(root)
    monkeypatch.setenv(variable, "1")
    requirements = export_requirements(root / "lib" / "fromargs")
    assert "\x1b" not in requirements
    names = {name for name, _version, _marker in parse_requirements(requirements)}
    assert {"cyclopts", "attrs", "docstring-parser"} <= names


@pytest.mark.ac("AC-W1")
def test_export_adds_each_configured_dependency_group(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    seen: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _ = export_requirements(Path("/project"), groups=("runtime", "cli"))

    assert seen[0][:2] == ["uv", "export"]
    assert "--no-dev" in seen[0]
    assert seen[0][-6:] == ["--group", "runtime", "--group", "cli", "--project", "/project"]


@pytest.mark.ac("AC-W1")
def test_groups_must_be_a_list_of_names(tmp_path: Path) -> None:
    _ = (tmp_path / "wedge.toml").write_text(
        'name = "x"\nentry = "x:main"\nsource = "x.py"\nrepo = "o/r"\ngroups = "runtime"\n'
    )

    with pytest.raises(ConfigError, match="'groups' must be a list"):
        _ = load_config(tmp_path)


@pytest.mark.ac("AC-W1")
def test_groups_may_not_include_dev(tmp_path: Path) -> None:
    _ = (tmp_path / "wedge.toml").write_text(
        'name = "x"\nentry = "x:main"\nsource = "x.py"\nrepo = "o/r"\ngroups = ["dev"]\n'
    )

    with pytest.raises(ConfigError, match="move the CLI dependencies"):
        _ = load_config(tmp_path)


@pytest.mark.ac("AC-W9")
def test_unknown_key_is_a_config_error(tmp_path: Path) -> None:
    _ = (tmp_path / "wedge.toml").write_text(
        'name = "x"\nentry = "x:main"\nsource = "x.py"\nrepo = "o/r"\ngroup = ["cli"]\n'
    )

    with pytest.raises(ConfigError, match="unknown key 'group'"):
        _ = load_config(tmp_path)


@pytest.mark.ac("AC-W9")
def test_a_bad_shared_default_names_the_shared_file(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill = root / "hello"
    skill.mkdir(parents=True)
    _ = (root / "wedge.toml").write_text('groups = "runtime"\n')
    _ = (skill / "wedge.toml").write_text(
        'name = "hello"\nentry = "hello:main"\nsource = "hello.py"\nrepo = "o/r"\n'
    )

    with pytest.raises(ConfigError) as raised:
        _ = load_config(skill)

    assert str(root / "wedge.toml") in str(raised.value)
    assert str(skill / "wedge.toml") not in str(raised.value)


@pytest.mark.ac("AC-W9")
def test_a_bad_own_value_names_the_skill_file(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill = root / "hello"
    skill.mkdir(parents=True)
    _ = (root / "wedge.toml").write_text('repo = "o/r"\n')
    _ = (skill / "wedge.toml").write_text(
        'name = "hello"\nentry = "hello:main"\nsource = "hello.py"\ngroups = "oops"\n'
    )

    with pytest.raises(ConfigError) as raised:
        _ = load_config(skill)

    assert str(skill / "wedge.toml") in str(raised.value)


@pytest.mark.ac("AC-W1")
def test_key_rejects_source_symlink(tmp_path: Path, copy_repo_subset: Callable[[Path], Path]) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    source = skill / "alias.py"
    target = skill / "real.py"
    _ = target.write_text("value = 1\n")
    _ = source.symlink_to(target)
    config = skill / "wedge.toml"
    _ = config.write_text(config.read_text().replace("../../../fromargs/examples/cheese_cave.py", "alias.py"))
    with pytest.raises(ValueError, match="symlink"):
        _ = _key(skill)


@pytest.mark.ac("AC-W1")
def test_key_rejects_intermediate_symlink(tmp_path: Path, copy_repo_subset: Callable[[Path], Path]) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    alias = skill / "alias"
    _ = alias.symlink_to(skill.parent.parent.parent / "fromargs" / "examples", target_is_directory=True)
    config = skill / "wedge.toml"
    _ = config.write_text(config.read_text().replace("../../../fromargs/examples/cheese_cave.py", "alias/cheese_cave.py"))
    with pytest.raises(ValueError, match="symlink"):
        _ = _key(skill)


@pytest.mark.ac("AC-W1")
def test_key_rejects_source_directory_symlink(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    source = skill.parent.parent.parent / "fromargs" / "src" / "fromargs"
    target = source.with_name("real_fromargs")
    _ = source.rename(target)
    _ = source.symlink_to(target, target_is_directory=True)
    config = skill / "wedge.toml"
    _ = config.write_text(config.read_text().replace(
        "../../../fromargs/examples/cheese_cave.py",
        "../../../fromargs/src/fromargs",
    ))
    with pytest.raises(ValueError, match="symlink"):
        _ = _key(skill)


@pytest.mark.ac("AC-W1")
def test_key_rejects_relative_intermediate_symlink(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    _ = copy_repo_subset(checkout)
    monkeypatch.chdir(checkout)
    skill = Path("lib/examples/skills/cheese-cave")
    alias = skill / "alias"
    _ = alias.symlink_to(checkout / "lib" / "fromargs" / "examples", target_is_directory=True)
    config = skill / "wedge.toml"
    _ = config.write_text(config.read_text().replace(
        "../../../fromargs/examples/cheese_cave.py", "alias/cheese_cave.py"
    ))
    with pytest.raises(ValueError, match="symlink"):
        _ = _key(skill)


@pytest.mark.ac("AC-W1")
def test_key_rejects_sibling_alias_symlink(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    alias_target = skill.parent / "real_alias"
    _ = alias_target.mkdir()
    _ = (alias_target / "module.py").write_text("value = 1\n")
    _ = (skill.parent / "alias").symlink_to(alias_target, target_is_directory=True)
    config = skill / "wedge.toml"
    _ = config.write_text(config.read_text().replace(
        "../../../fromargs/examples/cheese_cave.py", "../alias/module.py"
    ))
    with pytest.raises(ValueError, match="symlink"):
        _ = _key(skill)


@pytest.mark.ac("AC-W1")
def test_key_rejects_absolute_alias_symlink(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    alias_target = skill.parent / "absolute_real"
    _ = alias_target.mkdir()
    _ = (alias_target / "module.py").write_text("value = 1\n")
    alias = skill.parent / "absolute_alias"
    _ = alias.symlink_to(alias_target, target_is_directory=True)
    config = skill / "wedge.toml"
    _ = config.write_text(config.read_text().replace(
        "../../../fromargs/examples/cheese_cave.py",
        str(alias / "module.py"),
    ))
    with pytest.raises(ValueError, match="symlink"):
        _ = _key(skill)


@pytest.mark.ac("AC-W1")
def test_key_rejects_nested_include_symlink(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    root = tmp_path / "checkout"
    skill = copy_repo_subset(root)
    include_root = root / "lib" / "fromargs" / "src" / "fromargs"
    outside = root / "outside_nested"
    _ = outside.mkdir()
    _ = (outside / "module.py").write_text("value = 1\n")
    _ = (include_root / "nested").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _ = _key(skill)


@pytest.mark.ac("AC-W1")
def test_key_ignores_files_outside_configured_inputs(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    root = tmp_path / "checkout"
    skill = copy_repo_subset(root)
    before = _key(skill)
    _ = (root / "lib" / "unrelated.py").write_text("print('not bundled')\n")
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
    _ = (root / "outside").mkdir()
    toml = skill / "wedge.toml"
    kept = [entry for entry in toml.read_text().splitlines() if not entry.startswith("include")]
    _ = toml.write_text("\n".join([*kept, line]) + "\n")
    with pytest.raises(ConfigError, match=message):
        _ = _key(skill)


@pytest.mark.ac("AC-W9")
def test_project_without_uv_lock_is_a_config_error(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    root = tmp_path / "checkout"
    skill = copy_repo_subset(root)
    _ = (root / "lib" / "fromargs" / "uv.lock").unlink()
    with pytest.raises(ConfigError, match="has no uv.lock"):
        _ = _key(skill)


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
    _ = toml.write_text("\n".join(kept + ([repo_line] if repo_line else [])) + "\n")
    with pytest.raises(ConfigError, match=message):
        _ = load_config(skill)


@pytest.mark.ac("AC-W1")
def test_source_paths_select_only_explicit_files(tmp_path: Path) -> None:
    _ = (tmp_path / "wedge.toml").write_text(
        'name = "x"\nentry = "x:main"\nsource = "pkg"\nsource_paths = ["__init__.py"]\nrepo = "o/r"\n'
    )
    project = tmp_path / "pkg"
    project.mkdir()
    _ = (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    _ = (tmp_path / "uv.lock").write_text("version = 1\n")
    _ = (project / "__init__.py").write_text("one\n")
    _ = (project / "other.py").write_text("two\n")
    before = _key(tmp_path)
    _ = (project / "other.py").write_text("changed\n")
    assert _key(tmp_path) == before
    _ = (project / "__init__.py").write_text("changed\n")
    assert _key(tmp_path) != before


@pytest.mark.ac("AC-W9")
@pytest.mark.parametrize("selector", [[], [""], ["../outside"], ["/absolute"]])
def test_source_paths_rejects_unsafe_selectors(tmp_path: Path, selector: list[str]) -> None:
    _ = (tmp_path / "wedge.toml").write_text(
        f'name = "x"\nentry = "x:main"\nsource = "pkg"\nsource_paths = {selector!r}\nrepo = "o/r"\n'
    )
    with pytest.raises((ConfigError, ValueError)):
        _ = load_config(tmp_path)


@pytest.mark.ac("AC-W9")
def test_compute_key_rejects_intermediate_source_selector_symlink(tmp_path: Path) -> None:
    _ = (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    _ = (tmp_path / "uv.lock").write_text("version = 1\n")
    source = tmp_path / "pkg"
    source.mkdir()
    actual = source / "actual"
    actual.mkdir()
    _ = (actual / "file.py").write_text("pass\n")
    (source / "linked_dir").symlink_to(actual, target_is_directory=True)
    config = WedgeConfig(
        name="x",
        entry="x:main",
        source="pkg",
        repo="o/r",
        source_paths=("linked_dir/file.py",),
    )
    with pytest.raises(ValueError, match="must not contain symlink"):
        _ = compute_key(tmp_path, config)

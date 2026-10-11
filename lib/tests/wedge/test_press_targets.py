"""Adversarial tests for multi-target ``wedge.toml`` and the reusable site layer.

The builds run offline: a fake closure and a fake third-party install replace
the network step, while shiv and the real archive code run for real.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Callable
from zipfile import ZipFile

import pytest

import wedge._build as wedge_build
from wedge._build import (
    SiteLayer,
    build_from_layer,
    build_many,
    open_site_layer,
    populate_site_layer,
    resolve_target,
    site_layer_key,
    stage_sources,
)
from wedge._bundle import bundle_many
from wedge._config import ConfigError, WedgeConfig, load_config, load_targets
from wedge._guard import GuardError
from wedge._key import FORMAT_VERSION, BuildPaths, compute_key

SOURCE = "../../../fromargs/examples/cheese_cave.py"
ENTRY = "cheese_cave:main"
HEAD = (
    'project = "../../../fromargs"\n'
    'include = ["../../../fromargs/src/fromargs"]\n'
    'repo = "paulnsorensen/skillz-that-grillz"\n'
)
CopySubset = Callable[[Path], Path]


def _table(name: str, *, entry: str = ENTRY, source: str | None = SOURCE, extra: str = "") -> str:
    source_line = f'source = "{source}"\n' if source is not None else ""
    return f'\n[[target]]\nname = "{name}"\nentry = "{entry}"\n{source_line}{extra}'


def _write(skill: Path, *tables: str, head: str = HEAD) -> None:
    _ = (skill / "wedge.toml").write_text(head + "".join(tables))


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode())
        if path.is_file() and not path.is_symlink():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _names(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir())


@pytest.fixture
def installs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Offline closure and install; records the groups of every site populated."""
    groups_seen: list[tuple[str, ...]] = []

    def closure(_project: Path, groups: tuple[str, ...]) -> tuple[str, bool]:
        groups_seen.append(tuple(groups))
        return "", True

    def install(_requirements: str, site_dir: Path) -> None:
        (site_dir / "fakepkg" / "__pycache__").mkdir(parents=True)
        _ = (site_dir / "fakepkg" / "__init__.py").write_text("VALUE = 1\n")
        _ = (site_dir / "fakepkg" / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"\0")
        (site_dir / "fakepkg-1.0.dist-info").mkdir()
        _ = (site_dir / "fakepkg-1.0.dist-info" / "METADATA").write_text("Name: fakepkg\n")
        _ = (site_dir / "fakepkg-1.0.dist-info" / "RECORD").write_text("volatile\n")
        (site_dir / "bin").mkdir()
        _ = (site_dir / "bin" / "fakecli").write_text("#!/host/python\n")

    monkeypatch.setattr(wedge_build, "_closure_requirements", closure)
    monkeypatch.setattr(wedge_build, "_install_third_party", install)
    return groups_seen


def _hand_layer(root: Path, groups: tuple[str, ...] = (), *, dangling: bool = False) -> SiteLayer:
    (root / "fakepkg").mkdir(parents=True)
    _ = (root / "fakepkg" / "__init__.py").write_text("VALUE = 1\n")
    (root / "fakepkg-1.0.dist-info").mkdir()
    _ = (root / "fakepkg-1.0.dist-info" / "METADATA").write_text("Name: fakepkg\n")
    if dangling:
        (root / "dangling").symlink_to(root / "nowhere")
    project = root.parent / f"{root.name}-project"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")
    return SiteLayer(root.resolve(), site_layer_key(project, groups), tuple(sorted(set(groups))), project)


def _target(name: str = "app", groups: tuple[str, ...] = ()) -> WedgeConfig:
    return WedgeConfig(name=name, entry="app:main", source="app.py", repo="o/n", groups=groups)


def _sources(root: Path) -> Path:
    root.mkdir(parents=True)
    _ = (root / "app.py").write_text("def main() -> None: ...\n")
    return root


def _pyz_members(path: Path) -> list[str]:
    with ZipFile(path) as archive:
        return archive.namelist()


# ---------------------------------------------------------------- config attacks


@pytest.mark.ac("AC-3")
@pytest.mark.parametrize(
    "bad_name",
    ["", " ", "../evil", "a/b", ".hidden", "-lead", "a b", "café", "a\\nb", "a\\u0000b", "a\\tb", "..", "a/../b"],
)
def test_load_targets_unsafe_target_name_is_a_config_error(
    tmp_path: Path, copy_repo_subset: CopySubset, bad_name: str
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _ = (skill / "wedge.toml").write_text(
        HEAD + f'\n[[target]]\nname = "{bad_name}"\nentry = "{ENTRY}"\nsource = "{SOURCE}"\n'
    )

    with pytest.raises(ConfigError):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
@pytest.mark.parametrize(
    "body",
    [
        'target = "cave"\n',
        "target = 3\n",
        "target = []\n",
        "target = [1, 2]\n",
        'target = [["a"]]\n',
        '[target]\nname = "cave"\nentry = "cheese_cave:main"\n',
    ],
)
def test_load_targets_target_that_is_not_a_table_array_is_a_config_error(
    tmp_path: Path, copy_repo_subset: CopySubset, body: str
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _ = (skill / "wedge.toml").write_text(HEAD + body)

    with pytest.raises(ConfigError):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
def test_load_targets_empty_table_with_only_defaults_names_the_missing_key(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _ = (skill / "wedge.toml").write_text(HEAD + f'source = "{SOURCE}"\n[[target]]\n')

    with pytest.raises(ConfigError, match="missing required key 'name'"):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
def test_load_targets_table_without_entry_or_source_anywhere_is_a_config_error(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, '\n[[target]]\nname = "cave"\n')
    with pytest.raises(ConfigError, match="missing required key 'entry'"):
        _ = load_targets(skill)

    _write(skill, _table("cave", source=None))
    with pytest.raises(ConfigError, match="missing required key 'source'"):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
def test_load_targets_top_level_source_is_a_default_for_tables(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("cave", source=None), head=HEAD + f'source = "{SOURCE}"\n')

    (only,) = load_targets(skill)

    assert only.source == SOURCE


@pytest.mark.ac("AC-3")
@pytest.mark.parametrize("key", ["bogus", "Name", "targets", "Target"])
def test_load_targets_unknown_top_level_key_next_to_tables_is_a_config_error(
    tmp_path: Path, copy_repo_subset: CopySubset, key: str
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("cave"), head=HEAD + f"{key} = 1\n")

    with pytest.raises(ConfigError, match="unknown key"):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
@pytest.mark.parametrize("key", ["project", "repo", "target", "bogus"])
def test_load_targets_per_table_keys_outside_the_allowed_set_are_rejected(
    tmp_path: Path, copy_repo_subset: CopySubset, key: str
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("cave", extra=f'{key} = "x"\n'))

    with pytest.raises(ConfigError, match=rf"unknown key '{key}' in \[\[target\]\]"):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
@pytest.mark.parametrize("owned", ["target", "name", "entry"])
def test_load_targets_parent_defaults_cannot_set_target_name_or_entry(
    tmp_path: Path, copy_repo_subset: CopySubset, owned: str
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("cave"))
    value = '[{ name = "x", entry = "a:b" }]' if owned == "target" else '"x"'
    _ = (skill.parent / "wedge.toml").write_text(f"{owned} = {value}\n")

    with pytest.raises(ConfigError, match="shared defaults must not set"):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
def test_load_targets_parent_defaults_flow_into_every_table_and_tables_override(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _ = (skill.parent / "wedge.toml").write_text(HEAD + f'source = "{SOURCE}"\ngroups = ["shared"]\n')
    _ = (skill / "wedge.toml").write_text(
        _table("a", source=None) + _table("b", source=None, extra="groups = []\n")
    )

    a, b = load_targets(skill)

    assert (a.groups, b.groups) == (("shared",), ())
    assert a.source == b.source == SOURCE


@pytest.mark.ac("AC-3")
def test_load_targets_dev_group_from_parent_defaults_names_the_shared_file(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _ = (skill.parent / "wedge.toml").write_text(HEAD + 'groups = ["dev"]\n')
    _ = (skill / "wedge.toml").write_text(_table("a"))

    with pytest.raises(ConfigError, match=re.escape(str(skill.parent / "wedge.toml"))):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
@pytest.mark.parametrize(
    "extra",
    [
        'groups = ["dev"]\n',
        'groups = "extra"\n',
        "groups = [1]\n",
        "source_paths = []\n",
        'source_paths = ["../x"]\n',
        'source_paths = ["/etc"]\n',
        'include = "x"\n',
        'include = [""]\n',
    ],
)
def test_load_targets_bad_per_target_override_is_a_config_error(
    tmp_path: Path, copy_repo_subset: CopySubset, extra: str
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a", extra=extra))

    with pytest.raises(ConfigError):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
def test_load_targets_duplicate_names_are_caught_even_with_distinct_other_fields(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a"), _table("b"), _table("a", entry="other:main"))

    with pytest.raises(ConfigError, match=r"duplicate target names \['a'\]"):
        _ = load_targets(skill)


# ----------------------------------------------------------- path traversal


@pytest.mark.ac("AC-3")
@pytest.mark.parametrize(
    ("source", "include"),
    [
        ("../../../../../../../../../../etc/hostname", "../../../fromargs/src/fromargs"),
        ("/etc/hostname", "../../../fromargs/src/fromargs"),
        ("../../../fromargs-evil/mod.py", "../../../fromargs/src/fromargs"),
        (SOURCE, "../../../../../../../../../../etc"),
        (SOURCE, "/etc"),
        (SOURCE, "../../../fromargs-evil"),
        (SOURCE, "../../../fromargs/../fromargs-evil"),
    ],
)
def test_compute_key_target_path_leaving_the_project_is_a_config_error(
    tmp_path: Path, copy_repo_subset: CopySubset, source: str, include: str
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    evil = skill.parent.parent.parent / "fromargs-evil"
    evil.mkdir()
    _ = (evil / "mod.py").write_text("x = 1\n")
    _write(skill, _table("a", source=source, extra=f'include = ["{include}"]\n'))
    (target,) = load_targets(skill)

    with pytest.raises(ConfigError, match="must resolve inside the project"):
        _ = compute_key(skill, target)


@pytest.mark.ac("AC-3")
@pytest.mark.parametrize("selector", ["../cheese_cave.py", "/etc/hostname", "a/../../b", ""])
def test_load_targets_source_paths_escape_is_rejected(
    tmp_path: Path, copy_repo_subset: CopySubset, selector: str
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a", source="../../../fromargs/src/fromargs", extra=f'source_paths = ["{selector}"]\n'))

    with pytest.raises(ConfigError):
        targets = load_targets(skill)
        _ = compute_key(skill, targets[0])


@pytest.mark.ac("AC-3")
def test_compute_key_symlinked_source_or_selector_is_rejected(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    project = skill.parent.parent.parent / "fromargs"
    (project / "examples" / "link.py").symlink_to(project / "examples" / "cheese_cave.py")
    (project / "examples" / "linkdir").symlink_to(project / "src" / "fromargs")
    _write(skill, _table("a", source="../../../fromargs/examples/link.py"))
    (target,) = load_targets(skill)
    with pytest.raises(ValueError, match="symlink"):
        _ = compute_key(skill, target)

    _write(skill, _table("a", source="../../../fromargs/examples", extra='source_paths = ["linkdir/x"]\n'))
    (target,) = load_targets(skill)
    with pytest.raises(ValueError, match="symlink"):
        _ = compute_key(skill, target)


@pytest.mark.ac("AC-3")
def test_bundle_many_one_bad_target_fails_the_skill_and_writes_nothing(
    tmp_path: Path, copy_repo_subset: CopySubset, installs: list[tuple[str, ...]]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("good"), _table("bad", source="/etc/hostname"))

    outcomes = bundle_many([skill])

    assert len(outcomes) == 1 and outcomes[0].error is not None
    assert "must resolve inside the project" in outcomes[0].error
    assert not (skill / "scripts").exists()
    assert installs == []


@pytest.mark.ac("AC-3")
def test_resolve_paths_same_include_listed_twice_is_a_config_error(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    include = "../../../fromargs/src/fromargs"
    _write(skill, _table("a", extra=f'include = ["{include}", "{include}"]\n'))
    (target,) = load_targets(skill)

    with pytest.raises(ConfigError, match="share top-level names"):
        _ = compute_key(skill, target)


# ------------------------------------------------------------------ key attacks


@pytest.mark.ac("AC-3")
def test_compute_key_target_with_the_single_target_name_still_differs_from_single_form(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    single_text = (skill / "wedge.toml").read_text()
    (only_single,) = load_targets(skill)
    single_key = compute_key(skill, only_single)

    _write(skill, _table(only_single.name))
    (only_multi,) = load_targets(skill)

    assert only_multi.multi and only_multi.name == only_single.name
    assert compute_key(skill, only_multi) != single_key
    _ = (skill / "wedge.toml").write_text(single_text)
    assert compute_key(skill, load_config(skill)) == single_key


@pytest.mark.ac("AC-3")
def test_compute_key_same_multi_target_file_in_two_checkouts_gives_equal_keys(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    first = copy_repo_subset(tmp_path / "one")
    second = copy_repo_subset(tmp_path / "two" / "deeper")
    for skill in (first, second):
        _write(skill, _table("a"), _table("b"))

    keys_one = [compute_key(first, t) for t in load_targets(first)]
    keys_two = [compute_key(second, t) for t in load_targets(second)]

    assert keys_one == keys_two and keys_one[0] != keys_one[1]


@pytest.mark.ac("AC-3")
def test_compute_key_every_target_key_changes_when_the_shared_source_changes(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a"), _table("b"))
    before = [compute_key(skill, t) for t in load_targets(skill)]
    source = skill.parent.parent.parent / "fromargs" / "examples" / "cheese_cave.py"
    _ = source.write_text(source.read_text() + "\n# touched\n")

    after = [compute_key(skill, t) for t in load_targets(skill)]

    assert all(x != y for x, y in zip(before, after))


# ---------------------------------------------------------------- build attacks


@pytest.mark.ac("AC-3")
def test_build_many_target_order_does_not_change_any_target_output(
    tmp_path: Path, copy_repo_subset: CopySubset, installs: list[tuple[str, ...]]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    other = ENTRY.replace("main", "other")
    _write(skill, _table("a"), _table("b", entry=other))
    forward = {o.value.name: o.value.path.read_bytes() for o in build_many([skill], tmp_path / "f") if o.value}
    _write(skill, _table("b", entry=other), _table("a"))
    backward = {o.value.name: o.value.path.read_bytes() for o in build_many([skill], tmp_path / "b") if o.value}

    assert sorted(forward) == ["a", "b"] and forward == backward
    assert forward["a"] != forward["b"]
    assert len(installs) == 2


@pytest.mark.ac("AC-3")
def test_build_many_targets_with_one_site_install_once_and_other_groups_install_again(
    tmp_path: Path, copy_repo_subset: CopySubset, installs: list[tuple[str, ...]]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a"), _table("b"), _table("c", extra='groups = ["extra"]\n'))

    outcomes = build_many([skill], tmp_path / "out", jobs=1)

    assert [o.error for o in outcomes] == [None, None, None]
    assert sorted(installs) == [(), ("extra",)]


@pytest.mark.ac("AC-3")
def test_build_many_output_names_and_bytes_are_stable_across_mtime_mode_and_env(
    tmp_path: Path,
    copy_repo_subset: CopySubset,
    installs: list[tuple[str, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a"), _table("b"))
    first = build_many([skill], tmp_path / "one")
    for path in (skill.parent.parent.parent / "fromargs").rglob("*.py"):
        os.utime(path, (1_000_000_000, 1_000_000_000))
        path.chmod(0o600)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "42")
    monkeypatch.setenv("PYTHONHASHSEED", "7")
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    old_umask = os.umask(0o077)
    try:
        second = build_many([skill], tmp_path / "two")
    finally:
        _ = os.umask(old_umask)

    assert len(installs) == 2
    for x, y in zip(first, second):
        assert x.value is not None and y.value is not None
        assert re.fullmatch(rf"{x.value.name}-[0-9a-f]{{12}}\.pyz", x.value.path.name)
        assert x.value.path.name == y.value.path.name
        assert x.value.path.read_bytes() == y.value.path.read_bytes()
        assert x.value.content_sha256[:12] in x.value.path.name


@pytest.mark.ac("AC-3")
@pytest.mark.usefixtures("installs")
def test_build_many_duplicate_name_across_skills_fails_both_and_spares_the_rest(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    first = copy_repo_subset(tmp_path / "checkout")
    second = first.parent / "cheese-cave-2"
    second.mkdir()
    _write(first, _table("shared"), _table("only-first"))
    _write(second, _table("shared", entry="cheese_cave:other"))

    outcomes = build_many([first, second], tmp_path / "out")

    assert [o.item for o in outcomes] == [first, first, second]
    assert outcomes[0].error is not None and "duplicate target name 'shared'" in outcomes[0].error
    assert outcomes[1].error is None and outcomes[1].value is not None
    assert outcomes[2].error is not None and "duplicate target name 'shared'" in outcomes[2].error
    assert _names(tmp_path / "out") == [outcomes[1].value.path.name]


@pytest.mark.ac("AC-3")
@pytest.mark.usefixtures("installs")
def test_bundle_many_the_same_skill_twice_in_one_call_fails_both_entries(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a"))

    outcomes = bundle_many([skill, skill])

    assert all(o.error is not None and "duplicate" in o.error for o in outcomes)
    assert not (skill / "scripts").exists()


@pytest.mark.ac("AC-3")
@pytest.mark.usefixtures("installs")
def test_bundle_many_failing_shiv_for_one_target_leaves_no_partial_output(
    tmp_path: Path,
    copy_repo_subset: CopySubset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("good"), _table("bad", entry="bad:main"))
    scripts = skill / "scripts"
    scripts.mkdir()
    _ = (scripts / "bad.pyz").write_bytes(b"previous bundle")
    real_shiv = wedge_build._shiv  # pyright: ignore[reportPrivateUsage]

    def flaky(site_dir: Path, entry: str, out_path: Path) -> None:
        if entry == "bad:main":
            _ = out_path.write_bytes(b"half written")
            raise subprocess.CalledProcessError(2, ["shiv"], stderr="boom")
        real_shiv(site_dir, entry, out_path)

    monkeypatch.setattr(wedge_build, "_shiv", flaky)

    outcomes = bundle_many([skill])

    assert outcomes[0].error is None
    assert outcomes[1].error is not None and "boom" in outcomes[1].error
    assert _names(scripts) == ["bad.pyz", "good.pyz"]
    assert (scripts / "bad.pyz").read_bytes() == b"previous bundle"
    assert os.access(scripts / "good.pyz", os.X_OK)


@pytest.mark.ac("AC-3")
@pytest.mark.usefixtures("installs")
def test_bundle_many_failing_site_install_fails_every_target_and_creates_no_scripts(
    tmp_path: Path,
    copy_repo_subset: CopySubset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a"), _table("b"))

    def broken(_requirements: str, _site_dir: Path) -> None:
        raise subprocess.CalledProcessError(1, ["uv"], stderr="network down")

    monkeypatch.setattr(wedge_build, "_install_third_party", broken)

    outcomes = bundle_many([skill])

    assert [bool(o.error and "network down" in o.error) for o in outcomes] == [True, True]
    assert not (skill / "scripts").exists()


@pytest.mark.ac("AC-3")
@pytest.mark.usefixtures("installs")
def test_bundle_many_directory_or_symlink_in_the_way_fails_only_that_target_and_leaks_no_temp(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a"), _table("b"), _table("c"))
    scripts = skill / "scripts"
    (scripts / "a.pyz").mkdir(parents=True)
    victim = tmp_path / "victim"
    _ = victim.write_bytes(b"untouched")
    (scripts / "b.pyz").symlink_to(victim)

    outcomes = bundle_many([skill])

    assert [o.error is None for o in outcomes] == [False, False, True]
    assert victim.read_bytes() == b"untouched"
    assert _names(scripts) == ["a.pyz", "b.pyz", "c.pyz"]


@pytest.mark.ac("AC-3")
@pytest.mark.usefixtures("installs")
def test_bundle_many_scripts_symlink_fails_every_target_and_writes_through_nothing(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a"), _table("b"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (skill / "scripts").symlink_to(elsewhere)

    outcomes = bundle_many([skill])

    assert all(o.error and "must not be symlink" in o.error for o in outcomes)
    assert list(elsewhere.iterdir()) == []


@pytest.mark.ac("AC-3")
@pytest.mark.usefixtures("installs")
def test_bundle_many_overlong_target_name_is_an_error_outcome_not_a_crash(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("ok"), _table("n" * 300))

    outcomes = bundle_many([skill])

    assert outcomes[0].error is None
    assert outcomes[1].error is not None
    assert _names(skill / "scripts") == ["ok.pyz"]


@pytest.mark.ac("AC-3")
@pytest.mark.usefixtures("installs")
def test_bundle_check_reports_each_target_separately_and_writes_nothing(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a"), _table("b"), _table("c"))
    assert [o.error for o in bundle_many([skill])] == [None, None, None]
    scripts = skill / "scripts"
    (scripts / "b.pyz").unlink()
    (scripts / "c.pyz").chmod(0o644)
    _ = (scripts / "orphan.pyz").write_bytes(b"leftover from a removed target")
    before = _tree_digest(scripts)

    errors = [o.error for o in bundle_many([skill], check=True)]

    assert errors[0] is None
    assert errors[1] is not None and "missing bundle" in errors[1]
    assert errors[2] is not None and "not executable" in errors[2]
    assert _tree_digest(scripts) == before


@pytest.mark.ac("AC-3")
@pytest.mark.usefixtures("installs")
def test_bundle_check_corrupt_bundle_is_an_error_not_an_exception(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a"), _table("b"))
    _ = bundle_many([skill])
    _ = (skill / "scripts" / "a.pyz").write_bytes(b"#!/usr/bin/env python3\nnot a zip")
    _ = (skill / "scripts" / "b.pyz").write_bytes(b"")

    errors = [o.error for o in bundle_many([skill], check=True)]

    assert errors[0] is not None and "invalid bundle" in errors[0] and "a.pyz" in errors[0]
    assert errors[1] is not None and "invalid bundle" in errors[1] and "b.pyz" in errors[1]


# ------------------------------------------------------------------ site layer


@pytest.mark.ac("AC-4")
def test_site_layer_key_ignores_group_order_and_duplicates_but_not_membership(tmp_path: Path) -> None:
    _ = (tmp_path / "uv.lock").write_text("lock\n")

    base = site_layer_key(tmp_path, ["a", "b"])

    assert site_layer_key(tmp_path, ["b", "a"]) == base == site_layer_key(tmp_path, ("a", "b", "a"))
    assert site_layer_key(tmp_path, ["a"]) != base
    assert site_layer_key(tmp_path, []) != base
    assert site_layer_key(tmp_path, ["a,b"]) != site_layer_key(tmp_path, ["a", "b"])


@pytest.mark.ac("AC-4")
def test_site_layer_key_changes_with_lock_bytes_python_and_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "uv.lock"
    _ = lock.write_text("lock\n")
    base = site_layer_key(tmp_path, [])
    _ = lock.write_text("lock\n\n")
    assert site_layer_key(tmp_path, []) != base
    _ = lock.write_text("lock\n")
    assert site_layer_key(tmp_path, []) == base

    monkeypatch.setattr(wedge_build, "TARGET_PYTHON", "3.12")
    assert site_layer_key(tmp_path, []) != base
    monkeypatch.undo()
    monkeypatch.setattr(wedge_build, "FORMAT_VERSION", FORMAT_VERSION + 1)
    assert site_layer_key(tmp_path, []) != base


@pytest.mark.ac("AC-4")
def test_site_layer_key_is_independent_of_project_location_and_pyproject(tmp_path: Path) -> None:
    for name in ("one", "two/deeper"):
        (tmp_path / name).mkdir(parents=True)
        _ = (tmp_path / name / "uv.lock").write_text("lock\n")
    _ = (tmp_path / "two/deeper" / "pyproject.toml").write_text("[project]\n")

    assert site_layer_key(tmp_path / "one", []) == site_layer_key(tmp_path / "two/deeper", [])


@pytest.mark.ac("AC-4")
def test_site_layer_key_missing_lock_is_a_file_error(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        _ = site_layer_key(tmp_path, [])


@pytest.mark.ac("AC-4")
def test_populate_site_layer_strips_volatile_files_and_keeps_third_party_only(
    tmp_path: Path, installs: list[tuple[str, ...]]
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")

    layer = populate_site_layer(project, ["b", "a", "a"], tmp_path / "layer")

    assert layer.groups == ("a", "b") and layer.key == site_layer_key(project, ["a", "b"])
    assert installs == [("b", "a", "a")]
    found = sorted(p.relative_to(layer.path).as_posix() for p in layer.path.rglob("*"))
    assert found == ["fakepkg", "fakepkg-1.0.dist-info", "fakepkg-1.0.dist-info/METADATA", "fakepkg/__init__.py"]


@pytest.mark.ac("AC-4")
@pytest.mark.usefixtures("installs")
def test_populate_site_layer_existing_destination_is_refused_and_left_alone(
    tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")
    dest = tmp_path / "layer"
    dest.mkdir()
    _ = (dest / "keep").write_text("mine")

    with pytest.raises(FileExistsError):
        _ = populate_site_layer(project, [], dest)

    assert _names(dest) == ["keep"]


@pytest.mark.ac("AC-4")
def test_populate_site_layer_guard_failure_creates_no_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")

    def refuse(_project: Path, _groups: tuple[str, ...]) -> tuple[str, bool]:
        raise GuardError("native wheel")

    monkeypatch.setattr(wedge_build, "_closure_requirements", refuse)

    with pytest.raises(GuardError):
        _ = populate_site_layer(project, [], tmp_path / "layer")

    assert not (tmp_path / "layer").exists()


@pytest.mark.ac("AC-4")
def test_populate_site_layer_without_a_lock_fails_and_leaves_no_layer(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    with pytest.raises(Exception):
        _ = populate_site_layer(project, [], tmp_path / "layer")

    assert not (tmp_path / "layer").exists()


@pytest.mark.ac("AC-4")
@pytest.mark.usefixtures("installs")
def test_populate_site_layer_failed_install_leaves_no_layer_and_a_retry_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed install leaves no dest and no staging directory, so a retry can use the same dest."""
    project = tmp_path / "proj"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")

    def broken(_requirements: str, site_dir: Path) -> None:
        _ = (site_dir / "half.py").write_text("partial")
        raise subprocess.CalledProcessError(1, ["uv"], stderr="network down")

    monkeypatch.setattr(wedge_build, "_install_third_party", broken)

    with pytest.raises(subprocess.CalledProcessError):
        _ = populate_site_layer(project, [], tmp_path / "layer")

    assert not (tmp_path / "layer").exists(), "a failed populate must not leave a partial layer behind"
    assert not list(tmp_path.glob(".layer.*")), "a failed populate must remove its staging directory"


@pytest.mark.ac("AC-4")
def test_build_from_layer_output_has_no_bytecode_or_install_metadata_and_keeps_layer(tmp_path: Path) -> None:
    layer = _hand_layer(tmp_path / "layer")
    (layer.path / "fakepkg" / "__pycache__").mkdir()
    _ = (layer.path / "fakepkg" / "__pycache__" / "m.pyc").write_bytes(b"\0")
    _ = (layer.path / "fakepkg-1.0.dist-info" / "RECORD").write_text("r")
    sources = _sources(tmp_path / "sources")
    (sources / "pkg" / "__pycache__").mkdir(parents=True)
    _ = (sources / "pkg" / "__init__.py").write_text("")
    _ = (sources / "pkg" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    _ = (sources / "top.pyc").write_bytes(b"\0")
    (sources / "__pycache__").mkdir()
    _ = (sources / "__pycache__" / "app.cpython-311.pyc").write_bytes(b"\0")
    before = _tree_digest(layer.path)

    out = build_from_layer(layer, sources, _target(), tmp_path / "out")

    members = _pyz_members(out)
    assert not [m for m in members if "__pycache__" in m or m.endswith(".pyc") or m.endswith("RECORD")]
    assert "site-packages/app.py" in members and "site-packages/pkg/__init__.py" in members
    assert "site-packages/fakepkg/__init__.py" in members
    assert _tree_digest(layer.path) == before


@pytest.mark.ac("AC-4")
@pytest.mark.parametrize("shadow", ["fakepkg", "fakepkg-1.0.dist-info", "dangling"])
@pytest.mark.parametrize("kind", ["dir", "file"])
def test_build_from_layer_first_party_shadowing_an_installed_path_is_rejected(
    tmp_path: Path, shadow: str, kind: str
) -> None:
    layer = _hand_layer(tmp_path / "layer", dangling=True)
    sources = _sources(tmp_path / "sources")
    if kind == "dir":
        (sources / shadow).mkdir()
    else:
        _ = (sources / shadow).write_text("x")
    before = _tree_digest(layer.path)

    with pytest.raises(ConfigError, match="shadows installed"):
        _ = build_from_layer(layer, sources, _target(), tmp_path / "out")

    assert _tree_digest(layer.path) == before
    assert not list((tmp_path / "out").glob("*.pyz"))


@pytest.mark.ac("AC-4")
def test_build_from_layer_symlinked_directory_or_nested_link_in_sources_is_rejected(tmp_path: Path) -> None:
    layer = _hand_layer(tmp_path / "layer")
    outside = tmp_path / "outside"
    outside.mkdir()
    _ = (outside / "secret.py").write_text("SECRET = 1\n")
    sources = _sources(tmp_path / "sources")
    (sources / "linked").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        _ = build_from_layer(layer, sources, _target(), tmp_path / "out")

    (sources / "linked").unlink()
    (sources / "pkg").mkdir()
    (sources / "pkg" / "nested").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        _ = build_from_layer(layer, sources, _target(), tmp_path / "out")
    assert not list((tmp_path / "out").glob("*.pyz"))


@pytest.mark.ac("AC-4")
def test_build_from_layer_symlinked_file_in_sources_is_rejected(tmp_path: Path) -> None:
    """A symlink at the top level of the sources tree is rejected, as in build()."""
    layer = _hand_layer(tmp_path / "layer")
    outside = tmp_path / "outside-secret.txt"
    _ = outside.write_text("TOP SECRET\n")
    sources = _sources(tmp_path / "sources")
    (sources / "leak.txt").symlink_to(outside)

    with pytest.raises((ValueError, ConfigError, OSError)):
        out = build_from_layer(layer, sources, _target(), tmp_path / "out")
        with ZipFile(out) as archive:
            assert archive.read("site-packages/leak.txt") == b"TOP SECRET\n"


@pytest.mark.ac("AC-4")
def test_build_from_layer_unsafe_target_name_is_rejected_and_writes_outside_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name that gets past the constructor still fails in ``_shiv_into`` and writes outside nothing."""
    scratch = tmp_path / "scratch" / "tmp"
    scratch.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    layer = _hand_layer(tmp_path / "layer")
    sources = _sources(tmp_path / "sources")
    hostile = _target()
    object.__setattr__(hostile, "name", "../escape")

    with pytest.raises(ConfigError, match="safe filename component"):
        _ = build_from_layer(layer, sources, hostile, tmp_path / "out")

    escaped = [p for p in tmp_path.rglob("*escape*") if tmp_path / "out" not in p.parents]
    assert not escaped, f"build wrote outside out_dir: {escaped}"
    assert list((tmp_path / "out").iterdir()) == []


@pytest.mark.ac("AC-4")
def test_build_from_layer_rejects_group_mismatch_but_accepts_order_and_duplicates(tmp_path: Path) -> None:
    layer = _hand_layer(tmp_path / "layer", ("a", "b"))
    sources = _sources(tmp_path / "sources")

    for groups in (("b", "a"), ("a", "b", "a")):
        out = build_from_layer(layer, sources, _target(groups=groups), tmp_path / "out")
        assert out.is_file()
    for groups in ((), ("a",), ("a", "b", "c"), ("A", "B")):
        with pytest.raises(ValueError, match="do not match the layer"):
            _ = build_from_layer(layer, sources, _target(groups=groups), tmp_path / "out")


@pytest.mark.ac("AC-4")
def test_build_from_layer_two_targets_one_layer_differ_only_by_their_own_name(tmp_path: Path) -> None:
    layer = _hand_layer(tmp_path / "layer")
    sources = _sources(tmp_path / "sources")
    before = _tree_digest(layer.path)

    one = build_from_layer(layer, sources, _target("one"), tmp_path / "out")
    two = build_from_layer(layer, sources, _target("two"), tmp_path / "out")
    again = build_from_layer(layer, sources, _target("one"), tmp_path / "other")

    assert one.name.startswith("one-") and two.name.startswith("two-")
    assert one.name == again.name and one.read_bytes() == again.read_bytes()
    assert one.read_bytes() == two.read_bytes(), "the name selects the file name, never the archive bytes"
    assert _tree_digest(layer.path) == before


@pytest.mark.ac("AC-4")
def test_build_from_layer_source_creation_order_and_permissions_do_not_change_output(tmp_path: Path) -> None:
    layer = _hand_layer(tmp_path / "layer")
    first = tmp_path / "s1"
    first.mkdir()
    for name in ("b.py", "a.py", "c.py"):
        _ = (first / name).write_text(f"# {name}\n")
    second = tmp_path / "s2"
    second.mkdir()
    for name in ("c.py", "a.py", "b.py"):
        _ = (second / name).write_text(f"# {name}\n")
        (second / name).chmod(0o600)
        os.utime(second / name, (1, 1))

    one = build_from_layer(layer, first, _target(), tmp_path / "o1")
    two = build_from_layer(layer, second, _target(), tmp_path / "o2")

    assert one.name == two.name and one.read_bytes() == two.read_bytes()


@pytest.mark.ac("AC-4")
def test_build_from_layer_concurrent_builds_agree_and_never_write_the_layer(tmp_path: Path) -> None:
    layer = _hand_layer(tmp_path / "layer")
    sources = _sources(tmp_path / "sources")
    before = _tree_digest(layer.path)
    barrier = threading.Barrier(4)

    def run(index: int) -> bytes:
        _ = barrier.wait(timeout=30)
        out = build_from_layer(layer, sources, _target(), tmp_path / f"out-{index}")
        return out.read_bytes()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))

    assert len(set(results)) == 1
    assert _tree_digest(layer.path) == before


@pytest.mark.ac("AC-4")
def test_build_from_layer_missing_layer_or_sources_leaves_out_dir_empty(tmp_path: Path) -> None:
    layer = _hand_layer(tmp_path / "layer")
    sources = _sources(tmp_path / "sources")
    ghost = replace(layer, path=tmp_path / "ghost")

    with pytest.raises(OSError):
        _ = build_from_layer(ghost, sources, _target(), tmp_path / "out")
    with pytest.raises(OSError):
        _ = build_from_layer(layer, tmp_path / "no-sources", _target(), tmp_path / "out")

    assert list((tmp_path / "out").iterdir()) == []


@pytest.mark.ac("AC-4")
def test_build_from_layer_failing_shiv_leaves_out_dir_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer = _hand_layer(tmp_path / "layer")
    sources = _sources(tmp_path / "sources")

    def boom(_site: Path, _entry: str, out_path: Path) -> None:
        _ = out_path.write_bytes(b"half")
        raise subprocess.CalledProcessError(1, ["shiv"])

    monkeypatch.setattr(wedge_build, "_shiv", boom)

    with pytest.raises(subprocess.CalledProcessError):
        _ = build_from_layer(layer, sources, _target(), tmp_path / "out")

    assert list((tmp_path / "out").iterdir()) == []


@pytest.mark.ac("AC-4")
def test_stage_sources_never_touches_the_layer_and_refuses_an_existing_destination(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("a"))
    (target,) = load_targets(skill)
    paths = resolve_target(skill, target)
    layer = _hand_layer(tmp_path / "layer")
    before = _tree_digest(layer.path)

    staged = stage_sources(paths, tmp_path / "staged")

    assert sorted(p.name for p in staged.iterdir()) == ["cheese_cave.py", "fromargs"]
    assert _tree_digest(layer.path) == before
    with pytest.raises(FileExistsError):
        _ = stage_sources(paths, tmp_path / "staged")
    assert sorted(p.name for p in staged.iterdir()) == ["cheese_cave.py", "fromargs"], "a refused stage keeps the old tree"


def _only_target(skill: Path) -> WedgeConfig:
    _write(skill, _table("a"))
    (target,) = load_targets(skill)
    return target


@pytest.mark.ac("AC-4")
def test_stage_sources_overlay_replaces_checkout_files_and_leaves_the_rest(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    paths = resolve_target(skill, _only_target(skill))
    overlay = tmp_path / "overlay"
    (overlay / "examples").mkdir(parents=True)
    _ = (overlay / "examples/cheese_cave.py").write_text("EDITED = True\n")
    (overlay / "src/fromargs").mkdir(parents=True)
    _ = (overlay / "src/fromargs/__init__.py").write_text("EDITED_INIT = True\n")
    real_before = (paths.source).read_text()
    untouched = (paths.includes[0] / "_errors.py").read_text()

    plain = stage_sources(paths, tmp_path / "plain")
    staged = stage_sources(paths, tmp_path / "staged", overlay)

    assert (staged / "cheese_cave.py").read_text() == "EDITED = True\n"
    assert (staged / "fromargs/__init__.py").read_text() == "EDITED_INIT = True\n"
    assert (staged / "fromargs/_errors.py").read_text() == untouched, "a file outside the overlay comes from the checkout"
    assert (plain / "cheese_cave.py").read_text() == real_before, "the checkout itself is never edited"
    assert paths.source.read_text() == real_before


@pytest.mark.ac("AC-4")
def test_stage_sources_overlay_file_outside_the_target_sources_is_rejected(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    paths = resolve_target(skill, _only_target(skill))
    overlay = tmp_path / "overlay"
    (overlay / "examples").mkdir(parents=True)
    _ = (overlay / "examples/cheese_cave.py").write_text("EDITED = True\n")
    _ = (overlay / "examples/new_module.py").write_text("ADDED = True\n")

    with pytest.raises(ConfigError, match="overlay file is not under the target's sources: examples/new_module.py"):
        _ = stage_sources(paths, tmp_path / "staged", overlay)

    assert not (tmp_path / "staged").exists(), "a rejected overlay stages nothing"


@pytest.mark.ac("AC-4")
def test_stage_sources_overlay_symlink_is_rejected(tmp_path: Path, copy_repo_subset: CopySubset) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    paths = resolve_target(skill, _only_target(skill))
    overlay = tmp_path / "overlay"
    (overlay / "examples").mkdir(parents=True)
    (overlay / "examples/cheese_cave.py").symlink_to(paths.source)

    with pytest.raises(ValueError, match="regular file"):
        _ = stage_sources(paths, tmp_path / "staged", overlay)


def _paths(project: Path, source: Path, includes: tuple[Path, ...] = (), selectors: tuple[str, ...] = ()) -> BuildPaths:
    return BuildPaths(project / "wedge.toml", project, source, includes, selectors)


def _put(root: Path, relative: str, text: str) -> Path:
    file = root / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    _ = file.write_text(text)
    return file


@pytest.mark.ac("AC-4")
def test_stage_sources_overlay_adds_files_under_the_roots(tmp_path: Path) -> None:
    project = (tmp_path / "proj").resolve()
    _ = _put(project, "app/__init__.py", "A = 1\n")
    _ = _put(project, "lib/base.py", "B = 1\n")
    paths = _paths(project, project / "app", (project / "lib",))
    overlay = tmp_path / "overlay"
    _ = _put(overlay, "app/extra/mod.py", "ADDED_SOURCE = True\n")
    _ = _put(overlay, "lib/new.py", "ADDED_INCLUDE = True\n")

    staged = stage_sources(paths, tmp_path / "staged", overlay)

    assert (staged / "app/extra/mod.py").read_text() == "ADDED_SOURCE = True\n"
    assert (staged / "lib/new.py").read_text() == "ADDED_INCLUDE = True\n"
    assert (staged / "lib/base.py").read_text() == "B = 1\n"
    assert not (project / "app/extra").exists(), "the checkout never gains the added file"


@pytest.mark.ac("AC-6")
def test_stage_sources_overlay_only_stages_just_the_overlay_files(tmp_path: Path) -> None:
    project = (tmp_path / "proj").resolve()
    _ = _put(project, "app/__init__.py", "A = 1\n")
    _ = _put(project, "app/live_extra.py", "LIVE = True\n")
    _ = _put(project, "lib/base.py", "B = 1\n")
    paths = _paths(project, project / "app", (project / "lib",))
    overlay = tmp_path / "overlay"
    _ = _put(overlay, "app/__init__.py", "A = 2\n")

    staged = stage_sources(paths, tmp_path / "staged", overlay, overlay_only=True)

    assert (staged / "app/__init__.py").read_text() == "A = 2\n"
    assert not (staged / "app/live_extra.py").exists(), "a live file outside the overlay is not staged"
    assert not (staged / "lib/base.py").exists(), "an include file outside the overlay is not staged"

@pytest.mark.ac("AC-4")
def test_stage_sources_overlay_file_outside_every_root_is_rejected(tmp_path: Path) -> None:
    project = (tmp_path / "proj").resolve()
    _ = _put(project, "app/__init__.py", "A = 1\n")
    paths = _paths(project, project / "app")
    overlay = tmp_path / "overlay"
    _ = _put(overlay, "other/stray.py", "X = 1\n")

    with pytest.raises(ConfigError, match="not under the target's sources: other/stray.py"):
        _ = stage_sources(paths, tmp_path / "staged", overlay)

    assert not (tmp_path / "staged").exists()


@pytest.mark.ac("AC-4")
def test_stage_sources_overlay_file_outside_the_selectors_is_rejected(tmp_path: Path) -> None:
    project = (tmp_path / "proj").resolve()
    _ = _put(project, "src/a/one.py", "A = 1\n")
    _ = _put(project, "src/b/two.py", "B = 1\n")
    paths = _paths(project, project / "src", selectors=("a",))
    inside = tmp_path / "inside"
    _ = _put(inside, "src/a/new.py", "NEW = True\n")
    outside = tmp_path / "outside"
    _ = _put(outside, "src/b/new.py", "NEW = True\n")

    staged = stage_sources(paths, tmp_path / "staged", inside)
    assert (staged / "src/a/new.py").read_text() == "NEW = True\n"
    with pytest.raises(ConfigError, match="not under the target's sources: src/b/new.py"):
        _ = stage_sources(paths, tmp_path / "staged-2", outside)


@pytest.mark.ac("AC-4")
def test_stage_sources_overlay_replaces_a_source_inside_an_include_in_every_root(tmp_path: Path) -> None:
    project = (tmp_path / "proj").resolve()
    _ = _put(project, "pkg/cli.py", "V = 1\n")
    _ = _put(project, "pkg/util.py", "U = 1\n")
    paths = _paths(project, project / "pkg/cli.py", (project / "pkg",))
    overlay = tmp_path / "overlay"
    _ = _put(overlay, "pkg/cli.py", "V = 2\n")

    staged = stage_sources(paths, tmp_path / "staged", overlay)

    assert (staged / "cli.py").read_text() == "V = 2\n"
    assert (staged / "pkg/cli.py").read_text() == "V = 2\n"
    assert (staged / "pkg/util.py").read_text() == "U = 1\n"


@pytest.mark.ac("AC-4")
@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_populate_site_layer_refuses_an_existing_key_file(
    tmp_path: Path, installs: list[tuple[str, ...]], kind: str
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")
    key_file = tmp_path / "layer.key"
    if kind == "file":
        _ = key_file.write_text("stale\n")
    else:
        key_file.symlink_to(tmp_path / "elsewhere")

    with pytest.raises(FileExistsError):
        _ = populate_site_layer(project, [], tmp_path / "layer")

    assert not (tmp_path / "layer").exists()
    assert not (tmp_path / "elsewhere").exists(), "the key never writes through a symlink"
    assert installs == []


@pytest.mark.ac("AC-4")
def test_a_populated_layer_reopens_and_refuses_changed_inputs(
    tmp_path: Path, installs: list[tuple[str, ...]]
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    lock = project / "uv.lock"
    _ = lock.write_text("lock\n")
    layer = populate_site_layer(project, ["a"], tmp_path / "layer")

    assert open_site_layer(project, ["a", "a"], tmp_path / "layer") == layer
    assert not (layer.path / "layer.key").exists() and not list(layer.path.glob("*.key")), "the key stays outside the site"
    with pytest.raises(ValueError, match="uv.lock or groups changed"):
        _ = open_site_layer(project, ["b"], tmp_path / "layer")
    _ = lock.write_text("lock changed\n")
    with pytest.raises(ValueError, match="uv.lock or groups changed"):
        _ = open_site_layer(project, ["a"], tmp_path / "layer")
    with pytest.raises(FileNotFoundError):
        _ = open_site_layer(project, ["a"], tmp_path / "missing")
    assert installs == [("a",)]


@pytest.mark.ac("AC-4")
def test_a_layer_without_a_key_file_does_not_reopen(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")
    (tmp_path / "layer").mkdir()

    with pytest.raises(ValueError, match="no key file"):
        _ = open_site_layer(project, [], tmp_path / "layer")


@pytest.mark.ac("AC-4")
def test_build_from_layer_refuses_a_layer_populated_from_another_lock(tmp_path: Path) -> None:
    layer = _hand_layer(tmp_path / "layer")
    sources = _sources(tmp_path / "sources")
    _ = (layer.project / "uv.lock").write_text("another lock\n")

    with pytest.raises(ValueError, match="do not match the layer"):
        _ = build_from_layer(layer, sources, _target(), tmp_path / "out")

    assert not (tmp_path / "out").exists(), "a refused layer creates nothing"


@pytest.mark.ac("AC-4")
def test_build_from_layer_rejects_import_name_shadowing(tmp_path: Path) -> None:
    layer = _hand_layer(tmp_path / "layer")
    _ = (layer.path / "six.py").write_text("X = 1\n")
    (layer.path / "ext").mkdir()
    _ = (layer.path / "speedups.cpython-311-x86_64-linux-gnu.so").write_bytes(b"\0")
    cases = {"six": "dir", "fakepkg.py": "file", "speedups.py": "file", "ext.py": "file"}

    for name, kind in cases.items():
        sources = _sources(tmp_path / f"sources-{name}")
        if kind == "dir":
            (sources / name).mkdir()
        else:
            _ = (sources / name).write_text("x")
        with pytest.raises(ConfigError, match="shadows installed"):
            _ = build_from_layer(layer, sources, _target(), tmp_path / "out")


@pytest.mark.ac("AC-4")
def test_a_first_party_top_level_bin_is_rejected_not_silently_stripped(
    tmp_path: Path, copy_repo_subset: CopySubset
) -> None:
    layer = _hand_layer(tmp_path / "layer")
    sources = _sources(tmp_path / "sources")
    (sources / "bin").mkdir()
    _ = (sources / "bin" / "tool.py").write_text("x = 1\n")

    with pytest.raises(ConfigError, match="reserved top-level name 'bin'"):
        _ = build_from_layer(layer, sources, _target(), tmp_path / "out")

    skill = copy_repo_subset(tmp_path / "checkout")
    paths = resolve_target(skill, _only_target(skill))
    reserved = replace(paths, source=paths.source.parent / "bin")
    (paths.source.parent / "bin").mkdir(exist_ok=True)
    with pytest.raises(ConfigError, match="reserved top-level name 'bin'"):
        _ = stage_sources(reserved, tmp_path / "staged")


@pytest.mark.ac("AC-4")
def test_a_symlink_in_the_layer_is_rejected_before_shiv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layer = _hand_layer(tmp_path / "layer")
    (layer.path / "fakepkg" / "alias.py").symlink_to(layer.path / "fakepkg" / "__init__.py")
    sources = _sources(tmp_path / "sources")

    def no_shiv(*_args: object) -> None:
        raise AssertionError("shiv must not run on a tree with a symlink")

    monkeypatch.setattr(wedge_build, "_shiv", no_shiv)

    with pytest.raises(ValueError, match="symlink"):
        _ = build_from_layer(layer, sources, _target(), tmp_path / "out")


@pytest.mark.ac("AC-4")
def test_build_from_layer_never_writes_through_a_symlinked_output_directory(tmp_path: Path) -> None:
    layer = _hand_layer(tmp_path / "layer")
    sources = _sources(tmp_path / "sources")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "out").symlink_to(elsewhere)

    with pytest.raises(ValueError, match="output directory must not be a symlink"):
        _ = build_from_layer(layer, sources, _target(), tmp_path / "out")

    assert list(elsewhere.iterdir()) == []


@pytest.mark.ac("AC-4")
def test_build_from_layer_replaces_a_symlink_at_the_output_path_without_following_it(tmp_path: Path) -> None:
    layer = _hand_layer(tmp_path / "layer")
    sources = _sources(tmp_path / "sources")
    first = build_from_layer(layer, sources, _target(), tmp_path / "out")
    victim = tmp_path / "victim"
    _ = victim.write_text("keep")
    first.unlink()
    first.symlink_to(victim)

    again = build_from_layer(layer, sources, _target(), tmp_path / "out")

    assert again == first and not again.is_symlink()
    assert victim.read_text() == "keep"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [first.name]


@pytest.mark.ac("AC-3")
def test_config_errors_name_the_target_table(tmp_path: Path, copy_repo_subset: CopySubset) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _write(skill, _table("good"), _table("bad", extra='groups = "oops"\n'))

    with pytest.raises(ConfigError, match=r"\[\[target\]\] #2 \(bad\)"):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
def test_a_config_made_in_memory_gets_the_checks_of_a_parsed_one() -> None:
    for bad_name in ("../escape", "", "a/b"):
        with pytest.raises(ConfigError, match="safe filename component"):
            _ = WedgeConfig(name=bad_name, entry="app:main", source="app.py", repo="o/n")
    with pytest.raises(ConfigError, match="owner/name"):
        _ = WedgeConfig(name="app", entry="app:main", source="app.py", repo="nope")

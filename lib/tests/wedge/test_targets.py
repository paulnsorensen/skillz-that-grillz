"""Multi-target ``wedge.toml`` and the reusable site layer."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Callable

import pytest

import wedge._build as wedge_build
import wedge._lock as wedge_lock
from wedge._build import build, build_from_layer, populate_site_layer, resolve_target, stage_sources
from wedge._bundle import bundle_many
from wedge._cli import bundle_cmd
from wedge._config import ConfigError, load_config, load_targets
from wedge._discover import discover_skills
from wedge._key import compute_key
from wedge._lock import check, load_lock, lock_many
from wedge._publish import publish

FIXTURE_NAME = "cheese-cave"
SOURCE = "../../../fromargs/examples/cheese_cave.py"


def _multi(skill: Path, *names: str, extra: str = "") -> None:
    """Rewrite the fixture's wedge.toml into ``[[target]]`` tables over one source."""
    header = (
        'project = "../../../fromargs"\n'
        'include = ["../../../fromargs/src/fromargs"]\n'
        'repo = "paulnsorensen/skillz-that-grillz"\n'
    )
    tables = "".join(
        f'\n[[target]]\nname = "{name}"\nentry = "cheese_cave:main"\nsource = "{SOURCE}"\n' for name in names
    )
    _ = (skill / "wedge.toml").write_text(header + extra + tables)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.ac("AC-3")
def test_target_tables_parse_into_one_config_each(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _multi(skill, "cave-a", "cave-b")

    targets = load_targets(skill)

    assert [t.name for t in targets] == ["cave-a", "cave-b"]
    assert all(t.multi and t.repo == "paulnsorensen/skillz-that-grillz" for t in targets)
    assert targets[0].include == ("../../../fromargs/src/fromargs",)
    assert compute_key(skill, targets[0]) != compute_key(skill, targets[1])
    assert discover_skills([skill.parent]) == [skill]


@pytest.mark.ac("AC-3")
def test_a_target_table_overrides_the_top_level_defaults(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _multi(skill, "cave-a", extra='groups = ["extra"]\n')
    config = skill / "wedge.toml"
    _ = config.write_text(config.read_text() + 'groups = ["other"]\n')

    assert load_targets(skill)[0].groups == ("other",)


@pytest.mark.ac("AC-3")
def test_mixed_single_and_table_forms_are_a_config_error(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _multi(skill, "cave-a", extra='name = "cave"\n')

    with pytest.raises(ConfigError, match="cannot combine with .*target"):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
def test_duplicate_target_names_are_a_config_error(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _multi(skill, "cave", "cave")

    with pytest.raises(ConfigError, match="duplicate target names"):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
def test_an_unknown_key_in_a_target_table_is_a_config_error(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _multi(skill, "cave-a")
    config = skill / "wedge.toml"
    _ = config.write_text(config.read_text() + 'repo = "owner/other"\n')

    with pytest.raises(ConfigError, match=r"unknown key 'repo' in \[\[target\]\]"):
        _ = load_targets(skill)


@pytest.mark.ac("AC-3")
def test_lock_check_and_release_publish_reject_target_tables(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _multi(skill, "cave-a", "cave-b")
    message = "tables work only with build and bundle"

    with pytest.raises(ConfigError, match=message):
        _ = load_config(skill)

    def no_build(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("lock must reject [[target]] tables before it builds")

    monkeypatch.setattr(wedge_lock, "build_many", no_build)
    locked = lock_many([skill])
    assert len(locked) == 1, "one rejection per skill directory, not one per target"
    assert locked[0].error is not None and message in locked[0].error

    issues = check([skill])
    assert len(issues) == 1 and message in issues[0].reason

    published = publish([skill], repo="paulnsorensen/skillz-that-grillz", target="deadbeef")
    assert {name: r["status"] for name, r in published.items()} == {"cave-a": "failed", "cave-b": "failed"}
    assert all(message in r["reason"] for r in published.values())


@pytest.mark.ac("AC-3")
def test_publish_skips_each_direct_bundle_target_of_a_multi_target_skill(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _multi(skill, "cave-a", "cave-b")
    assert [o.error for o in bundle_many([skill])] == [None, None]

    result = publish([skill], repo="paulnsorensen/skillz-that-grillz", target="deadbeef")

    assert result == {
        "cave-a": {"status": "skipped", "reason": "direct bundle; no release asset"},
        "cave-b": {"status": "skipped", "reason": "direct bundle; no release asset"},
    }


@pytest.mark.ac("AC-3")
def test_bundle_command_checks_a_two_target_skill(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _multi(skill, "cave-a", "cave-b")

    written = bundle_cmd(skill_dir=[str(skill)])
    checked = bundle_cmd(skill_dir=[str(skill)], check=True)

    assert sorted(written) == sorted(checked) == ["cave-a", "cave-b"]
    assert written["cave-a"]["key"] != written["cave-b"]["key"]


@pytest.mark.ac("AC-3")
def test_single_target_form_keeps_its_config_and_key(fixture_skill_dir: Path) -> None:
    (only,) = load_targets(fixture_skill_dir)

    assert not only.multi
    assert only.name == FIXTURE_NAME and only.groups == ()
    assert compute_key(fixture_skill_dir, only) == load_lock(fixture_skill_dir, FIXTURE_NAME).key


@pytest.mark.ac("AC-3")
def test_bundle_writes_and_checks_every_target(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path], rewrite_pyz: Callable[..., Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _multi(skill, "cave-a", "cave-b")

    written = bundle_many([skill])

    assert [o.error for o in written] == [None, None]
    assert sorted(p.name for p in (skill / "scripts").iterdir()) == ["cave-a.pyz", "cave-b.pyz"]
    assert [o.error for o in bundle_many([skill], check=True)] == [None, None]

    stale = skill / "scripts" / "cave-b.pyz"
    edited = rewrite_pyz(stale, tmp_path / "edited.pyz", extra={"site-packages/extra.py": b"x = 1\n"})
    _ = stale.write_bytes(edited.read_bytes())
    errors = [o.error for o in bundle_many([skill], check=True)]
    assert errors[0] is None
    assert errors[1] is not None and "cave-b.pyz" in errors[1]


@pytest.mark.ac("AC-4")
def test_a_layer_builds_byte_identical_output_without_the_network(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    fresh = build(skill, tmp_path / "fresh")
    (target,) = load_targets(skill)
    project = skill.parent.parent.parent / "fromargs"

    populated: list[Path] = []
    original = wedge_build._install_third_party  # pyright: ignore[reportPrivateUsage]

    def counting(requirements: str, site_dir: Path) -> None:
        populated.append(site_dir)
        original(requirements, site_dir)

    monkeypatch.setattr(wedge_build, "_install_third_party", counting)
    layer = populate_site_layer(project, target.groups, tmp_path / "layer")
    assert len(populated) == 1
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith((".", "layer"))) == ["layer", "layer.key"], (
        "populate renames its staging directory into place and leaves only the layer and its key file"
    )
    sources = stage_sources(resolve_target(skill, target), tmp_path / "sources")
    before = _tree_digest(layer.path)

    def no_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("build_from_layer must not install third-party packages")

    monkeypatch.setattr(wedge_build, "_install_third_party", no_network)
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
    monkeypatch.setenv("UV_OFFLINE", "1")
    first = build_from_layer(layer, sources, target, tmp_path / "out-1")
    second = build_from_layer(layer, sources, target, tmp_path / "out-2")

    assert first.name == fresh.path.name
    assert first.read_bytes() == fresh.path.read_bytes() == second.read_bytes()
    assert _tree_digest(layer.path) == before, "a build must leave the shared layer unchanged"
    assert not (layer.path / "cheese_cave.py").exists()
    assert not (layer.path / "fromargs").exists(), "first-party source never enters the layer"
    assert len(populated) == 1


@pytest.mark.ac("AC-4")
def test_one_layer_serves_every_target_of_a_skill(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _multi(skill, "cave-a", "cave-b")
    a, b = load_targets(skill)
    layer = populate_site_layer(skill.parent.parent.parent / "fromargs", a.groups, tmp_path / "layer")
    reference = bundle_many([skill])

    built = [
        build_from_layer(layer, stage_sources(resolve_target(skill, t), tmp_path / f"src-{t.name}"), t, tmp_path / "out")
        for t in (a, b)
    ]

    assert len(built) == 2
    for path, outcome in zip(built, reference):
        assert outcome.value is not None
        assert path.read_bytes() == outcome.value.path.read_bytes()


@pytest.mark.ac("AC-4")
def test_a_layer_rejects_a_target_with_other_groups(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    (target,) = load_targets(skill)
    layer = populate_site_layer(skill.parent.parent.parent / "fromargs", (), tmp_path / "layer")
    sources = stage_sources(resolve_target(skill, target), tmp_path / "sources")
    other = replace(target, groups=("extra",))

    with pytest.raises(ValueError, match="do not match the layer"):
        _ = build_from_layer(layer, sources, other, tmp_path / "out")


@pytest.mark.ac("AC-4")
def test_a_layer_rejects_first_party_source_that_shadows_an_installed_path(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path]
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    (target,) = load_targets(skill)
    layer = populate_site_layer(skill.parent.parent.parent / "fromargs", (), tmp_path / "layer")
    sources = tmp_path / "sources"
    (sources / "cyclopts").mkdir(parents=True)

    with pytest.raises(ConfigError, match="build source 'cyclopts' shadows installed 'cyclopts'"):
        _ = build_from_layer(layer, sources, target, tmp_path / "out")

"""AC-W2: build_many exports each skill's groups and splits sites on them."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Callable

import pytest

import wedge._build as wedge_build
from wedge._config import WedgeConfig

FIXTURE_NAME = "cheese-cave"


@pytest.mark.ac("AC-W2")
def test_build_many_exports_each_skills_groups_and_splits_sites(
    tmp_path: Path,
    copy_repo_subset: Callable[[Path], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two skills that differ only in ``groups`` must not share a site: a
    mutation that drops ``groups`` from the site key would merge them and
    export the wrong closure to one of them."""
    skill_a = copy_repo_subset(tmp_path / "checkout-a")
    skill_b = copy_repo_subset(tmp_path / "checkout-b")
    config_a = skill_a / "wedge.toml"
    _ = config_a.write_text(config_a.read_text().replace(f'name = "{FIXTURE_NAME}"', 'name = "cheese-cave-a"'))
    config_b = skill_b / "wedge.toml"
    _ = config_b.write_text(
        config_b.read_text().replace(f'name = "{FIXTURE_NAME}"', 'name = "cheese-cave-b"')
        + '\ngroups = ["extra"]\n'
    )

    export_calls: list[tuple[str, ...]] = []
    populated_groups: list[tuple[str, ...]] = []

    def _fake_export(_project: Path, groups: Sequence[str] = ()) -> str:
        export_calls.append(tuple(groups))
        return ""

    def _fake_populate(project: Path, groups: Sequence[str], dest: Path) -> wedge_build.SiteLayer:
        populated_groups.append(tuple(groups))
        _ = _fake_export(project, groups)
        dest.mkdir()
        return wedge_build.SiteLayer(dest, "fake", tuple(sorted(set(groups))), project)

    def _fake_build(
        _layer: wedge_build.SiteLayer, _sources: Path, target: WedgeConfig, out_dir: Path
    ) -> tuple[str, Path]:
        out_path = out_dir / f"{target.name}.pyz"
        _ = out_path.write_bytes(b"fake")
        return "deadbeef", out_path

    monkeypatch.setattr(wedge_build, "populate_site_layer", _fake_populate)
    monkeypatch.setattr(wedge_build, "_build_from_layer", _fake_build)

    outcomes = wedge_build.build_many([skill_a, skill_b], tmp_path / "out")

    assert all(o.error is None for o in outcomes), [o.error for o in outcomes]
    assert len(populated_groups) == 2
    assert sorted(export_calls) == [(), ("extra",)]

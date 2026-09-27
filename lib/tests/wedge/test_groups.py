"""AC-W2: build_many exports each skill's groups and splits sites on them."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

import wedge._build as wedge_build

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
    populated_sites: list[wedge_build.SiteInputs] = []

    def _fake_export(_project: Path, groups: tuple[str, ...] = ()) -> str:
        export_calls.append(tuple(groups))
        return ""

    def _fake_populate(site: wedge_build.SiteInputs, site_dir: Path) -> None:
        populated_sites.append(site)
        _ = _fake_export(site.project, site.groups)
        site_dir.mkdir()

    def _fake_shiv(
        prepared: wedge_build._Prepared,  # pyright: ignore[reportPrivateUsage]
        _site_dir: Path,
        out_dir: Path,
    ) -> wedge_build.BuildResult:
        out_path = out_dir / f"{prepared.config.name}.pyz"
        _ = out_path.write_bytes(b"fake")
        return wedge_build.BuildResult(
            name=prepared.config.name, key=prepared.key, content_sha256="deadbeef", path=out_path
        )

    monkeypatch.setattr(wedge_build, "_populate_site", _fake_populate)
    monkeypatch.setattr(wedge_build, "_shiv_skill", _fake_shiv)

    outcomes = wedge_build.build_many([skill_a, skill_b], tmp_path / "out")

    assert all(o.error is None for o in outcomes), [o.error for o in outcomes]
    assert len(populated_sites) == 2
    assert sorted(export_calls) == [(), ("extra",)]

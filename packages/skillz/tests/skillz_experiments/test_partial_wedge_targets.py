"""A wedge target whose sources exceed the edit limit stays editable in part: its own package, then the named files.

The builds run offline: a fake closure replaces the network step, while shiv and the archive code run for real.
"""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

import wedge._build as wedge_build

from skillz_experiments._candidate import Candidate
from skillz_experiments.run import _plan
from skillz_experiments.run._wedge_fit import WEDGE_EDIT_LIMIT
from skillz_experiments.run._wedge_targets import TargetPlan, audit_targets, plan_targets
from test_wedge_targets import Recorder, World, archive_text, evaluate, prepared, record_of

pytestmark = pytest.mark.usefixtures("host_login")

OWN = "@wedge/proj/pkg/echo_skill/commands.py"
OWN_INIT = "@wedge/proj/pkg/echo_skill/__init__.py"
SHARED = [f"@wedge/proj/pkg/shared/part{index}.py" for index in range(3)]
COMMANDS = 'def main() -> None:\n    print("echo v1")\n'
WEDGE_TOML = """project = "../proj"
repo = "o/n"
name = "echo"
entry = "pkg.echo_skill.commands:main"
source = "../proj/pkg"
source_paths = ["__init__.py", "echo_skill", "shared"]
"""


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_target: Callable[..., Path]) -> World:
    """A skill whose one target holds a small own package and a shared tree over the wedge edit limit."""
    def closure(_project: Path, _groups: object) -> tuple[str, bool]:
        return "", False

    monkeypatch.setattr(wedge_build, "_closure_requirements", closure)
    repo = tmp_path / "repo"
    _ = subprocess.run(["git", "init", "-q", str(repo)], check=True)
    skill = make_target(repo)
    proj = repo / "proj"
    proj.mkdir()
    _ = (proj / "pyproject.toml").write_text('[project]\nname = "proj"\nversion = "0"\n')
    _ = (proj / "uv.lock").write_text("lock\n")
    built = World(repo, proj, skill)
    built.put("pkg/__init__.py", "")
    built.put("pkg/echo_skill/__init__.py", "")
    built.put("pkg/echo_skill/commands.py", COMMANDS)
    built.put("pkg/other/__init__.py", "OTHER = 1\n")
    for index in range(3):
        built.put(f"pkg/shared/part{index}.py", f"PART = {index}\n# " + "s" * (WEDGE_EDIT_LIMIT // 2) + "\n")
    _ = (skill / "wedge.toml").write_text(WEDGE_TOML)
    (skill / "scripts").mkdir(exist_ok=True)
    built.build()
    return built


def _plan_of(world: World) -> TargetPlan:
    contract = world.contract("scripts/echo.pyz")
    return plan_targets(world.skill, Candidate.capture(world.skill, [], contract).files, contract, "prose+cli")


def test_an_oversized_shared_tree_leaves_the_own_package_editable_and_the_shared_tree_fixed(world: World) -> None:
    plan = _plan_of(world)
    assert [target.name for target in plan.editable] == ["echo"] and plan.reasons == {}
    assert sorted(plan.sources) == [OWN_INIT, OWN]
    target = plan.editable[0]
    assert set(SHARED) | {"@wedge/proj/pkg/__init__.py"} <= set(target.fixed)
    assert "@wedge/proj/pkg/other/__init__.py" not in target.names
    record = plan.record()
    assert cast(dict[str, list[str]], record["editable"])["echo"] == [OWN_INIT, OWN]
    assert set(SHARED) <= set(cast(dict[str, list[str]], record["fixed"])["echo"])
    assert f"wedge edit limit of {WEDGE_EDIT_LIMIT}" in cast(dict[str, str], record["partial"])["echo"]
    assert audit_targets(world.skill).targets[0].frozen == ""


def test_a_shared_file_that_skill_md_names_joins_the_editable_part_while_it_fits(world: World) -> None:
    world.mention("Edit shared/part1.py and pkg.shared.part2 when the echo output changes.")
    plan = _plan_of(world)
    assert sorted(plan.sources) == [OWN_INIT, OWN, SHARED[1]]
    assert SHARED[2] in plan.editable[0].fixed and SHARED[0] in plan.editable[0].fixed


def test_a_target_that_fits_whole_keeps_every_source_editable_and_records_no_partial(world: World) -> None:
    for index in range(3):
        world.put(f"pkg/shared/part{index}.py", f"PART = {index}\n")
    world.build()
    plan = _plan_of(world)
    assert set(plan.sources) == {OWN_INIT, OWN, *SHARED, "@wedge/proj/pkg/__init__.py"}
    assert "partial" not in plan.record()


def test_an_own_package_over_the_limit_stays_frozen(world: World) -> None:
    world.put("pkg/echo_skill/commands.py", COMMANDS + "# " + "o" * WEDGE_EDIT_LIMIT + "\n")
    world.build()
    plan = _plan_of(world)
    assert plan.editable == () and "echo" in plan.reasons


def test_a_run_rebuilds_the_partial_target_with_the_fixed_shared_files_from_the_seed(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    contract = world.contract("scripts/echo.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    names = _plan.editable_files(skeleton.files | plan.sources, contract, "prose+cli", skeleton.frozen.keys(), plan)
    assert OWN in names and not set(SHARED) & set(names)
    out = prepared(world, tmp_path, write_draft, approvals)
    owned = cast(dict[str, object], record_of(out)["own_targets"])
    assert "echo" in cast(dict[str, str], owned["partial"])
    recorder = Recorder()
    changed = COMMANDS.replace("v1", "v2")
    _ = evaluate(out, recorder, lambda components: components.update({OWN: changed}))
    rebuilt = recorder.seen[0].frozen["scripts/echo.pyz"]
    assert archive_text(rebuilt, "echo_skill/commands.py") == changed
    assert archive_text(rebuilt, "shared/part1.py").startswith("PART = 1\n")

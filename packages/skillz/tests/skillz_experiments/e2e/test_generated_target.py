"""Joined offline CLI coverage for one generated wedge target."""
from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import cast

import pytest

from support import (HARNESS_RUNS, ROOT, Scenario, archive_witnesses, assert_promoted, assert_replayed, build_wedge,
                     call_cli, make_scenario, run_scenario, snapshot, uv_bin_dir)
from fixtures.targets import NEW_MODULE


def _lock_cyclopts(project: Path) -> None:
    """Pin `project` to the cyclopts version of the workspace lock, and copy its lock entries from that lock.

    `uv sync` stores wheels but no index data, so `uv lock --offline` cannot resolve cyclopts on a cold cache.
    The workspace lock holds cyclopts and its dependencies, so uv checks this lock without the index.
    `uv lock --offline --locked` also fails when this lock differs from the lock that uv writes for `project`.
    """
    text = (ROOT / "uv.lock").read_text()
    head, *blocks = text.split("\n[[package]]\n")
    lock = cast(dict[str, object], tomllib.loads(text))
    tables = cast(list[dict[str, object]], lock["package"])
    locked = {cast(str, table["name"]): (table, block.rstrip("\n"))
              for table, block in zip(tables, blocks, strict=True)}
    version = cast(str, locked["cyclopts"][0]["version"])
    _ = (project / "pyproject.toml").write_text(
        f'[project]\nname = "offline-proj"\nversion = "0"\nrequires-python = {json.dumps(lock["requires-python"])}\n'
        + f'dependencies = ["cyclopts=={version}"]\n'
    )
    closure: set[str] = set()
    pending = ["cyclopts"]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure.add(name)
            dependencies = cast(list[dict[str, object]], locked[name][0].get("dependencies", []))
            pending += [cast(str, dependency["name"]) for dependency in dependencies]
    root = ('name = "offline-proj"\nversion = "0"\nsource = { virtual = "." }\n'
            + 'dependencies = [\n    { name = "cyclopts" },\n]\n\n[package.metadata]\n'
            + f'requires-dist = [{{ name = "cyclopts", specifier = "=={version}" }}]')
    packages = sorted([("offline-proj", root), *((name, locked[name][1]) for name in closure)])
    # Keep the top-level keys of the workspace lock; drop its `[manifest]` table of workspace members.
    header = head.split("\n[", 1)[0].rstrip("\n")
    _ = (project / "uv.lock").write_text(header + "".join(f"\n\n[[package]]\n{block}" for _, block in packages) + "\n")
    checked = subprocess.run(["uv", "lock", "--offline", "--locked", "--project", str(project)],
                             capture_output=True, text=True, check=False)
    assert checked.returncode == 0, checked.stderr


def _world(base: Path) -> tuple[Scenario, str]:
    _ = subprocess.run(["git", "init", "-q", str(base)], check=True)
    scenario = make_scenario(base)
    skill = scenario.target
    _lock_cyclopts(skill)
    source = skill / "src/alpha/__init__.py"
    source.parent.mkdir(parents=True)
    _ = source.write_text('def main() -> None:\n    print("alpha")\n')
    include = skill / "lib/fromargs/__init__.py"
    include.parent.mkdir(parents=True)
    _ = include.write_text("")
    _ = (skill / "wedge.toml").write_text(
        'project = "."\nrepo = "offline/test"\nname = "alpha"\nentry = "alpha:main"\n'
        + 'source = "src/alpha"\ninclude = ["lib/fromargs"]\n'
    )
    built = build_wedge(skill, base / "initial-build")
    assert set(built) == {"alpha"}
    scripts = skill / "scripts"
    scripts.mkdir(exist_ok=True)
    _ = shutil.copy2(Path(built["alpha"]["path"]), scripts / "alpha.pyz")
    original = (skill / "SKILL.md").read_text() + "\nExisting scripts/alpha.pyz is available.\n"
    _ = (skill / "SKILL.md").write_text(original)
    return Scenario(skill, scenario.out, scenario.export, scenario.draft, original), built["alpha"]["content_sha256"]


@pytest.mark.ac("skillz-experiments-end-to-end:AC-1")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-5")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-6")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-10")
@pytest.mark.ac("skillz-autoimprove-claims:AC-4")
@pytest.mark.parametrize("harness, cli", HARNESS_RUNS)
def test_new_cli_rebuilds_executes_and_exports_from_real_cli(
        tmp_path: Path, harness: str, cli: Path) -> None:
    scenario, initial_digest = _world(tmp_path)
    before = snapshot(scenario.target)
    home = tmp_path / "home"
    home.mkdir()
    result = run_scenario(scenario, home, helper=True, mode="targets", harness=harness,
                          cli=cli, bin_dir=uv_bin_dir())
    assert_promoted(scenario, result)
    assert_replayed(scenario.out)
    assert snapshot(scenario.target) == before

    code, exported, error = call_cli(["export", str(scenario.out), "--out", str(scenario.export)], home, cli=cli)
    assert code == 0, (exported, error)
    assert exported["installed"] is False
    assert exported["patches"] == ["candidate.patch"]
    assert scenario.export.stat().st_mode & 0o077 == 0
    assert (scenario.export / "candidate.patch").stat().st_mode & 0o077 == 0
    copy = tmp_path / "replayed"
    _ = shutil.copytree(scenario.target, copy)
    applied = subprocess.run(["git", "apply", str(scenario.export / "candidate.patch")], cwd=copy,
                             capture_output=True, text=True, check=False)
    assert applied.returncode == 0, applied.stderr
    assert (copy / "src/transform_cli/__init__.py").read_text() == NEW_MODULE
    assert "transform-cli" in (copy / "wedge.toml").read_text()

    built = build_wedge(copy, tmp_path / "rebuilt")
    assert set(built) == {"alpha", "transform-cli"}
    generated = Path(built["transform-cli"]["path"])
    generated_digest = built["transform-cli"]["content_sha256"]
    assert built["alpha"]["content_sha256"] == initial_digest
    assert archive_witnesses(scenario) == {generated_digest}
    _ = (home / "fixture.md").write_text("held value\n")
    executed = subprocess.run(["/usr/bin/python3", "-I", str(generated), "fixture.md"], cwd=home,
                              capture_output=True, text=True, check=False)
    assert executed.returncode == 0, executed.stderr
    assert json.loads(executed.stdout) == {"text": "HELD VALUE\n"}

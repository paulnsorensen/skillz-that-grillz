"""Joined offline CLI coverage for existing wedge targets."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from support import (HARNESS_RUNS, Scenario, archive_witnesses, assert_promoted, assert_replayed, build_wedge, call_cli,
                     make_scenario, run_scenario, snapshot, uv_bin_dir)

SOURCE = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "def main() -> None:\n"
    "    text = Path(sys.argv[1]).read_text()\n"
    "    text = text\n"
    "    print(json.dumps({'text': text}))\n"
)


def _world(base: Path) -> tuple[Scenario, str]:
    _ = subprocess.run(["git", "init", "-q", str(base)], check=True)
    scenario = make_scenario(base)
    skill = scenario.target
    project = base / "proj"
    project.mkdir()
    _ = (project / "pyproject.toml").write_text('[project]\nname = "offline-proj"\nversion = "0"\n')
    _ = subprocess.run(["uv", "lock", "--offline", "--project", str(project)], check=True,
                       capture_output=True, text=True)
    _ = (project / "transform.py").write_text(SOURCE)
    _ = (skill / "wedge.toml").write_text(
        'project = "../proj"\nrepo = "offline/test"\n'
        + 'name = "transform"\nentry = "transform:main"\nsource = "../proj/transform.py"\n'
    )
    built = build_wedge(skill, base / "initial-build")
    assert set(built) == {"transform"}
    (skill / "scripts").mkdir(exist_ok=True)
    _ = shutil.copy2(Path(built["transform"]["path"]), skill / "scripts/transform.pyz")
    original = (skill / "SKILL.md").read_text() + "\nUse scripts/transform.pyz for fixture.md.\n"
    _ = (skill / "SKILL.md").write_text(original)
    return Scenario(skill, scenario.out, scenario.export, scenario.draft, original), built["transform"]["content_sha256"]


@pytest.mark.ac("skillz-experiments-end-to-end:AC-1")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-5")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-6")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-10")
@pytest.mark.ac("skillz-autoimprove-claims:AC-4")
@pytest.mark.parametrize("harness, cli", HARNESS_RUNS)
def test_external_wedge_rebuilds_and_exports_real_behavior(
        tmp_path: Path, harness: str, cli: Path) -> None:
    scenario, initial_digest = _world(tmp_path)
    before = snapshot(scenario.target)
    external = snapshot(tmp_path / "proj")
    home = tmp_path / "home"
    home.mkdir()
    result = run_scenario(scenario, home, helper=True, mode="targets", harness=harness,
                          cli=cli, bin_dir=uv_bin_dir())
    assert_promoted(scenario, result)
    assert_replayed(scenario.out)
    assert snapshot(scenario.target) == before
    assert snapshot(tmp_path / "proj") == external

    code, exported, error = call_cli(["export", str(scenario.out), "--out", str(scenario.export)], home, cli=cli)
    assert code == 0, (exported, error)
    assert exported["installed"] is False
    assert scenario.export.stat().st_mode & 0o077 == 0
    assert set(cast(list[str], exported["patches"])) == {"candidate.patch", "wedge-sources.patch"}
    for name in cast(list[str], exported["patches"]):
        assert (scenario.export / name).stat().st_mode & 0o077 == 0

    copy = tmp_path / "replayed"
    skill = copy / "transform-skill"
    _ = shutil.copytree(scenario.target, skill)
    _ = shutil.copytree(tmp_path / "proj", copy / "proj")
    for name, directory in (("candidate.patch", skill), ("wedge-sources.patch", copy)):
        patch = scenario.export / name
        assert patch.is_file()
        applied = subprocess.run(["git", "apply", str(patch)], cwd=directory,
                                 capture_output=True, text=True, check=False)
        assert applied.returncode == 0, applied.stderr
    source = copy / "proj/transform.py"
    assert "text = text.upper()" in source.read_text()
    built = build_wedge(skill, tmp_path / "rebuild")
    assert set(built) == {"transform"}
    rebuilt = Path(built["transform"]["path"])
    rebuilt_digest = built["transform"]["content_sha256"]
    assert rebuilt_digest != initial_digest
    assert archive_witnesses(scenario) == {rebuilt_digest}
    _ = (home / "fixture.md").write_text("held value\n")
    execution = subprocess.run(["/usr/bin/python3", "-I", str(rebuilt), "fixture.md"],
                               cwd=home, capture_output=True, text=True, check=False)
    assert execution.returncode == 0, execution.stderr
    assert json.loads(execution.stdout) == {"text": "HELD VALUE\n"}

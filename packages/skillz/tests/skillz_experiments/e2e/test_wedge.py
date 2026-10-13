"""Joined offline CLI coverage for existing wedge targets."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from support import (BUNDLE, CLI, Scenario, build_wedge, call_cli, make_scenario, read_cases, read_events,
                     read_record, run_scenario, snapshot)

SOURCE = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "def main() -> None:\n"
    "    text = Path(sys.argv[1]).read_text()\n"
    "    text = text\n"
    "    print(json.dumps({'text': text}))\n"
)



def assert_promoted(scenario: Scenario, result: dict[str, object]) -> None:
    record = read_record(scenario.out)
    gate = cast(dict[str, object], result["gate"])
    assert (gate["verdict"], gate["delta"], gate["se"], gate["n_shared"],
            gate["cases"], gate["repeats"]) == ("promote", 1.0, 0.0, 6, 6, 3)
    held = {cast(str, case["id"]) for case in read_cases(scenario.out) if case["split"] == "holdout"}
    assert len(held) == 6
    outcomes = [cast(dict[str, object], item) for item in cast(list[object], record["outcomes"])
                if cast(dict[str, object], item).get("split") == "holdout"]
    assert len(outcomes) == 36
    for case_id in held:
        for arm, score in (("baseline", 0.0), ("winner", 1.0)):
            selected = [item for item in outcomes if item["case_id"] == case_id and item["arm"] == arm]
            assert sorted(cast(int, item["repeat"]) for item in selected) == [0, 1, 2]
            assert {item["score"] for item in selected} == {score}


def archive_witnesses(scenario: Scenario) -> set[str]:
    winner = read_record(scenario.out)["winner_hash"]
    return {cast(str, event["archive_sha256"]) for event in read_events(scenario.out)
            if event.get("role") == "task" and event.get("candidate") == winner
            and "archive_sha256" in event}


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


def uv_bin_dir() -> Path:
    executable = shutil.which("uv")
    assert executable is not None
    path = Path(executable)
    if path.parent.name == "shims":
        selected = subprocess.run(["mise", "which", "uv"], capture_output=True,
                                  text=True, check=True)
        path = Path(selected.stdout.strip())
    assert path.is_file()
    return path.parent


@pytest.mark.ac("skillz-experiments-end-to-end:AC-1")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-5")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-6")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-10")
@pytest.mark.parametrize("cli", [CLI, BUNDLE], ids=["source", "bundle"])
@pytest.mark.parametrize("harness", ["command", "claude", "codex"])
def test_external_wedge_rebuilds_and_exports_real_behavior(
        tmp_path: Path, cli: Path, harness: str) -> None:
    scenario, initial_digest = _world(tmp_path)
    before = snapshot(scenario.target)
    external = snapshot(tmp_path / "proj")
    home = tmp_path / "home"
    home.mkdir()
    result = run_scenario(scenario, home, helper=True, mode="targets", harness=harness,
                          cli=cli, bin_dir=uv_bin_dir())
    assert_promoted(scenario, result)
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

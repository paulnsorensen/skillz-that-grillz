from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate, snapshot_outputs
from skillz_experiments._cases import load_cases, mapping
from skillz_experiments._records import prepare, read

SKILLZ_CONTRACT = Path(__file__).resolve().parents[3] / "skills/skillz/evals/autoimprove.json"


def test_cli_dataset_is_local_and_complete(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    _ = (target / "SKILL.md").write_text("---\nname: skillz\ndescription: test\n---\nInspect.\n")
    (target / "evals").mkdir()
    _ = (target / "evals/autoimprove.json").write_text(SKILLZ_CONTRACT.read_text())
    manifest = tmp_path / "cases.json"
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": [
        {"id": split, "family": split, "split": split, "request": "Inspect SKILL.md",
         "files": {"SKILL.md": "---\nname: fixture\n---\nBody\n"}, "expected": {"name": "fixture"},
         "provenance": "public", "provider_approved": True}
        for split in ["train", "validation", "holdout"]]}))
    out = tmp_path / "run"
    command = [sys.executable, "-m", "skillz_experiments", "dataset", str(manifest),
               "--target", str(target), "--out", str(out)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    record = mapping(cast(object, json.loads((out / "run.json").read_text())))
    assert record["phase"] == "prepared"
    assert record["calls"] == 0
    assert record["contract_source"] == "skill" and mapping(record["contract"])["skill"] == "skillz"
    assert "evals/autoimprove.json" not in mapping(record["seed"])
    assert (out / "run.json").stat().st_mode & 0o077 == 0
    assert "feedback" not in mapping(cast(object, json.loads(result.stdout)))


def test_host_alias_directory_passes_but_package_symlinks_fail(tmp_path: Path) -> None:
    physical = tmp_path / "physical"
    package = physical / "skill"
    package.mkdir(parents=True)
    _ = (package / "SKILL.md").write_text("seed")
    manifest = physical / "cases.json"
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": []}))
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    assert Candidate.capture(alias / "skill", ["SKILL.md"]).files == {"SKILL.md": "seed"}
    assert load_cases(alias / "cases.json") == []
    (package / "linked.md").symlink_to(package / "SKILL.md")
    with pytest.raises(ValueError, match="symlinks"):
        _ = Candidate.capture(alias / "skill", ["SKILL.md"])
    (package / "linked.md").unlink()
    (tmp_path / "leaf").symlink_to(package, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        _ = Candidate.capture(tmp_path / "leaf", ["SKILL.md"])
    (tmp_path / "leaf.json").symlink_to(manifest)
    with pytest.raises(ValueError, match="symlink"):
        _ = load_cases(tmp_path / "leaf.json")


def _git(path: Path, *args: str) -> None:
    _ = subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def test_capture_keeps_files_when_the_skill_root_sits_in_an_ignored_directory(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _ = (tmp_path / ".gitignore").write_text(".agents/\n")
    skill = tmp_path / ".agents/skills/foo"
    skill.mkdir(parents=True)
    _ = (skill / "SKILL.md").write_text("seed")
    assert Candidate.capture(skill, ["SKILL.md"]).files == {"SKILL.md": "seed"}


def test_capture_skips_ignored_hidden_files_symlinks_and_nested_git(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    (skill / ".venv/bin").mkdir(parents=True)
    _git(skill, "init", "-q")
    _ = (skill / ".gitignore").write_text(".gitignore\n.venv/\n.DS_Store\n")
    _ = (skill / "SKILL.md").write_text("seed")
    _ = (skill / ".DS_Store").write_bytes(b"\x00\x01")
    (skill / ".venv/bin/python").symlink_to("/usr/bin/env")
    assert Candidate.capture(skill, ["SKILL.md"]).files == {"SKILL.md": "seed"}


def test_capture_excludes_the_evals_directory_and_the_manifest_under_the_target(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    (target / "evals").mkdir(parents=True)
    (target / "cases").mkdir()
    _ = (target / "SKILL.md").write_text("seed")
    _ = (target / "evals/evals.json").write_text('{"answer": 1}')
    manifest = target / "cases/manifest.json"
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": []}))
    out = tmp_path / "run"
    _ = prepare(manifest, target, out)
    assert read(out / "run.json")["seed"] == {"SKILL.md": "seed"}


def test_snapshot_outputs_works_when_the_workspace_sits_below_a_symlinked_parent(tmp_path: Path) -> None:
    physical = tmp_path / "physical"
    (physical / "workspace").mkdir(parents=True)
    _ = (physical / "workspace/result.txt").write_text("done\n")
    (tmp_path / "alias").symlink_to(physical, target_is_directory=True)
    assert snapshot_outputs(tmp_path / "alias/workspace") == {"result.txt": "done\n"}
    (physical / "workspace/real").mkdir()
    _ = (physical / "workspace/real/inner.txt").write_text("x")
    (physical / "workspace/link").symlink_to(physical / "workspace/real", target_is_directory=True)
    assert "link/inner.txt" not in snapshot_outputs(tmp_path / "alias/workspace")

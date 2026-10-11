from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from skillz_experiments._candidate import Candidate, snapshot_outputs
from skillz_experiments._cases import load_cases

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


def test_capture_excludes_the_evals_directory_under_the_target(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    (target / "evals").mkdir(parents=True)
    _ = (target / "SKILL.md").write_text("seed")
    _ = (target / "evals/evals.json").write_text('{"answer": 1}')
    assert Candidate.capture(target, []).files == {"SKILL.md": "seed"}


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

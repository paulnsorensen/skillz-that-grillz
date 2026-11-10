"""A contract companion may be `../<name>`: a sibling directory of the target, staged as `.claude/skills/<name>`."""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from in_process_claude import in_process_claude
from skillz_experiments import _cli
from skillz_experiments._budget import Budget
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import CodedError
from skillz_experiments._contract import load_contract, parse
from skillz_experiments.harness import Configuration

Maker = Callable[..., Path]
SIBLING_SKILL = "---\nname: sibling\ndescription: s\n---\n"
pytestmark = pytest.mark.usefixtures("host_login", "claude_in_process")


def sibling_target(tmp_path: Path, make_target: Maker, companions: list[str], name: str = "sibling") -> Path:
    target = make_target(tmp_path, surface={"companions": companions})
    (tmp_path / name / "references").mkdir(parents=True)
    _ = (tmp_path / name / "SKILL.md").write_text(SIBLING_SKILL)
    _ = (tmp_path / name / "references/notes.md").write_text("notes\n")
    return target


def capture(target: Path) -> Candidate:
    return Candidate.capture(target, ["SKILL.md"], load_contract(target))


@pytest.mark.ac("gh-699:AC-1")
def test_validate_accepts_a_sibling_companion(tmp_path: Path, make_target: Maker,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    target = sibling_target(tmp_path, make_target, ["../sibling"])
    _ = _cli.main(["validate", "--target", str(target)])
    report = cast(dict[str, list[dict[str, str]]], json.loads(capsys.readouterr().out))
    assert "contract-surface" not in {finding["code"] for finding in report["findings"]}


@pytest.mark.ac("gh-699:AC-2")
def test_a_sibling_is_staged_read_only_under_the_project_skills(tmp_path: Path, make_target: Maker) -> None:
    target = sibling_target(tmp_path / "tree", make_target, ["../sibling"])
    candidate = capture(target)
    contract = load_contract(target)
    work = tmp_path / "work"
    work.mkdir()
    executable = in_process_claude(work)
    config = work / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(executable)]}))
    session = Configuration.load(config, "claude-test-model").create(
        "claude-test-model", Budget(10, 120, 0), lambda: None, None, contract.surface, candidate.files)
    try:
        _ = session.transports["task"].invoke("hello", candidate)
    finally:
        session.close()
    log = executable.with_name("claude.log").read_text().splitlines()
    staged = cast(dict[str, str], json.loads(log[0])["staged"])
    assert staged[".claude/skills/sibling"] == "0o555"
    assert staged[".claude/skills/sibling/SKILL.md"] == "0o444"
    assert staged[".claude/skills/sibling/references/notes.md"] == "0o444"


@pytest.mark.ac("gh-699:AC-3")
def test_sibling_files_enter_the_candidate_hash(tmp_path: Path, make_target: Maker) -> None:
    target = sibling_target(tmp_path, make_target, ["../sibling"])
    before = capture(target)
    _ = (tmp_path / "sibling/references/notes.md").write_text("changed\n")
    after = capture(target)
    assert before.identity != after.identity


@pytest.mark.ac("gh-699:AC-3")
def test_the_search_never_edits_a_sibling_file(tmp_path: Path, make_target: Maker) -> None:
    from skillz_experiments.run._plan import editable_files

    target = sibling_target(tmp_path, make_target, ["../sibling"])
    candidate = capture(target)
    assert "@sibling/sibling/SKILL.md" in candidate.files
    names = editable_files(candidate.files, load_contract(target), "prose", candidate.frozen.keys())
    assert names and not any(name.startswith("@sibling/") for name in names)

BAD_FORMS = ["/abs", "../.hidden", "../a/b", "../../x", "a/../b", "../", "..", "../a/", "skills/a/../b"]


def refused(companions: list[str]) -> str:
    document: dict[str, object] = {
        "schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
        "kinds": {"echo": {"grader": "exact-json"}}, "editable": [], "surface": {"companions": companions}}
    with pytest.raises(CodedError) as caught:
        _ = parse(document, "skill")
    return caught.value.code


@pytest.mark.ac("gh-699:AC-4")
@pytest.mark.parametrize("companion", BAD_FORMS)
def test_malformed_sibling_forms_stop_with_contract_surface(companion: str) -> None:
    assert refused([companion]) == "contract-surface"


@pytest.mark.ac("gh-699:AC-4")
def test_two_companions_that_stage_to_one_name_stop_with_contract_surface() -> None:
    assert refused(["../sibling", "skills/sibling"]) == "contract-surface"


def missing_sibling(tmp_path: Path, make_target: Maker) -> Path:
    return make_target(tmp_path, surface={"companions": ["../sibling"]})


def file_sibling(tmp_path: Path, make_target: Maker) -> Path:
    target = missing_sibling(tmp_path, make_target)
    _ = (tmp_path / "sibling").write_text("not a directory")
    return target


def symlink_sibling(tmp_path: Path, make_target: Maker) -> Path:
    target = sibling_target(tmp_path, make_target, ["../sibling"], name="real")
    os.symlink(tmp_path / "real", tmp_path / "sibling")
    return target


def self_sibling(tmp_path: Path, make_target: Maker) -> Path:
    return sibling_target(tmp_path, make_target, ["../echo-skill"], name="unused")


@pytest.mark.ac("gh-699:AC-5")
@pytest.mark.parametrize("build", [missing_sibling, file_sibling, symlink_sibling, self_sibling],
                         ids=["missing", "file", "symlink", "self"])
def test_an_unusable_sibling_stops_the_capture_with_contract_surface(
        tmp_path: Path, make_target: Maker, build: Callable[[Path, Maker], Path]) -> None:
    target = build(tmp_path, make_target)
    with pytest.raises(CodedError) as caught:
        _ = capture(target)
    assert caught.value.code == "contract-surface"

"""Offline checks of the `fromargs-benefit` live scenario: the oracle, the draft check, the CLI check, and the pass rule.

None of them makes a model call, so the `skillz-pytest` gate step runs them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._contract import parse

from fromargs_benefit import CONTRACT, FIXTURE, check_draft, cli_verdict, invocations, match_cli, reference_draft
from semver_oracle import next_version

HERE = Path(__file__).resolve().parent
NAME = "semver-next"


def _log(*commits: str) -> str:
    return "\n---\n".join(commits) + "\n"


@pytest.mark.ac("fromargs-benefit-smoke:AC-1")
@pytest.mark.parametrize(("version", "commits", "bump", "following"), [
    ("1.4.2", _log("docs: explain flags", "chore: bump deps"), "none", "1.4.2"),
    ("1.4.2", _log("fix: a", "Feat(api): b"), "minor", "1.5.0"),
    ("1.4.2", _log("feat:missing space", "feature: not a type", "fix(): empty", " fix: lead"), "none", "1.4.2"),
    ("1.4.2", _log("feat!(api): bang before scope", "perf: faster"), "patch", "1.4.3"),
    ("1.4.2", _log("chore(env)!: rename"), "major", "2.0.0"),
    ("1.4.2", _log("chore: rename\n\nBREAKING-CHANGE: env"), "major", "2.0.0"),
    ("1.4.2", _log("fix: a\n\nbreaking change: lower\n  BREAKING CHANGE: indented\nBREAKING CHANGE:no space"), "patch",
     "1.4.3"),
    ("1.4.2", _log("WIP\n\nBREAKING CHANGE: ignored with its commit"), "none", "1.4.2"),
    ("1.4.2", _log("fix: a\n\nBREAKING CHANGE: "), "major", "2.0.0"),
    ("1.4.2", "docs: x\n--- \nfeat: y\n", "none", "1.4.2"),
    ("1.4.2", _log("feat: x", "revert: feat: x"), "none", "1.4.2"),
    ("1.4.2", _log("feat: x", "feat: x", "revert: feat: x"), "minor", "1.5.0"),
    ("1.4.2", _log("revert: feat: never existed"), "patch", "1.4.3"),
    ("1.4.2", _log("feat: x", "revert: feat: x", "revert: revert: feat: x"), "minor", "1.5.0"),
    ("1.4.2", _log("feat: x", "revert: feat: x", "revert: revert: feat: x", "revert: revert: revert: feat: x"),
     "none", "1.4.2"),
    ("1.4.2", _log("feat: x", "revert: feat: x", "revert: revert: feat: x", "revert: feat: x",
                    "revert: revert: revert: feat: x"), "none", "1.4.2"),
    ("1.4.2", _log("revert: feat: ghost", "revert: revert: feat: ghost"), "none", "1.4.2"),
    ("1.4.2", _log("feat: x", "Revert: feat: x", "revert: Revert: feat: x"), "minor", "1.5.0"),
    ("1.4.2", _log("feat: x", "revert: feat: x", "revert: revert: feat: x "), "patch", "1.4.3"),
    ("1.4.2", _log("revert!: feat: gone"), "major", "2.0.0"),
    ("0.4.1", _log("feat!: new config"), "minor", "0.5.0"),
    ("0.4.1", _log("feat: y"), "patch", "0.4.2"),
    ("1.2.0-rc.3", _log("feat!: z"), "prerelease", "1.2.0-rc.4"),
    ("1.2.0-rc.3", _log("docs: z"), "none", "1.2.0-rc.3"),
])
def test_the_oracle_applies_each_rule_of_the_fixture_skill(version: str, commits: str, bump: str, following: str) -> None:
    assert next_version(version + "\n", commits) == {"bump": bump, "next": following}


@pytest.mark.ac("fromargs-benefit-smoke:AC-1")
def test_the_oracle_gives_the_answer_of_the_skill_example() -> None:
    guide = (FIXTURE / "SKILL.md.fixture").read_text(encoding="utf-8")
    example = guide.split("## Example", 1)[1].split("## Helper", 1)[0]
    version = re.search(r"`VERSION` holds `([^`]+)`", example)
    commits = re.search(r"```text\n(.*?)```", example, re.DOTALL)
    answer = re.search(r"`(\{\"bump\".*?\})`", example)
    assert version and commits and answer, example
    assert next_version(version[1], commits[1]) == json.loads(answer[1])


@pytest.mark.ac("fromargs-benefit-smoke:AC-1")
def test_the_oracle_refuses_a_version_that_is_not_semantic() -> None:
    with pytest.raises(ValueError, match="not a semantic version"):
        _ = next_version("v1.2", _log("fix: a"))


@pytest.mark.ac("fromargs-benefit-smoke:AC-2")
def test_the_reference_draft_passes_the_draft_check_with_twenty_four_cases_in_eight_families() -> None:
    draft = reference_draft()
    assert check_draft(draft) == []
    assert len(draft) == 24 and len({case["family"] for case in draft}) == 8
    assert len({json.dumps(case["files"]) for case in draft}) == 24, "a second family copy repeats a case"
    assert len({json.dumps(case["expected"]) for case in draft}) > 3, "the reference answers do not vary"


@pytest.mark.ac("fromargs-benefit-smoke:AC-2")
def test_the_draft_check_names_a_case_whose_expected_answer_differs_from_the_oracle() -> None:
    draft = reference_draft()
    case = draft[0]
    case["expected"] = cast(dict[str, object], case["expected"]) | {"bump": "major", "next": "9.9.9"}
    problems = check_draft(draft)
    assert len(problems) == 1 and str(case["id"]) in problems[0] and "the oracle says" in problems[0], problems


@pytest.mark.ac("fromargs-benefit-smoke:AC-2")
def test_the_scenario_contract_grades_the_task_kind_with_exact_json() -> None:
    contract = parse(CONTRACT, "test")
    assert contract.grader("task").type == "exact-json"
    assert not contract.judged("task")


@pytest.mark.ac("fromargs-benefit-smoke:AC-2")
@pytest.mark.parametrize(("change", "problem"), [
    ({"files": {"commits.txt": "fix: a\n"}}, "files lack VERSION or commits.txt"),
    ({"expected": {"bump": "patch", "next": "1.0.1", "criteria": []}}, "must hold exactly bump and next"),
    ({"expected": {"bump": "patch"}}, "must hold exactly bump and next"),
    ({"files": {"VERSION": "v1\n", "commits.txt": _log(*["fix: a"] * 80)}}, "not a semantic version"),
    ({"files": {"VERSION": "1.0.0\n", "commits.txt": _log(*["fix: a"] * 79)}}, "holds 79 commits; the scenario needs 80"),
])
def test_the_draft_check_refuses_a_malformed_task_case(change: dict[str, object], problem: str) -> None:
    draft = reference_draft()
    draft[0] |= change
    problems = check_draft(draft)
    assert len(problems) == 1 and problem in problems[0], problems


@pytest.mark.ac("fromargs-benefit-smoke:AC-2")
def test_the_draft_check_refuses_too_few_families_and_a_draft_that_is_not_a_list() -> None:
    draft = [case | {"family": "one" if index < 6 else "two"} for index, case in enumerate(reference_draft())]
    assert check_draft(draft) == ["the task cases span 2 families; the scenario needs 3"]
    assert check_draft({"cases": []}) == ["the draft is not a list of case objects"]


@pytest.mark.ac("fromargs-benefit-smoke:AC-3")
def test_invocations_reads_fenced_and_inline_commands_and_skips_shell_operators_and_placeholders() -> None:
    guide = ("Run `python3 .agents/skills/semver-next/scripts/next.pyz --version-file VERSION`.\n"
             "```sh\n$ python3 scripts/next.pyz commits.txt\npython3 scripts/next.pyz <file>\n```\n"
             "Or `python3 scripts/next.pyz | jq .` and `python3 scripts/other.pyz`.\n")
    assert invocations(guide, "next") == [["python3", "scripts/next.pyz", "commits.txt"],
                                          ["python3", ".agents/skills/semver-next/scripts/next.pyz", "--version-file",
                                           "VERSION"]]


def _archive(tmp_path: Path, body: str) -> Path:
    """Write a stand-in for the built CLI. Python runs a plain script that has a `.pyz` name."""
    archive = tmp_path / "build/next.pyz"
    archive.parent.mkdir(parents=True)
    _ = archive.write_text(f"import json, sys\nsys.path.insert(0, {str(HERE)!r})\n{body}", encoding="utf-8")
    return archive


ORACLE_CLI = ("from pathlib import Path\nfrom semver_oracle import next_version\n"
              "print(json.dumps(next_version(Path('VERSION').read_text(), Path(sys.argv[1]).read_text())))\n")
GUIDE = "Run `python3 .agents/skills/semver-next/scripts/next.pyz commits.txt` and return its JSON.\n"


@pytest.mark.ac("fromargs-benefit-smoke:AC-3")
def test_the_cli_check_passes_a_cli_that_answers_every_case_as_the_oracle(tmp_path: Path) -> None:
    fields = match_cli(GUIDE, _archive(tmp_path, ORACLE_CLI), "next", reference_draft(), tmp_path / "cases")
    assert fields["cli"] == "ok" and fields["cli_cases"] == 24 and fields["cli_mismatches"] == [], fields
    assert fields["cli_command"] == "python3 .agents/skills/semver-next/scripts/next.pyz commits.txt", fields


@pytest.mark.ac("fromargs-benefit-smoke:AC-3")
def test_the_cli_check_names_each_case_that_a_wrong_cli_answers(tmp_path: Path) -> None:
    wrong = "print(json.dumps({'bump': 'patch', 'next': '0.0.1'}))\n"
    fields = match_cli(GUIDE, _archive(tmp_path, wrong), "next", reference_draft(), tmp_path / "cases")
    assert fields["cli"] == "mismatch" and len(cast(list[str], fields["cli_mismatches"])) == 10, fields


@pytest.mark.ac("fromargs-benefit-smoke:AC-3")
def test_the_cli_check_reports_no_invocation_when_no_guide_command_answers(tmp_path: Path) -> None:
    placeholder = "Run `python3 scripts/next.pyz <file>`.\n"
    good, failing = _archive(tmp_path / "good", ORACLE_CLI), _archive(tmp_path / "failing", "sys.exit(2)\n")
    assert match_cli(placeholder, good, "next", reference_draft(), tmp_path / "a")["cli"] == "no-invocation"
    assert match_cli(GUIDE, failing, "next", reference_draft(), tmp_path / "b")["cli"] == "no-invocation"


def _complete(tmp_path: Path, **fields: object) -> dict[str, object]:
    patch = tmp_path / "candidate.patch"
    _ = patch.write_text("--- a\n+++ b\n", encoding="utf-8")
    return {"stage": "complete", "phase": "complete", "code": None, "expect": None, "patch": str(patch),
            "holdout_scored": 6, "verdict": "inconclusive"} | fields


@pytest.mark.ac("fromargs-benefit-smoke:AC-4")
def test_the_scenario_passes_only_with_a_checked_cli_whatever_the_gate_verdict(tmp_path: Path) -> None:
    passed, reason = cli_verdict(_complete(tmp_path, cli="ok", cli_target="semver-calc", cli_cases=12))
    assert passed and "semver-calc" in reason and "inconclusive" in reason, reason
    for cli in ("missing", "mismatch", "no-invocation", "build-failed", None):
        passed, reason = cli_verdict(_complete(tmp_path, cli=cli))
        assert not passed and f"cli={cli}" in reason, reason


@pytest.mark.ac("fromargs-benefit-smoke:AC-4")
@pytest.mark.parametrize(("stage", "code"), [("author", "draft-wrong"), ("author", "draft-missing"),
                                             ("run", "cases-too-easy"), ("cases", "cases-invalid")])
def test_a_coded_stop_fails_the_scenario_and_names_the_stage_and_the_code(stage: str, code: str) -> None:
    passed, reason = cli_verdict({"stage": stage, "code": code})
    assert not passed and f"stage {stage}" in reason and code in reason, reason


@pytest.mark.ac("fromargs-benefit-smoke:AC-4")
def test_a_completed_run_without_the_exported_patch_fails() -> None:
    passed, reason = cli_verdict({"stage": "complete", "phase": "complete", "patch": "/absent/candidate.patch",
                                  "cli": "ok"})
    assert not passed and "candidate.patch" in reason, reason


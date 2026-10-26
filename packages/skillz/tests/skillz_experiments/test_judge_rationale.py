"""gh-301: the judge rationale record, the grader-aware scoring policy label, export `zero_cases`, and holdout isolation."""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from skillz_experiments._audit import CRITERIA_SCORING_POLICY, SCORING_POLICY, identity
from skillz_experiments._contract import parse
from skillz_experiments._records import read, write
from skillz_experiments.run import export
from skillz_experiments.run._open import judge_kind
from test_validation_repeats_press import FakeState, _candidate, _phase

if TYPE_CHECKING:
    from .conftest import JudgedRun

CHILD = _candidate("child")
CRITERIA = {"criteria": [{"id": "answer", "text": "THE answer SHALL name the case"}]}


def _mode(judged: JudgedRun, mode: str) -> None:
    _ = judged.settings.write_text(json.dumps({"mode": mode, "log": str(judged.log)}))


def _judged_outcomes(out: Path) -> list[dict[str, object]]:
    outcomes = cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
    return [item for item in outcomes if isinstance(item.get("criteria"), dict)]


def _proposer_prompts(log: Path) -> list[str]:
    """Return the prompt of each reflection call. A reflection reply has no judge or task field."""
    prompts: list[str] = []
    for line in log.read_text().splitlines():
        item = cast(dict[str, object], json.loads(line))
        if item["operation"] != "infer":
            continue
        required = set(cast(list[str], cast(dict[str, object], item["response_schema"])["required"]))
        if not {"criteria", "result_json", "matches"} & required:
            prompts.append(str(item["prompt"]))
    return prompts


def _contract(kinds: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
            "kinds": kinds}


@pytest.mark.ac("gh-301:AC-1")
@pytest.mark.parametrize(("mode", "level", "met"), [("normal", 4, True), ("low-score", 1, False)])
def test_each_judged_outcome_stores_a_reason_and_a_met_verdict_per_criterion(
        judged: JudgedRun, mode: str, level: int, met: bool) -> None:
    _mode(judged, mode)
    assert judged.start()["phase"] == "complete"
    rows = _judged_outcomes(judged.out)
    assert rows
    for item in rows:
        assert isinstance(item["score"], (int, float))
        assert item["criteria"] == {"answer": {"level": level, "reason": "fake judge", "met": met}}


@pytest.mark.ac("gh-301:AC-2")
def test_the_scoring_policy_label_differs_between_an_audit_and_a_rubric_contract() -> None:
    audit = parse(_contract({"review": {"grader": "audit"}}), "skill")
    rubric = parse(_contract({"task": {"grader": "judge", "rubric": "Grade the answer."}}), "skill")
    mixed = parse(_contract({"echo": {"grader": "exact-json"},
                             "task": {"grader": "hybrid", "argv": ["python3", "check.py"], "rubric": "Grade it."}}), "skill")
    assert (judge_kind(audit), judge_kind(rubric), judge_kind(mixed)) == ("audit", "criteria", "criteria")
    audit_label = identity("judge-model", judge_kind(audit))["scoring_policy"]
    rubric_label = identity("judge-model", judge_kind(rubric))["scoring_policy"]
    assert audit_label == SCORING_POLICY
    assert rubric_label == CRITERIA_SCORING_POLICY
    assert audit_label != rubric_label


@pytest.mark.ac("gh-301:AC-2")
def test_a_rubric_run_freezes_the_criteria_label_in_run_json(judged: JudgedRun) -> None:
    assert judged.start()["phase"] == "complete"
    frozen = cast(dict[str, str], read(judged.out / "run.json")["judge"])
    assert frozen["scoring_policy"] == CRITERIA_SCORING_POLICY


@pytest.mark.ac("gh-301:AC-3")
def test_export_lists_each_case_that_scores_zero_on_every_repeat_with_redacted_reasons(
        judged: JudgedRun, tmp_path: Path) -> None:
    _mode(judged, "case-reason")
    assert judged.start()["phase"] == "complete"
    record = read(judged.out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    rows = [item for item in outcomes if isinstance(item.get("criteria"), dict)]
    assert rows and all(item["score"] == 0 for item in rows)
    secret, passing = rows[0], next(item for item in rows if item["case_id"] != rows[0]["case_id"])
    secret_case, passing_case = str(secret["case_id"]), str(passing["case_id"])
    cast(dict[str, dict[str, object]], secret["criteria"])["answer"]["reason"] = "password=hunter2hunter2"
    passing_arm = passing["arm"]
    passing["score"] = 0.5
    write(judged.out / "run.json", record)
    _ = export(judged.out, tmp_path / "export")
    report = read(tmp_path / "export" / "report.json")
    zero = cast(list[dict[str, object]], report["zero_cases"])
    listed = {(str(entry["case_id"]), str(entry["arm"])) for entry in zero}
    assert (passing_case, str(passing_arm)) not in listed
    assert (secret_case, str(secret["arm"])) in listed
    assert zero == sorted(zero, key=lambda entry: (str(entry["case_id"]), str(entry["arm"]), str(entry["candidate"])))
    text = json.dumps(zero)
    assert "hunter2hunter2" not in text and "[redacted]" in text
    for entry in zero:
        verdicts = cast(dict[str, list[dict[str, object]]], entry["criteria"])["answer"]
        assert len(verdicts) == entry["repeats"]
        for verdict in verdicts:
            assert verdict["level"] == 1 and verdict["met"] is False
            assert verdict["reason"] in {f"rationale<{entry['case_id']}>", "[redacted]"}


@pytest.mark.ac("gh-301:AC-3")
def test_export_lists_no_zero_case_when_every_case_scores(judged: JudgedRun, tmp_path: Path) -> None:
    assert judged.start()["phase"] == "complete"
    _ = export(judged.out, tmp_path / "export")
    assert read(tmp_path / "export" / "report.json")["zero_cases"] == []


@pytest.mark.ac("gh-301:AC-4")
def test_a_holdout_rationale_never_reaches_a_proposer_prompt(judged: JudgedRun) -> None:
    _mode(judged, "case-reason")
    assert judged.start()["phase"] == "complete"
    rows = _judged_outcomes(judged.out)
    holdout = {str(item["case_id"]) for item in rows if item.get("split") == "holdout"}
    search = {str(item["case_id"]) for item in rows if item.get("split") != "holdout"}
    assert holdout and search
    for item in rows:
        reason = cast(dict[str, dict[str, object]], item["criteria"])["answer"]["reason"]
        assert reason == f"rationale<{item['case_id']}>"
    prompts = _proposer_prompts(judged.log)
    assert prompts
    # The search reasons reach the proposer, so the absence of the holdout reasons is not vacuous.
    assert any(f"rationale<{case}>" in prompt for case in search for prompt in prompts)
    for case in holdout:
        assert all(f"rationale<{case}>" not in prompt for prompt in prompts)



def _judged_row(split: str, arm: str, reason: str) -> dict[str, object]:
    """Return one judged outcome of the child on `t0` with an unmet `answer` criterion that gives `reason`."""
    return {"arm": arm, "split": split, "case_id": "t0", "search_candidate": CHILD.identity, "score": 0.0,
            "criteria": {"answer": {"level": 1, "reason": reason, "met": False}}}


@pytest.mark.ac("gh-301:AC-4")
def test_a_recorded_holdout_rationale_stays_out_of_the_search_feedback() -> None:
    # The holdout rationales come first, for the same candidate and case id, so a reuse that ignores the split takes them.
    outcomes = [_judged_row("holdout", "search", "rationale<holdout-search>"),
                _judged_row("holdout", "baseline", "rationale<holdout-baseline>"),
                _judged_row("train", "search", "rationale<train>")]
    state = FakeState(outcomes, {}, cases=[])
    phase = _phase(state, CHILD)
    vars(phase)["_search_cases"]["t0"] = SimpleNamespace(identifier="t0", family="f0", split="train", request="t0",
                                                         expected=CRITERIA)
    _ = phase.recorded_for_reuse()
    _, feedback = phase.evaluate_example({"SKILL.md": "child"}, "t0")
    assert state.live == [], "the train outcome comes from the record"
    text = json.dumps(feedback)
    assert "rationale<train>" in text
    assert "holdout" not in text
    with pytest.raises(ValueError, match="holdout must not enter optimization"):
        _ = phase.evaluate_example({"SKILL.md": "child"}, "h0")
"""Exercise baseline measurement at the session and run-record boundaries."""
from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast, final

import pytest

from _reproduction_runs import Approver, measure, seed_zero, start
from fake_provider import FakeRunProvider, search_approval
from skillz_experiments._budget import Budget
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError, Split, mapping
from skillz_experiments._intake import DRAFT_NAME
from skillz_experiments._records import SCHEMA_VERSION, read, write
from skillz_experiments.evaluation import CheckMemo
from skillz_experiments.gate import DEFAULT_STATISTICS
from skillz_experiments.run import Stop, export, run
from skillz_experiments.run._baseline_pass import measure_baseline
from skillz_experiments.run._holdout import arm_scores
from skillz_experiments.run._record_fields import record_outcomes
from skillz_experiments.run._resume import refuse_too_easy
from skillz_experiments.run._session import Session
from test_run_search import MODEL

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]


@pytest.mark.usefixtures("host_login")
def test_search_cannot_rebuild_missing_baseline_headroom(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = start(tmp_path, make_target, write_draft, approvals)
    seed_zero(state)
    assert measure(state, target, out, approved, calls)["search"] == "not-run"
    with pytest.raises(Stop) as stopped:
        _ = run(target, out, MODEL, search=True, approve_budget=search_approval(target, out, MODEL))
    assert stopped.value.code == "live-required"
    path = out / "run.json"
    record = read(path)
    del record["headroom"]
    write(path, record)
    before = state.holdout_calls
    with pytest.raises(ValueError, match="run record lacks headroom"):
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert state.holdout_calls == before
    assert read(path)["outcomes"] == record["outcomes"]


def test_export_refers_a_baseline_run_to_its_local_report(tmp_path: Path) -> None:
    out = tmp_path / "run"
    out.mkdir()
    write(out / "run.json", {"schema_version": SCHEMA_VERSION, "phase": "prepared",
                            "plan_mode": "baseline", "exposed": ["case"], "headroom": {}})
    write(out / "report.json", {"exposed": ["case"]})
    destination = tmp_path / "export"
    with pytest.raises(ValueError, match=r"export requires a completed run; see .*report\.json"):
        _ = export(out, destination)
    assert not destination.exists()


@final
class MeasurementProvider(FakeRunProvider):
    def __init__(self, budget: Budget, attempts: Counter[str], failure: str | None,
                 zero_family: str, train_score: float) -> None:
        super().__init__()
        self.budget = budget
        self.attempts = attempts
        self.failure = failure
        self.zero_family = zero_family
        self.train_score = train_score
        self.lock = threading.Lock()

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None, *,
               holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case, holdout, schema
        raise AssertionError("baseline must not reflect")

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: CheckMemo) -> dict[str, object]:
        del checks
        assert holdout == (case.split == "holdout")
        with self.lock:
            self.attempts[case.identifier] += 1
            attempt = self.attempts[case.identifier]
        if self.failure == case.identifier and attempt == 1:
            raise RuntimeError("scripted provider failure")
        role = "judge" if case.regression else "task"
        with self.budget.timed(role):
            self.budget.claim(holdout=holdout, role=role)
        score = self.train_score if case.split == "train" else (
            (0.25 if attempt == 1 else 0.75) if case.split == "validation" else float(case.family != self.zero_family))
        level = 4 if score == 1 else 2 if score == 0.75 else 1
        return {"score": score, "candidate_hash": candidate.identity,
                "criteria": {case.identifier: {"level": level, "reason": "Missing redo. sk-sentinel"}},
                "task_usage": {"input_tokens": 10}, "judge_usage": {"output_tokens": 3}}


@pytest.mark.usefixtures("host_login")
@pytest.mark.parametrize(("saving", "train_score", "verdict"), [(None, 1.0, "too-easy"), (0.1, 1.0, "ok"),
                                                               (None, 0.5, "ok")])
def test_measurement_resumes_only_missing_slots_and_reports_all_splits(
        tmp_path: Path, make_target: Maker, write_draft: Drafter,
        saving: float | None, train_score: float, verdict: str) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    ids = sorted(write_draft(out))
    draft = out / DRAFT_NAME
    cases = cast(list[dict[str, object]], json.loads(draft.read_text(encoding="utf-8")))
    for case in cases:
        if str(case["id"]).endswith("-0"):
            case.update(regression=True, source="session")
    cases.append({"id": "pending", "family": "f0", "kind": "trigger", "source": "skill", "request": "Check activation."})
    write(draft, cases)
    flags = None if saving is None else {"min_token_saving": saving, "max_quality_loss": 0.05}
    with pytest.raises(Stop) as question:
        _ = run(target, out, MODEL, statistics=flags)
    assert question.value.code == "cases-unapproved"
    case_hash = cast(str, question.value.data["case_hash"])
    estimate = question.value.data.get("estimate")
    if estimate is None:
        with pytest.raises(Stop) as budget:
            _ = run(target, out, MODEL, approve_cases=case_hash, statistics=flags)
        assert budget.value.code == "budget-unapproved"
        estimate = budget.value.data["estimate"]
    calls = cast(int, mapping(estimate)["calls"])
    with pytest.raises(Stop) as prepared:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls, statistics=flags)
    assert prepared.value.code == "live-required"

    attempts: Counter[str] = Counter()
    failure: str | None = None
    zero_family = ""

    def factory(_model: str, budget: Budget, _checkpoint: Callable[[], None]) -> MeasurementProvider:
        return MeasurementProvider(budget, attempts, failure, zero_family, train_score)

    stats = replace(DEFAULT_STATISTICS, min_token_saving=saving, max_quality_loss=0.05 if saving is not None else None)
    session = Session(out, MODEL, "claude", factory, None, statistics=stats)
    holdout = session.cases_for("holdout")
    regression = next(case for case in holdout if case.regression)
    failure, zero_family = regression.identifier, regression.family
    session.provider.close()
    session = Session(out, MODEL, "claude", factory, None, statistics=stats)
    session.record["plan_mode"] = "baseline"
    with pytest.raises(CodedError, match="1 seed evaluations have no score") as stopped:
        _ = measure_baseline(session)
    assert stopped.value.code == "baseline-faulted"
    session.provider.close()
    interrupted = read(out / "run.json")
    assert "headroom" not in interrupted and not (out / "report.json").exists()
    assert attempts == Counter({name: 2 for name in ids})
    failures = [row for row in record_outcomes(interrupted) if row.get("status") == "evaluation-failed"]
    assert [(row["case_id"], row["arm"]) for row in failures] == [(failure, "baseline")]

    session = Session(out, MODEL, "claude", factory, None, statistics=stats)
    report = measure_baseline(session)
    session.provider.close()
    assert attempts == Counter({name: 2 + (name == failure) for name in ids})
    record = read(out / "run.json")
    assert read(out / "report.json") == report
    assert record["exposed"] == report["exposed"] == ids
    rows = [row for row in record_outcomes(record) if row.get("status") != "evaluation-failed"]
    slots_by_split: dict[Split, tuple[tuple[str, int | None], ...]] = {
        "train": (("search", None), ("baseline", 1)),
        "validation": (("search", 0), ("search", 1)),
        "holdout": (("baseline", 0), ("baseline", 1)),
    }
    assert Counter((row["case_id"], row["arm"], row.get("repeat"), "search_candidate" in row) for row in rows) == Counter(
        (case.identifier, arm, repeat, arm == "search")
        for split, slots in slots_by_split.items() for case in session.cases_for(split) for arm, repeat in slots)
    assert arm_scores(rows, "baseline") == {case.identifier: [float(case.family != zero_family)] * 2 for case in holdout}

    block = mapping(record["headroom"])
    regressions = mapping(block["reproduction"])
    assert set(regressions) == {case.identifier for split in slots_by_split
                                for case in session.cases_for(split) if case.regression}
    reproduced = mapping(regressions[failure])
    assert reproduced == {"split": "holdout", "solvable": "unknown", "scores": [0, 0], "reproduces": True,
                          "state": "fail-to-pass", "unmet": {failure: {"level": 1}}}
    assert block["verdict"] == verdict
    if verdict == "too-easy":
        with pytest.raises(Stop, match="the seed already scores") as stopped_search:
            refuse_too_easy(record, out)
        assert stopped_search.value.data["baseline"] == report
    else:
        refuse_too_easy(record, out)

    reports = mapping(report["cases"])
    assert sorted(reports) == ids
    assert reports[failure] == {"split": "holdout", "family": zero_family, "scores": [0, 0],
                                "unmet": [{failure: {"level": 1, "reason": "Missing redo. [redacted]"}}] * 2}
    for case in session.cases_for("validation"):
        detail = mapping(reports[case.identifier])
        assert detail["split"] == "validation"
        assert sorted(cast(list[float], detail["scores"])) == [0.25, 0.75]
    first_passing = next(case for case in holdout if case.family != zero_family)
    assert reports[first_passing.identifier] == {"split": "holdout", "family": first_passing.family, "scores": [1, 1],
                                                  "unmet": [{}, {}]}
    train = session.cases_for("train")
    validation = session.cases_for("validation")
    assert report["family_means"] == {"train": {case.family: train_score for case in train},
                                      "validation": {case.family: 0.5 for case in validation},
                                      "holdout": {case.family: float(case.family != zero_family) for case in holdout}}
    assert report["saturated_families"] == {"train": sorted({case.family for case in train}) if train_score == 1 else [],
                                           "validation": []}
    assert report["criteria_flags"] == [{"criterion": case.identifier, "reason": "partly-met",
                                          "case_id": case.identifier, "level_2": 1, "outcomes": 2} for case in validation]
    assert report["zero_cases"] == [{"case_id": case.identifier, "arm": "baseline", "candidate": session.seed.identity,
                                     "repeats": 2, "criteria": {case.identifier: [
                                         {"level": 1, "met": False, "reason": "Missing redo. [redacted]"}] * 2}}
                                    for case in holdout if case.family == zero_family]
    assert report["calls_by_role"] == {"task": len(ids), "judge": len(ids)}
    assert report["seconds_by_role"] == record["seconds_by_role"]
    assert set(mapping(report["seconds_by_role"])) == {"task", "judge"}
    usage = mapping(report["usage_by_role"])
    assert mapping(usage["task"])["input_tokens"] == 20 * len(ids)
    assert mapping(usage["judge"])["output_tokens"] == 6 * len(ids)


@pytest.mark.ac("gh-756:AC-6")
@pytest.mark.usefixtures("host_login")
def test_the_report_applies_the_search_too_easy_rule_to_train_families(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = start(
        tmp_path, make_target, write_draft, approvals, regression=lambda case: str(case["id"]).endswith("-0"))

    def score(text: str, split: str, case_id: str) -> float | None:
        """Score the seed 1.0 on each train floor case, 0.85 on each other train case, and 0 elsewhere."""
        if "improved" in text:
            return None
        return (1.0 if case_id.endswith("-0") else 0.85) if split == "train" else 0.0

    state.case_score_of = score
    report = measure(state, target, out, approved, calls)
    headroom = mapping(read(out / "run.json")["headroom"])
    train = {name: mapping(entry) for name, entry in mapping(headroom["families"]).items()
             if mapping(entry)["split"] == "train"}
    assert train and all(entry["cases"] == 1 for entry in train.values())
    assert headroom["verdict"] == "ok"
    assert report["family_means"] == {
        "train": {name: pytest.approx(0.85) for name in train},
        "validation": {name: 0.0 for name in mapping(mapping(report["family_means"])["validation"])},
        "holdout": mapping(report["family_means"])["holdout"]}
    assert mapping(report["saturated_families"])["train"] == []


@pytest.mark.ac("gh-756:AC-4")
@pytest.mark.usefixtures("host_login")
def test_the_baseline_warns_on_stderr_and_records_the_seed_warnings_and_unscorable_cases_in_the_report(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        capsys: pytest.CaptureFixture[str]) -> None:
    target, out, state, approved, calls = start(tmp_path, make_target, write_draft, approvals, regression=lambda _case: False)
    seed_zero(state)
    report = measure(state, target, out, approved, calls)
    captured = capsys.readouterr()
    headroom = mapping(read(out / "run.json")["headroom"])
    warnings = cast(list[str], headroom["warnings"])
    assert any(text.startswith("suspect-zero:") for text in warnings)
    assert report["warnings"] == warnings
    assert report["unscorable"] == headroom["unscorable"] == []
    printed = [line for line in captured.err.splitlines() if line.startswith("skillz-experiment: warning: ")]
    assert printed == [f"skillz-experiment: warning: {text}" for text in warnings]
    assert captured.out == ""
    assert read(out / "report.json") == report


@pytest.mark.ac("gh-756:AC-4")
@pytest.mark.usefixtures("host_login")
def test_the_report_names_each_case_that_a_setup_fault_zeroes_on_every_repeat(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = start(tmp_path, make_target, write_draft, approvals, regression=lambda _case: False)
    seed_zero(state)
    state.extra = {"gate_fault": "timeout"}
    report = measure(state, target, out, approved, calls)
    unscorable = cast(list[dict[str, object]], report["unscorable"])
    frozen = [case for case in cast(list[dict[str, object]], read(out / "cases.json")["cases"])
              if case["split"] in ("train", "validation")]
    assert unscorable and sorted(str(item["case_id"]) for item in unscorable) == sorted(str(case["id"]) for case in frozen)
    assert all(str(item["cause"]).startswith("setup fault timeout") for item in unscorable)
    assert unscorable == mapping(read(out / "run.json")["headroom"])["unscorable"]
    assert read(out / "report.json") == report

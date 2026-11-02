"""A diagnosis gets a useful answer without a completed search: the baseline report and the fixture check."""
from __future__ import annotations

import json
import subprocess
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest

from skillz_experiments import _cli
from skillz_experiments._cases import CodedError
from skillz_experiments._intake import DRAFT_NAME, DraftCase
from skillz_experiments._records import read
from skillz_experiments._seed import check_fixture_paths
from skillz_experiments._validate import validate
from skillz_experiments.run import Stop, _session, export, run
from skillz_experiments.run._plan import VALIDATION_REPEATS

from test_run_search import MODEL, Search, _start  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]
Json = dict[str, Any]  # pyright: ignore[reportExplicitAny]

SECRET = "abcdef1234567890XYZ"
UNMET = {"answer": {"level": 2, "met": False, "reason": f"no status check before the handoff; api_key={SECRET}"}}


def _no_model_process(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Fail any subprocess start, so a test proves that no harness or model process runs."""
    started: list[object] = []

    def refuse(*args: object, **kwargs: object) -> object:
        started.append(args)
        raise AssertionError(f"a subprocess started: {args!r} {kwargs!r}")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    return started


def _stopped(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
             monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Search, int, Stop, list[int]]:
    """Run to the `cases-too-easy` stop: train saturates at 1.0, and validation fails at 0.5 with an unmet criterion."""
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, split: (1.0 if split == "train" else 0.5) if "improved" not in text else None
    state.extra = {"criteria": UNMET}
    grants: list[int] = []
    real = _session.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        grants.append(cast(int, kwargs["metric_calls"]))
        return real(*args, **kwargs)  # pyright: ignore[reportAny]

    monkeypatch.setattr(_session, "pareto_search", spy)
    with pytest.raises(Stop) as stopped:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    return target, out, state, calls, stopped.value, grants


@pytest.mark.ac("autoimprove-useful-answer:AC-1")
@pytest.mark.ac("autoimprove-useful-answer:AC-2")
def test_a_too_easy_stop_reports_the_failing_baseline_criteria_spent_calls_and_what_never_ran(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    _, out, state, approved, stop, grants = _stopped(tmp_path, make_target, write_draft, approvals, monkeypatch)
    assert stop.code == "cases-too-easy" and "(train) mean 1.00" in str(stop) and "new run directory" in str(stop)
    baseline = cast(Json, stop.data["baseline"])
    record = read(out / "run.json")
    cases = cast(list[Json], read(out / "cases.json")["cases"])
    validation = [case for case in cases if case["split"] == "validation"]
    # Nothing past the headroom pass ran, and the report says so instead of implying a search or an export.
    assert grants == [] and state.proposals == 0 and state.invokes == 0 and state.holdout_calls == 0
    assert {key: baseline[key] for key in ("search", "holdout_gate", "replay", "export")} == {
        "search": "not-run", "holdout_gate": "not-run", "replay": "not-run", "export": "unavailable"}
    assert record["phase"] == "prepared" and "export" in str(stop.data["next"]) and "no improvement" in str(stop.data["next"])
    # The failing validation family shows beside the saturated train families, with its redacted unmet criterion.
    failing = cast(list[Json], baseline["failing"])
    assert Counter(item["case_id"] for item in failing) == Counter(
        {case["id"]: VALIDATION_REPEATS for case in validation})
    assert all(item["split"] == "validation" and item["score"] == 0.5 for item in failing)
    assert {item["family"] for item in failing} == {case["family"] for case in validation}
    reasons = [cast(str, item["criteria"]["answer"]["reason"]) for item in failing]
    assert all("no status check" in reason and SECRET not in reason for reason in reasons)
    assert cast(Json, baseline["headroom"])["verdict"] == "too-easy"
    # Spent counts come from the record; the approved ceiling and the estimate stay separate from them.
    spent = cast(Json, baseline["spent"])
    assert spent["calls"] == record["calls"] and 0 < spent["calls"] < approved
    assert spent["calls_by_role"] == record["calls_by_role"] and spent["retries"] == 0
    assert baseline["approved_calls"] == approved
    assert baseline["estimate"] == {key: cast(Json, record["estimate"])[key] for key in ("calls", "seconds")}
    assert baseline["scored"] == {"outcomes": len(cast(list[object], record["outcomes"])), "failed": 0,
                                  "cases": sum(case["split"] != "holdout" for case in cases)}
    assert baseline["unscored"] == {"holdout_cases": sum(case["split"] == "holdout" for case in cases),
                                    "pending_cases": 0}


@pytest.mark.ac("autoimprove-useful-answer:AC-3")
def test_the_cli_resume_prints_the_same_baseline_and_export_still_refuses_with_no_model_process(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    target, out, _, _, stop, _ = _stopped(tmp_path, make_target, write_draft, approvals, monkeypatch)
    _ = capsys.readouterr()
    started = _no_model_process(monkeypatch)
    code = _cli.main(["run", "--target", str(target), "--out", str(out), "--model", MODEL, "--live"])
    printed = capsys.readouterr()
    assert code == 1 and started == []
    assert json.loads(printed.out) == json.loads(json.dumps(stop.data))
    assert json.loads(printed.err)["code"] == "cases-too-easy"
    destination = tmp_path / "export"
    assert _cli.main(["export", str(out), "--out", str(destination)]) == 1
    assert "export requires a completed run" in capsys.readouterr().err and not destination.exists()
    with pytest.raises(ValueError, match="export requires a completed run"):
        _ = export(out, destination)


@pytest.mark.ac("autoimprove-useful-answer:AC-2")
def test_a_resume_with_an_unreadable_case_file_reports_unknown_coverage_instead_of_a_guess(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, _, stop, _ = _stopped(tmp_path, make_target, write_draft, approvals, monkeypatch)
    _ = (out / "cases.json").write_text("not json", encoding="utf-8")
    with pytest.raises(Stop) as again:
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    baseline, first = cast(Json, again.value.data["baseline"]), cast(Json, stop.data["baseline"])
    assert again.value.code == "cases-too-easy"
    assert baseline["unscored"] == {"holdout_cases": None, "pending_cases": None}
    assert [item["family"] for item in cast(list[Json], baseline["failing"])] == [None] * len(cast(list[Json], first["failing"]))
    assert baseline["spent"] == first["spent"] and baseline["search"] == "not-run"


@pytest.mark.ac("autoimprove-useful-answer:AC-3")
def test_a_completed_run_still_exports_its_report(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert result["phase"] == "complete" and "baseline" not in result
    exported = export(out, tmp_path / "export")
    assert exported["sharing"] == "private-local-only" and (tmp_path / "export" / "report.json").is_file()


def _fixture_draft(out: Path, request_of: Callable[[str], str]) -> None:
    """Write the default draft with one case-local fixture under `evals/files/` and the request from `request_of`."""
    cases = cast(list[Json], json.loads((out / DRAFT_NAME).read_text(encoding="utf-8")))
    for case in cases:
        case["files"] = {f"evals/files/{case['id']}.json": "{\"observed\": true}\n"}
        case["request"] = request_of(cast(str, case["id"]))
    _ = (out / DRAFT_NAME).write_text(json.dumps(cases), encoding="utf-8")


@pytest.mark.ac("autoimprove-useful-answer:AC-4")
def test_a_request_that_names_an_unstaged_fixture_stops_before_the_case_question_and_any_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, monkeypatch: pytest.MonkeyPatch) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    _fixture_draft(out, lambda name: f"Read evals/files/{name if name != 't1-0' else 'missing'}.json, then answer.")
    started = _no_model_process(monkeypatch)
    built: list[tuple[object, ...]] = []

    def factory(*args: object) -> NoReturn:
        built.append(args)
        raise AssertionError("a session started")

    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, factory=factory)
    assert caught.value.code == "case-fixture-missing" and not isinstance(caught.value, Stop)
    assert "t1-0: evals/files/missing.json" in str(caught.value) and "no model call ran" in str(caught.value)
    assert built == [] and started == [] and not (out / "cases.json").exists() and not (out / "run.json").exists()
    report = validate(target, out / DRAFT_NAME)
    assert not report["ok"]
    assert [found["code"] for found in cast(list[Json], report["findings"])] == ["case-fixture-missing"]


@pytest.mark.ac("autoimprove-useful-answer:AC-5")
def test_a_request_needs_no_name_for_an_auxiliary_file_and_may_name_an_output_path(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    # Odd cases name nothing; even cases name their fixture and an output file outside the fixture directory.
    _fixture_draft(out, lambda name: "Answer from the workspace." if name.endswith("1") else
                   f"Read evals/files/{name}.json and write notes/answer.md; see https://example.com/a/b.html.")
    with pytest.raises(Stop) as asked:
        _ = run(target, out, MODEL, live=True)
    assert asked.value.code == "cases-unapproved"
    assert validate(target, out / DRAFT_NAME)["ok"]


@pytest.mark.ac("autoimprove-useful-answer:AC-4")
@pytest.mark.ac("autoimprove-useful-answer:AC-5")
@pytest.mark.parametrize(("request_text", "missing"), [
    ("Read evals/files/b.json.", ["evals/files/b.json"]),
    ("Use `evals/files/a.json` and (evals/files/c.md).", ["evals/files/c.md"]),
    ("Open /repo/evals/files/b.json or ~/evals/files/b.json or ./evals/files/b.json.", []),
    ("Write evals/out/b.json and src/app.py.", []),
    ("Read evals/files/a.json twice: evals/files/a.json.", []),
])
def test_the_fixture_check_matches_only_relative_paths_in_a_staged_directory(request_text: str, missing: list[str]) -> None:
    cases = [DraftCase("one", "f", "task", request_text, "skill", {"evals/files/a.json": "{}"}, {}),
             DraftCase("two", "g", "task", "no paths", "skill", {"fixture.md": "x"}, {})]
    if not missing:
        check_fixture_paths(cases)
        return
    with pytest.raises(CodedError) as caught:
        check_fixture_paths(cases)
    assert caught.value.code == "case-fixture-missing"
    assert [line.removeprefix("- one: ") for line in str(caught.value).splitlines() if line.startswith("- ")] == missing

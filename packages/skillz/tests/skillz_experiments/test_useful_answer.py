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
from skillz_experiments._cases import digest
from skillz_experiments._records import read
from skillz_experiments._validate import validate
from skillz_experiments._seed import fixture_misses
from skillz_experiments.run import Stop, _session, export, run
from skillz_experiments.run._baseline import baseline_report
from skillz_experiments.run._outcomes import FIELD_LIMIT
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

    def no_session(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError(f"a session started: {args!r} {kwargs!r}")

    monkeypatch.setattr("skillz_experiments.run._run.Session", no_session)
    code = _cli.main(["run", "--target", str(target), "--out", str(out), "--model", MODEL, "--live"])
    printed = capsys.readouterr()
    assert code == 1 and started == []
    assert json.loads(printed.out) == json.loads(json.dumps(stop.data))
    assert json.loads(printed.err)["code"] == "cases-too-easy" and json.loads(printed.err)["error"] == str(stop)
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


@pytest.mark.ac("autoimprove-useful-answer:AC-2")
def test_the_baseline_keeps_one_seed_outcome_per_run_and_lists_only_unmet_criteria(tmp_path: Path) -> None:
    """A resume replaces a stale failure; a met criterion and a non-seed arm stay out; a long reason is cut."""
    document: dict[str, object] = {"schema_version": 1, "seed": 1, "approved_hash": "h", "fixtures": {},
                "cases": [{"id": "v1", "family": "fam", "split": "validation"}, {"id": "h1", "split": "holdout"}],
                "pending": [{"id": "p1"}]}
    _ = (tmp_path / "cases.json").write_text(json.dumps(document), encoding="utf-8")
    failed = {"arm": "search", "score": 0.0, "status": "evaluation-failed"}
    verdicts = {"met": {"level": 4, "met": True, "reason": "fine"}, "miss": {"level": 2, "reason": "x" * 400}}
    record: dict[str, object] = {
        "dataset_hash": digest(document), "calls": 7, "retries": 1, "calls_by_role": {"task": 4, "judge": 3},
        "seconds_by_role": {"task": 12.5, "judge": 3.25}, "budget": {"maximum": 99}, "estimate": {"calls": 99, "seconds": 900},
        "headroom": {"ceiling": 0.9, "families": {}, "verdict": "too-easy"},
        "outcomes": [failed | {"split": "validation", "case_id": "v1", "repeat": 0},
                     failed | {"split": "validation", "case_id": "v1", "repeat": 1},
                     {"arm": "search", "split": "train", "case_id": "t1", "score": 0.5, "criteria": verdicts},
                     {"arm": "search", "split": "validation", "case_id": "v1", "repeat": 0, "score": 1.0},
                     {"arm": "baseline", "split": "validation", "case_id": "v1", "repeat": 0, "score": 0.0}]}
    baseline = baseline_report(record, tmp_path)
    failing = cast(list[Json], baseline["failing"])
    assert [(item["case_id"], item["repeat"], item["score"], item["status"], item["family"]) for item in failing] == [
        ("v1", 1, None, "evaluation-failed", "fam"), ("t1", None, 0.5, None, None)]
    assert failing[0]["criteria"] == {} and list(cast(Json, failing[1]["criteria"])) == ["miss"]
    reason = cast(str, failing[1]["criteria"]["miss"]["reason"])
    assert failing[1]["criteria"]["miss"]["level"] == 2 and len(reason) == FIELD_LIMIT and reason.endswith("[truncated]")
    assert baseline["scored"] == {"outcomes": 3, "failed": 1, "cases": 2}
    assert baseline["unscored"] == {"holdout_cases": 1, "pending_cases": 1}
    assert baseline["spent"] == {"calls": 7, "retries": 1, "calls_by_role": {"task": 4, "judge": 3},
                                 "seconds_by_role": {"task": 12.5, "judge": 3.25}}
    assert (baseline["approved_calls"], baseline["estimate"]) == (99, {"calls": 99, "seconds": 900})
    # A case file that differs from the frozen dataset hash, and outcomes that do not load, give unknown values.
    changed = baseline_report(record | {"dataset_hash": "other", "outcomes": "broken"}, tmp_path)
    assert (changed["failing"], changed["scored"]) == (None, None)
    assert changed["unscored"] == {"holdout_cases": None, "pending_cases": None} and changed["search"] == "not-run"

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
def test_a_request_that_names_a_sibling_fixture_stops_before_the_case_question_and_any_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, monkeypatch: pytest.MonkeyPatch) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    # Case t1-0 copies the request of t0-0, so it names a fixture that only t0-0 stages.
    _fixture_draft(out, lambda name: f"Read evals/files/{name if name != 't1-0' else 't0-0'}.json, then answer.")
    built: list[tuple[object, ...]] = []

    def factory(*args: object) -> NoReturn:
        built.append(args)
        raise AssertionError("a session started")

    with monkeypatch.context() as process_patch:
        started = _no_model_process(process_patch)
        with pytest.raises(CodedError) as caught:
            _ = run(target, out, MODEL, live=True, factory=factory)
        assert caught.value.code == "case-fixture-missing" and not isinstance(caught.value, Stop)
        assert "t1-0: evals/files/t0-0.json" in str(caught.value) and "no model call ran" in str(caught.value)
        assert built == [] and started == [] and not (out / "cases.json").exists() and not (out / "run.json").exists()
    report = validate(target, out / DRAFT_NAME, edit="prose")
    findings = cast(list[Json], report["findings"])
    assert not report["ok"] and [(found["code"], found["file"]) for found in findings] == [
        ("case-fixture-missing", str(out / DRAFT_NAME))]


@pytest.mark.ac("autoimprove-useful-answer:AC-5")
def test_a_request_needs_no_name_for_an_auxiliary_file_and_may_name_outputs_and_urls(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    # Odd cases name nothing. Even cases name their fixture, outputs in and out of the fixture directory, and a URL
    # whose query names another case's fixture.
    _fixture_draft(out, lambda name: "Answer from the workspace." if name.endswith("1") else
                   f"Read ./evals/files/{name}.json, write evals/files/{name}-answer.md and notes/answer.md; "
                   + "see https://example.com/?file=evals/files/t0-0.json.")
    with pytest.raises(Stop) as asked:
        _ = run(target, out, MODEL, live=True)
    assert asked.value.code == "cases-unapproved"
    assert validate(target, out / DRAFT_NAME)["ok"]


@pytest.mark.ac("autoimprove-useful-answer:AC-4")
@pytest.mark.ac("autoimprove-useful-answer:AC-5")
@pytest.mark.parametrize(("request_text", "missing"), [
    ("Read evals/files/b.json.", ["evals/files/b.json"]),
    ("Read ./evals/files/b.json and `evals/files/shipped.json`.", ["evals/files/b.json", "evals/files/shipped.json"]),
    ("Read fixture.md first.", ["fixture.md"]),
    ("Read evals/files/b.json twice: (evals/files/b.json).", ["evals/files/b.json"]),
    ("Use evals/files/a.json-v2 and evals/files/README, then evals/files/a.json.", []),
    ("Open /repo/evals/files/b.json, ~/evals/files/b.json, or https://example.com/?file=evals/files/b.json.", []),
    ("Write evals/files/out.json, then rename evals/files/a.json to evals/files/AGENTS.md.", []),
    ("If evals/files/absent.json is missing, say so.", []),
    ("Read **evals/files/b.json**, [b](evals/files/b.json), and evals/files/shipped.json:3.",
     ["evals/files/b.json", "evals/files/shipped.json"]),
    ("Write a Makefile and a README for this repo from scratch.", []),
])
def test_the_fixture_check_flags_only_a_known_fixture_that_the_case_does_not_stage(
        tmp_path: Path, request_text: str, missing: list[str]) -> None:
    """A known fixture is staged by some draft case or shipped under the skill's `evals/files/`.

    A bare word without `/` or an extension, such as `Makefile`, is prose even when another case stages that file.
    """
    shipped = tmp_path / "skill/evals/files/shipped.json"
    shipped.parent.mkdir(parents=True)
    _ = shipped.write_text("{}")
    own = {"evals/files/a.json": "{}", "evals/files/a.json-v2": "{}", "evals/files/README": "x"}
    other = {"evals/files/b.json": "{}", "fixture.md": "x", "Makefile": "all:\n", "README": "x"}
    cases = [DraftCase("one", "f", "task", request_text, "skill", own, {}),
             DraftCase("two", "g", "task", "no paths", "skill", other, {})]
    assert [(case.identifier, path) for case, path in fixture_misses(tmp_path / "skill", cases)] == [
        ("one", path) for path in missing]


@pytest.mark.ac("autoimprove-useful-answer:AC-4")
def test_validate_reports_a_seed_fixture_miss_against_the_file_that_holds_the_case(
        tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _ = (target / "evals/files").mkdir(parents=True, exist_ok=True)
    _ = (target / "evals/files/shipped.json").write_text("{}")
    entries: list[dict[str, object]] = [{"name": f"{family}-{index}", "prompt": f"prompt-{family}-{index}", "expected_output": "out",
                "files": [], "family": family} for family in "abcdef" for index in range(2)]
    entries[0]["prompt"] = "Read evals/files/shipped.json, then answer."
    _ = (target / "evals/evals.json").write_text(json.dumps({"skill_name": "echo-skill", "evals": entries}))
    report = validate(target)
    found = [item for item in cast(list[Json], report["findings"]) if item["code"] == "case-fixture-missing"]
    assert [(item["file"], "a-0: evals/files/shipped.json" in cast(str, item["message"])) for item in found] == [
        ("evals/evals.json", True)]

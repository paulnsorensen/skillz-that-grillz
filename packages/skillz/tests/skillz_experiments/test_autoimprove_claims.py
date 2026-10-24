"""The three autoimprove claims: the runner ran, the export reproduces the candidate, the candidate improves held-out outcomes."""
from __future__ import annotations

import json
import statistics
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from skillz_experiments._budget import BudgetExhausted, CallTimeout, estimate, gate_seconds
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._cli import main
from skillz_experiments._contract import Grader
from skillz_experiments._graders import judge
from skillz_experiments._records import read, write
from skillz_experiments.run import Stop, _export, _patch, _replay_phase, _session, export, run, summary
from skillz_experiments.run._open import engine_hash
from skillz_experiments.run._patch import Patch
from skillz_experiments.run._plan import _plan  # pyright: ignore[reportPrivateUsage]
from skillz_experiments.run._ports import Ports
from skillz_experiments.run._replay import apply_patches, drift, prepare_copy
from test_run_search import MODEL, Provider, Search

if TYPE_CHECKING:
    from .conftest import JudgedRun

pytestmark = pytest.mark.usefixtures("host_login", "headroom_off")

ROOT = Path(__file__).resolve().parents[4]
Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]
STATUSES = {"supported", "refuted", "inconclusive"}


def _finished(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver, suffix: str = "",
              ) -> tuple[Path, Path, Search, int, dict[str, object]]:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    state = Search(target)
    state.suffix = suffix
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert result["phase"] == "complete"
    return target, out, state, calls, read(out / "run.json")


def _replays(record: dict[str, object]) -> list[dict[str, object]]:
    return [item for item in cast(list[dict[str, object]], record["outcomes"]) if item.get("arm") == "replay"]


def _rewind(out: Path, record: dict[str, object], keep: int = 0) -> dict[str, object]:
    """Put a completed record back at the gated phase. `keep` scored replay outcomes stay, as an interrupted replay leaves them."""
    replays = _replays(record)
    dropped = replays[keep:]
    cost = cast(dict[str, int], record["estimate"])["replay_calls"] // len(replays)
    record["outcomes"] = [item for item in cast(list[dict[str, object]], record["outcomes"]) if not any(item is gone for gone in dropped)]
    del record["replay"]
    record["phase"] = "gated"
    record["calls"] = cast(int, record["calls"]) - len(dropped) * cost
    write(out / "run.json", record)
    return record


def _resume(target: Path, out: Path, state: Search, ports: Ports | None = None) -> dict[str, object]:
    _ = run(target, out, MODEL, live=True, factory=state.factory, ports=ports)
    return read(out / "run.json")


def _replay_of(record: dict[str, object]) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny]
    return cast(dict[str, Any], record["replay"])  # pyright: ignore[reportExplicitAny]


def _export_status(record: dict[str, object], out: Path) -> str:
    """Return the export claim status that the report derives from the replay reasons."""
    return cast(str, _claims(record, out)["export"]["status"])


@pytest.mark.ac("skillz-autoimprove-claims:AC-1")
def test_export_and_replay_share_one_patch_builder_and_export_refuses_another_digest(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    assert cast(object, getattr(_export, "build_patch")) is _patch.build_patch
    built = _patch.build_patch(record["seed"], record["winner"], target)
    assert _replay_of(record)["patch_digest"] == built.digest
    destination = tmp_path / "exported"
    assert export(out, destination)["patches"] == list(built.files)
    assert (destination / "candidate.patch").read_text() == built.skill
    record["replay"] = {**_replay_of(record), "patch_digest": "0" * 64}
    write(out / "run.json", record)
    other = tmp_path / "refused"
    with pytest.raises(ValueError, match="export differs from the replayed patch; create a new run"):
        _ = export(out, other)
    assert not other.exists()


# A digest that no engine build produces: it stands for the engine hash of an earlier build.
EARLIER_ENGINE = "0" * 64


@pytest.mark.ac("gh-508:AC-1")
def test_a_completed_run_from_an_earlier_engine_exports_when_its_replay_digest_matches(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    built = _patch.build_patch(record["seed"], record["winner"], target)
    assert built.files and _replay_of(record)["patch_digest"] == built.digest
    assert record["engine_hash"] == engine_hash() != EARLIER_ENGINE
    write(out / "run.json", record | {"engine_hash": EARLIER_ENGINE})
    destination = tmp_path / "exported"
    assert export(out, destination)["patches"] == list(built.files)
    assert (destination / "candidate.patch").read_text() == built.skill
    assert (destination / "report.json").is_file()


@pytest.mark.ac("gh-508:AC-2")
@pytest.mark.parametrize("replay", ["no-replay", "no-digest", "other-digest"])
def test_a_completed_run_from_an_earlier_engine_without_a_matching_replay_digest_is_refused_with_a_code(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver, replay: str) -> None:
    _target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    if replay == "no-replay":
        del record["replay"]
    else:
        record["replay"] = {**_replay_of(record), "patch_digest": None if replay == "no-digest" else "1" * 64}
    write(out / "run.json", record | {"engine_hash": EARLIER_ENGINE})
    destination = tmp_path / "refused"
    with pytest.raises(CodedError) as refused:
        _ = export(out, destination)
    assert refused.value.code == "export-engine-unverified"
    assert str(refused.value) == "evaluator differs from the frozen record; create a new run"
    assert not destination.exists()


@pytest.mark.ac("skillz-autoimprove-claims:AC-2")
def test_the_replay_applies_the_exported_patch_with_git_to_a_clean_copy_outside_the_target(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver, monkeypatch: pytest.MonkeyPatch) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    seen: list[list[str]] = []
    real = cast(Callable[..., object], subprocess.run)

    def spy(arguments: list[str], *args: object, **kwargs: object) -> object:
        seen.append(list(arguments))
        return real(arguments, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    state = Search(target)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    winner = cast(dict[str, str], record["winner"])
    assert record["winner_hash"] != record["seed_hash"] and _export_status(record, out) == "supported"
    patch_file = str(out / "replay" / "candidate.patch")
    assert [item[item.index("apply"):] for item in seen if item[:1] == ["git"] and "apply" in item] == [["apply", patch_file]]
    copy = out / "replay" / "skill"
    assert not copy.is_relative_to(target) and (out / "replay").stat().st_mode & 0o777 == 0o700
    for name, text in winner.items():
        assert (copy / name).read_bytes() == text.encode("utf-8")
    assert {path.relative_to(copy).as_posix() for path in copy.rglob("*") if path.is_file()} == set(winner)
    assert not any((target / name).read_text() == text for name, text in winner.items() if "improved" in text)


def _holdout_scores(record: dict[str, object], arm: str) -> dict[str, list[float]]:
    found: dict[str, list[float]] = {}
    for item in cast(list[dict[str, object]], record["outcomes"]):
        if item.get("arm") == arm and item.get("split") == "holdout" and item.get("status") != "evaluation-failed":
            found.setdefault(cast(str, item["case_id"]), []).append(cast(float, item["score"]))
    return found


def _overwrite(copy: Path, text: str) -> None:
    _ = (copy / "skill" / "SKILL.md").write_text(text)


def _tamper_copy(monkeypatch: pytest.MonkeyPatch, change: Callable[[Path], None]) -> None:
    """Run the real `prepare_copy`, then change the clean copy, as a stale or corrupt copy would be."""

    def wrapped(directory: Path, seed: Candidate, patch: Patch) -> Path:
        copy = prepare_copy(directory, seed, patch)
        change(copy)
        return copy

    monkeypatch.setattr(_replay_phase, "prepare_copy", wrapped)


def _tampering_ports(change: Callable[[Path], None]) -> Ports:
    """Ports whose applier runs the real `git apply`, then changes the patched copy."""
    def tampering(directory: Path, patch: Patch) -> str | None:
        failure = apply_patches(directory, patch)
        change(directory)
        return failure

    return Ports(patches=tampering)


@pytest.mark.parametrize("fault", ["patch-apply-failed", "patch-bytes-differ", "replay-identity-differs"])
@pytest.mark.ac("skillz-autoimprove-claims:AC-3")
def test_a_patch_that_fails_differs_or_rebuilds_another_identity_refutes_the_export_claim(
        fault: str, tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    record = _rewind(out, record)
    ports: Ports | None = None
    if fault == "patch-apply-failed":
        _tamper_copy(monkeypatch, lambda copy: _overwrite(copy, "another seed\n"))
    elif fault == "patch-bytes-differ":
        ports = _tampering_ports(lambda copy: _overwrite(copy, "patched, then edited\n"))
    else:
        record["winner_hash"] = "f" * 64
        write(out / "run.json", record)
    state = Search(target)
    after = _resume(target, out, state, ports)
    assert after["phase"] == "complete" and state.holdout_calls == 0
    assert _replay_of(after)["reasons"] == [fault]
    claim = cast(dict[str, object], summary(after, out)["claims"])
    assert cast(dict[str, object], claim["export"])["status"] == "refuted"
    assert cast(dict[str, object], claim["export"])["reasons"] == [fault]
    assert claim["apply"] is False
    assert cast(dict[str, object], claim["improvement"])["status"] == "supported"


def _gated(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver, keep: int = 0,
           suffix: str = "") -> tuple[Path, Path, dict[str, object], dict[str, object]]:
    target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals, suffix)
    done = cast(dict[str, object], json.loads(json.dumps(record)))
    return target, out, done, _rewind(out, record, keep)


@pytest.mark.ac("skillz-autoimprove-claims:AC-4")
def test_the_replay_scores_the_patched_copy_once_on_each_holdout_case_and_records_the_differences(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, _done, _rewound = _gated(tmp_path, make_target, write_draft, approvals)
    state = Search(target)
    after = _resume(target, out, state)
    held = set(_holdout_scores(after, "winner"))
    items = _replays(after)
    assert len(held) == 6 and state.holdout_calls == len(items) == len(held)
    assert sorted(cast(str, item["case_id"]) for item in items) == sorted(held)
    assert {(item["split"], item["repeat"], item["candidate_hash"]) for item in items} == {
        ("holdout", 0, after["winner_hash"])}
    winners = _holdout_scores(after, "winner")
    expected = {cast(str, item["case_id"]): cast(float, item["score"]) - statistics.fmean(winners[cast(str, item["case_id"])])
                for item in items}
    replay = _replay_of(after)
    assert replay["per_case"] == expected and replay["cases"] == len(held)
    assert replay["mean_delta"] == pytest.approx(statistics.fmean(expected.values()))
    assert replay["reasons"] == [] and _export_status(after, out) == "supported"


@pytest.mark.parametrize("cause", ["budget", "drift"])
@pytest.mark.ac("skillz-autoimprove-claims:AC-5")
def test_a_short_replay_budget_or_drifting_replay_scores_make_the_export_claim_inconclusive(
        cause: str, tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, _done, rewound = _gated(tmp_path, make_target, write_draft, approvals)
    maximum = cast(dict[str, int], rewound["budget"])["maximum"]
    state = Search(target)
    if cause == "budget":
        rewound["calls"] = maximum - 1
        write(out / "run.json", rewound)
    else:
        state.score_of = lambda text, _split: 0.0 if "improved" in text else None
    after = _resume(target, out, state)
    reason = "replay-budget-exhausted" if cause == "budget" else "replay-score-drift"
    assert after["phase"] == "complete" and _replay_of(after)["reasons"] == [reason]
    assert cast(int, after["calls"]) <= maximum
    if cause == "budget":
        assert state.holdout_calls == 0 and after["calls"] == maximum - 1 and not _replays(after)
        assert _replay_of(after)["detail"] == {"needed": 6 * state.contract.calls("echo"), "left": 1}
    else:
        assert _replay_of(after)["mean_delta"] < 0 and _replay_of(after)["detail"] == {}
    claims = cast(dict[str, object], summary(after, out)["claims"])
    assert cast(dict[str, object], claims["export"])["status"] == "inconclusive" and claims["apply"] is False


@pytest.mark.ac("skillz-autoimprove-claims:AC-6")
def test_a_resume_in_the_replay_phase_does_not_repeat_a_scored_replay_outcome(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, done, rewound = _gated(tmp_path, make_target, write_draft, approvals, keep=4)
    kept = _replays(rewound)
    assert len(kept) == 4 and len(_replays(done)) == 6
    state = Search(target)
    after = _resume(target, out, state)
    items = _replays(after)
    assert state.holdout_calls == 2 and len(items) == 6
    assert all(any(item == old for item in items) for old in kept)
    assert len({item["case_id"] for item in items}) == 6
    assert after["calls"] == done["calls"] and _export_status(after, out) == "supported"


@pytest.mark.ac("skillz-autoimprove-claims:AC-7")
def test_the_plan_counts_replay_calls_in_the_estimate_the_approval_and_the_holdout_reserve(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    plan = _plan(6, 6, 6, 1, 3)
    without = estimate(preflight_calls=3, holdout=12, repeats=3, search_calls=plan.metric_calls,
                       retry_calls=plan.retry_calls, reflection_calls=plan.reflection_calls)
    assert plan.replay_calls == 6 and plan.headroom_calls == 6
    assert plan.sized.calls == without.calls + plan.replay_calls + plan.headroom_calls
    assert plan.holdout_calls == 2 * 6 * 3 + plan.retry_calls and plan.reserve == plan.holdout_calls + 6
    _target, out, state, calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    planned = cast(dict[str, int], record["estimate"])
    budget = cast(dict[str, int], record["budget"])
    assert planned["replay_calls"] == 6 * state.contract.calls("echo")
    assert budget["maximum"] == calls == planned["calls"]
    assert budget["reserve"] == planned["holdout_retry_calls"] + planned["replay_calls"] + 2 * 6 * 3 * state.contract.calls("echo")
    assert cast(int, record["calls"]) <= calls and (out / "run.json").is_file()


def _first_holdout(record: dict[str, object]) -> str:
    return sorted(_holdout_scores(record, "winner"))[0]


def _claims(record: dict[str, object], out: Path) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny]
    return cast(dict[str, Any], summary(record, out)["claims"])  # pyright: ignore[reportExplicitAny]


@pytest.mark.ac("skillz-autoimprove-claims:AC-8")
def test_a_completed_run_reports_the_three_claims_separately_each_with_one_status(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    _target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    claims = _claims(record, out)
    assert set(claims) == {"runner", "export", "improvement", "apply"}
    statuses = {name: claims[name]["status"] for name in ("runner", "export", "improvement")}
    assert set(statuses.values()) <= STATUSES and set(statuses.values()) == {"supported"}
    assert claims["improvement"]["verdict"] == "promote" and claims["apply"] is True
    assert set(cast(dict[str, object], claims["runner"]["checks"])) == {"preflight", "holdout-isolated", "holdout-coverage", "calls-within-approval",
                                               "calls-within-bound"}
    assert claims["export"]["patch_digest"] == _replay_of(record)["patch_digest"]
    assert claims["runner"]["calls"]["charged"] == record["calls"]
    destination = tmp_path / "exported"
    _ = export(out, destination)
    report = cast(dict[str, Any], json.loads((destination / "report.json").read_text()))  # pyright: ignore[reportExplicitAny]
    assert report["claims"] == claims
    unfinished = {**record, "phase": "gated"}
    assert "claims" not in summary(unfinished, out)


@pytest.mark.parametrize("verdict", ["inconclusive", "promote-cheaper"])
@pytest.mark.ac("skillz-autoimprove-claims:AC-9")
def test_an_inconclusive_or_cheaper_verdict_is_never_an_improvement_and_never_applies(
        verdict: str, tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    _target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    gate = cast(dict[str, object], record["gate"])
    gate["verdict"] = verdict
    claims = _claims(record, out)
    assert claims["improvement"]["status"] == "inconclusive" and claims["improvement"]["verdict"] == verdict
    assert ("cheaper-not-better" in claims["improvement"]["reasons"]) == (verdict == "promote-cheaper")
    assert claims["runner"]["status"] == claims["export"]["status"] == "supported"
    assert claims["apply"] is False
    gate["verdict"] = "reject"
    assert _claims(record, out)["improvement"]["status"] == "refuted"


@pytest.mark.parametrize("check", ["preflight", "holdout-isolated", "holdout-coverage", "calls-within-approval",
                                   "calls-within-bound"])
@pytest.mark.ac("skillz-autoimprove-claims:AC-10")
def test_any_failed_runner_check_refutes_the_runner_claim_and_no_other_claim(
        check: str, tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    _target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    budget = cast(dict[str, int], record["budget"])
    bound = cast(int, summary(record, out)["calls_upper_bound"])
    assert _claims(record, out)["runner"]["status"] == "supported"
    if check == "preflight":
        del record["preflight"]
    elif check == "holdout-isolated":
        outcomes.append({**next(item for item in outcomes if item.get("split") == "holdout"), "arm": "search"})
    elif check == "holdout-coverage":
        victim = next(item for item in outcomes if item.get("arm") == "winner" and item.get("split") == "holdout")
        outcomes.remove(victim)
    elif check == "calls-within-approval":
        record["calls"] = budget["maximum"] + 1
    else:
        budget["maximum"] = bound + 10
        record["calls"] = bound + 1
    claims = _claims(record, out)
    assert claims["runner"]["checks"][check] == "fail" and claims["runner"]["status"] == "refuted"
    assert claims["export"]["status"] == "supported" and claims["improvement"]["status"] == "supported"
    assert claims["apply"] is False


@pytest.mark.ac("skillz-autoimprove-claims:AC-17")
def test_a_failed_winner_holdout_outcome_leaves_the_runner_claim_inconclusive_but_a_missing_one_refutes_it(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    _target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    victims = [item for item in outcomes if item.get("arm") == "winner" and item.get("split") == "holdout"]
    victim, other = victims[0], next(item for item in victims if item["case_id"] != victims[0]["case_id"])
    outcomes[outcomes.index(victim)] = {**{key: value for key, value in victim.items() if key != "score"},
                                        "status": "evaluation-failed", "failure": "CallTimeout"}
    runner = cast(dict[str, Any], _claims(record, out)["runner"])  # pyright: ignore[reportExplicitAny]
    assert runner["checks"]["holdout-coverage"] == "unknown" and runner["status"] == "inconclusive"
    outcomes.remove(other)
    runner = cast(dict[str, Any], _claims(record, out)["runner"])  # pyright: ignore[reportExplicitAny]
    assert runner["checks"]["holdout-coverage"] == "fail" and runner["status"] == "refuted"

@pytest.mark.ac("skillz-autoimprove-claims:AC-11")
def test_the_improvement_claim_lists_the_case_count_and_mean_paired_delta_of_each_family(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    from skillz_experiments._cases import load_cases
    _target, out, state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    holdout = [case for case in load_cases(out / "cases.json", state.contract.grader_types()) if case.split == "holdout"]
    base, win = _holdout_scores(record, "baseline"), _holdout_scores(record, "winner")
    expected: dict[str, tuple[int, float]] = {}
    for family in sorted({case.family for case in holdout}):
        members = [case.identifier for case in holdout if case.family == family]
        deltas = [statistics.fmean(win[name]) - statistics.fmean(base[name]) for name in members]
        expected[family] = (len(members), statistics.fmean(deltas))
    improvement = cast(dict[str, object], _claims(record, out)["improvement"])
    families = cast(dict[str, dict[str, Any]], improvement["families"])  # pyright: ignore[reportExplicitAny]
    assert set(families) == set(expected) and len(expected) > 1
    for family, (count, mean) in expected.items():
        assert families[family]["cases"] == count
        assert families[family]["mean_delta"] == pytest.approx(mean)
    assert sum(item["cases"] for item in families.values()) == improvement["cases"] == len(holdout)
    assert improvement["repeats"] == record["repeats"] and improvement["delta"] == cast(dict[str, object], record["gate"])["delta"]


class _Recorder:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, prompt: str, *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del holdout, schema
        self.prompts.append(prompt)
        return {"answer": {"criteria": {"echo": {"level": 3, "reason": "echoes the request"}}}}


@pytest.mark.ac("skillz-autoimprove-claims:AC-12")
def test_the_judge_prompt_names_no_arm_and_no_candidate_identity() -> None:
    case = Case("case-1", "family-1", "holdout", "Echo this request.", {"input.txt": "hello"},
                {"criteria": [{"id": "echo", "text": "THE answer SHALL echo the request."}]}, "synthetic", True)
    recorder = _Recorder()
    score, _verdicts, _result = judge(recorder, Grader("judge", rubric="Rate the echo."), case, {"text": "hello"}, None,
                                     holdout=True)
    assert score == pytest.approx(2 / 3) and len(recorder.prompts) == 1
    prompt = recorder.prompts[0]
    data = cast(dict[str, object], json.loads(prompt.split("UNTRUSTED DATA (JSON):\n", 1)[1]))
    assert set(data) == {"request", "files", "answer", "output_files", "reference"}
    assert not any(word in prompt.lower() for word in ("baseline", "winner", "replay", "candidate_hash"))


@pytest.mark.ac("skillz-autoimprove-claims:AC-12")
def test_no_judge_prompt_of_a_real_run_holds_an_arm_name_or_a_candidate_hash(judged: JudgedRun) -> None:
    result = judged.start()
    record = read(judged.out / "run.json")
    assert result["phase"] == "complete" and record["winner_hash"] and record["seed_hash"]
    requests = [cast(dict[str, object], json.loads(line)) for line in judged.log.read_text().splitlines()]
    prompts = [str(item["prompt"]) for item in requests if item["operation"] == "infer"
               and "criteria" in cast(list[str], cast(dict[str, object], item["response_schema"])["required"])]
    assert prompts
    forbidden = (cast(str, record["winner_hash"]), cast(str, record["seed_hash"]), "baseline", "winner", "replay")
    for prompt in prompts:
        assert not any(word in prompt.lower() for word in forbidden)


@pytest.mark.ac("skillz-autoimprove-claims:AC-13")
def test_experiments_reference_requires_example_before_draft_and_three_claims() -> None:
    text = (ROOT / "skills/skillz/references/experiments.md").read_text()
    draft = text[text.index("## Draft the cases"):]
    assert draft.index("write one representative example") < draft.index("Write `RUN/cases.draft.json`")
    assert "observable checks that refute it" in draft
    start = text.index("### Reasons and reporting")
    reporting = text[start:text.index("\n#", start + 1)]
    for name in ("`runner`", "`export`", "`improvement`", "`claims.apply`"):
        assert name in reporting, name


@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.ac("skillz-autoimprove-claims:AC-2")
def test_the_replay_ignores_a_git_repository_above_the_run_directory(
        relative: bool, tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    _ = subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)
    target = make_target(tmp_path)
    out = Path("run") if relative else tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    state = Search(target)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(tmp_path / "run" / "run.json")
    assert record["winner_hash"] != record["seed_hash"]
    assert _export_status(record, tmp_path / "run") == "supported" and _replay_of(record)["reasons"] == []


@pytest.mark.ac("skillz-autoimprove-claims:AC-4")
def test_the_rerun_candidate_comes_from_the_patched_copy_not_from_the_recorded_winner(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, _done, _rewound = _gated(tmp_path, make_target, write_draft, approvals)
    real = cast(Callable[..., list[str]], getattr(_replay_phase, "differences"))

    def edited_after_the_check(copy: Path, seed: Candidate, patch: object) -> list[str]:
        _ = real(copy, seed, patch)
        _overwrite(copy, "edited after the byte check\n")
        return []

    monkeypatch.setattr(_replay_phase, "differences", edited_after_the_check)
    state = Search(target)
    after = _resume(target, out, state)
    replay = _replay_of(after)
    assert _export_status(after, out) == "refuted" and replay["reasons"] == ["replay-identity-differs"]
    assert replay["detail"]["expected"] == after["winner_hash"] != replay["detail"]["found"]
    assert state.holdout_calls == 0 and not _replays(after)


@pytest.mark.ac("skillz-autoimprove-claims:AC-5")
def test_drift_compares_the_mean_plus_two_standard_errors_with_the_negative_tolerance() -> None:
    deltas = [-0.7, -0.3]
    mean, se, drifted = drift(deltas, 0.09)
    assert mean == pytest.approx(-0.5) and se == pytest.approx(0.2) and drifted
    assert not drift(deltas, 0.11)[2]
    mean, se, drifted = drift([-0.3], 0.2)
    assert (mean, se, drifted) == (-0.3, 0.0, True)
    assert not drift([-0.3], 0.3)[2]
    assert not drift([0.1, 0.3], 0.0)[2]


def _tag_replays(monkeypatch: pytest.MonkeyPatch, victim: str | None, fields: dict[str, object],
                 fault: Exception | None = None) -> None:
    """Make the fake provider mark the result of `victim` (every case when None) with `fields`, or claim a call and raise `fault`."""
    real = Provider.evaluate

    def evaluate(self: Provider, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: object = None) -> dict[str, object]:
        chosen = victim is None or case.identifier == victim
        if chosen and fault is not None and holdout:
            self.budget.claim(holdout=True)
            raise fault
        result = real(self, candidate, case, holdout=holdout, checks=checks)
        if chosen:
            result.update(fields)
        return result

    monkeypatch.setattr(Provider, "evaluate", evaluate)


@pytest.mark.ac("skillz-autoimprove-claims:AC-5")
def test_one_replay_case_that_does_not_load_the_skill_makes_the_export_claim_inconclusive(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, done, _rewound = _gated(tmp_path, make_target, write_draft, approvals)
    _tag_replays(monkeypatch, _first_holdout(done), {"loaded": False})
    after = _resume(target, out, Search(target))
    assert _export_status(after, out) == "inconclusive" and _replay_of(after)["reasons"] == ["replay-not-loaded"]
    assert len(_replays(after)) == 6
    claims = _claims(after, out)
    assert claims["export"]["status"] == "inconclusive" and claims["runner"]["status"] == "supported"
    assert claims["improvement"]["status"] == "supported" and claims["apply"] is False


@pytest.mark.ac("skillz-autoimprove-claims:AC-3")
def test_a_replay_case_that_the_contract_rejects_refutes_the_export_claim(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, done, _rewound = _gated(tmp_path, make_target, write_draft, approvals)
    _tag_replays(monkeypatch, _first_holdout(done), {"status": "candidate-contract-rejected"})
    after = _resume(target, out, Search(target))
    assert _export_status(after, out) == "refuted" and _replay_of(after)["reasons"] == ["replay-contract-rejected"]
    claims = _claims(after, out)
    assert claims["export"]["status"] == "refuted" and claims["apply"] is False


@pytest.mark.ac("skillz-autoimprove-claims:AC-5")
def test_a_replay_evaluation_that_fails_leaves_a_case_unscored_and_the_export_claim_inconclusive(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, _done, _rewound = _gated(tmp_path, make_target, write_draft, approvals)
    state = Search(target)
    state.holdout_fault = lambda number: OSError("provider fell over") if number == 1 else None
    after = _resume(target, out, state)
    replay = _replay_of(after)
    assert after["phase"] == "complete" and _export_status(after, out) == "inconclusive"
    assert replay["reasons"] == ["replay-evaluation-failed"] and replay["cases"] == 5
    failed = [item for item in cast(list[dict[str, object]], after["outcomes"])
              if item.get("arm") == "replay" and item.get("status") == "evaluation-failed"]
    assert len(failed) == 1 and len(_replays(after)) == 6
    assert _claims(after, out)["apply"] is False


@pytest.mark.ac("skillz-autoimprove-claims:AC-3")
def test_a_wedge_source_outside_the_repository_fails_the_patch_and_keeps_the_error_code(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, _done, rewound = _gated(tmp_path, make_target, write_draft, approvals)
    cast(dict[str, str], rewound["winner"])["@wedge/../escape.py"] = "x = 1\n"
    write(out / "run.json", rewound)
    state = Search(target)
    after = _resume(target, out, state)
    replay = _replay_of(after)
    assert _export_status(after, out) == "refuted" and replay["reasons"] == ["patch-apply-failed"]
    assert replay["detail"]["code"] == "export-path-escapes" and "stderr" not in replay["detail"]
    assert replay["patch_digest"] is None and state.holdout_calls == 0


@pytest.mark.ac("skillz-autoimprove-claims:AC-5")
def test_a_rebuild_failure_is_inconclusive_with_its_code_and_not_a_refutation(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, _done, _rewound = _gated(tmp_path, make_target, write_draft, approvals)

    def broken(_self: object, _candidate: Candidate) -> Candidate:
        raise CodedError("build-failed", "the toolchain is missing")

    monkeypatch.setattr(_session.Session, "_built", broken)
    state = Search(target)
    after = _resume(target, out, state)
    replay = _replay_of(after)
    assert _export_status(after, out) == "inconclusive" and replay["reasons"] == ["replay-build-failed"]
    assert replay["detail"]["code"] == "build-failed" and state.holdout_calls == 0
    assert _claims(after, out)["export"]["status"] == "inconclusive"


@pytest.mark.ac("skillz-autoimprove-claims:AC-5")
def test_a_missing_git_stops_the_run_without_a_verdict_and_a_resume_completes_the_replay(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, _done, _rewound = _gated(tmp_path, make_target, write_draft, approvals)
    with monkeypatch.context() as patched:
        patched.setenv("PATH", str(tmp_path / "no-git"))
        with pytest.raises(CodedError) as raised:
            _ = _resume(target, out, Search(target))
    assert raised.value.code == "replay-git-unavailable"
    stalled = read(out / "run.json")
    assert stalled["phase"] == "gated" and "replay" not in stalled
    after = _resume(target, out, Search(target))
    assert after["phase"] == "complete" and _export_status(after, out) == "supported"


@pytest.mark.ac("skillz-autoimprove-claims:AC-5")
@pytest.mark.parametrize("preflight_reads_deadline", [False, True], ids=["reused-preflight", "codex-like-preflight"])
def test_a_gated_run_that_resumes_after_the_deadline_concludes_the_replay_without_a_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch, preflight_reads_deadline: bool) -> None:
    target, out, _done, rewound = _gated(tmp_path, make_target, write_draft, approvals)
    seconds = cast(dict[str, float], rewound["budget"])["seconds"]
    rewound["started"] = time.time() - seconds - 10
    rewound["started_monotonic"] = time.monotonic() - seconds - 10
    write(out / "run.json", rewound)
    if preflight_reads_deadline:
        # The Codex preflight bounds each probe with `budget.remaining()`, which raises after the deadline.
        original = Provider.preflight

        def deadline_preflight(self: Provider) -> dict[str, object]:
            _ = self.budget.remaining()
            return original(self)

        monkeypatch.setattr(Provider, "preflight", deadline_preflight)
    state = Search(target)
    after = _resume(target, out, state)
    replay = _replay_of(after)
    assert after["phase"] == "complete" and _export_status(after, out) == "inconclusive"
    assert replay["reasons"] == ["replay-budget-exhausted"] and replay["detail"] == {"shortfall": "deadline"}
    assert state.holdout_calls == 0 and after["calls"] == rewound["calls"] and not _replays(after)


@pytest.mark.ac("skillz-autoimprove-claims:AC-5")
def test_a_budget_stop_inside_a_replay_case_keeps_an_outcome_for_its_calls_and_does_not_refute_the_runner(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, done, _rewound = _gated(tmp_path, make_target, write_draft, approvals)
    victim = _first_holdout(done)
    _tag_replays(monkeypatch, victim, {}, fault=BudgetExhausted("global invocation budget exhausted"))
    after = _resume(target, out, Search(target))
    replay = _replay_of(after)
    assert after["phase"] == "complete" and _export_status(after, out) == "inconclusive"
    assert replay["reasons"] == ["replay-budget-exhausted"] and replay["detail"] == {"shortfall": "calls"}
    failed = [item for item in cast(list[dict[str, object]], after["outcomes"])
              if item.get("arm") == "replay" and item.get("case_id") == victim]
    assert [item["status"] for item in failed] == ["evaluation-failed"]
    # A case that meets the latched stop makes no call, so it records no outcome and adds nothing to the call bound.
    assert {item["case_id"] for item in _replays(after) if item.get("status") == "evaluation-failed"} == {victim}
    claims = _claims(after, out)
    assert claims["runner"]["status"] == "supported" and claims["export"]["status"] == "inconclusive"


@pytest.mark.ac("skillz-autoimprove-claims:AC-5")
def test_the_gate_precheck_leaves_the_replay_reserve_unspent(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    cost = state.contract.calls("echo")
    maximum = cast(dict[str, int], record["budget"])["maximum"]
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    record["outcomes"] = [item for item in outcomes if item.get("split") != "holdout"]
    for key in ("gate", "replay"):
        del record[key]
    record["phase"] = "searched"
    gate_need, replay_need = 2 * 6 * 3 * cost, 6 * cost
    record["calls"] = maximum - gate_need - replay_need + 1
    write(out / "run.json", record)
    short = Search(target)
    with pytest.raises(Stop) as raised:
        _ = run(target, out, MODEL, live=True, factory=short.factory)
    assert raised.value.code == "gate-budget-exhausted" and "after the replay reserve" in str(raised.value)
    assert short.holdout_calls == 0
    record["calls"] = maximum - gate_need - replay_need
    write(out / "run.json", record)
    enough = Search(target)
    after = _resume(target, out, enough)
    assert after["phase"] == "complete" and cast(int, after["calls"]) <= maximum
    assert _export_status(after, out) == "supported" and enough.holdout_calls == gate_need // cost + 6


@pytest.mark.ac("skillz-autoimprove-claims:AC-10")
def test_a_record_without_a_call_bound_leaves_the_runner_claim_unknown(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    _target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    del record["preflight_calls"]
    unbounded = _claims(record, out)
    assert unbounded["runner"]["checks"]["calls-within-bound"] == "unknown"
    assert unbounded["runner"]["status"] == "inconclusive" and unbounded["apply"] is False


@pytest.mark.ac("skillz-autoimprove-claims:AC-7")
def test_the_reserved_seconds_cover_the_gate_and_the_replay_calls_and_the_gate_sandbox_time() -> None:
    plain = _plan(6, 6, 6, 1, 3)
    assert plain.reserve_seconds == gate_seconds(plain.holdout_calls + plain.replay_calls) + plain.sandbox_gate
    assert plain.reserve_seconds > gate_seconds(plain.holdout_calls)
    boxed = _plan(6, 6, 6, 1, 3, sandbox_seconds=30)
    assert boxed.sandbox_gate > 0
    assert boxed.reserve_seconds == gate_seconds(boxed.holdout_calls + boxed.replay_calls) + boxed.sandbox_gate


@pytest.mark.ac("skillz-autoimprove-claims:AC-9")
def test_a_tie_run_replays_nothing_and_never_applies(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    state = Search(target)
    state.score_of = lambda _text, _split: 0.0
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    assert record["phase"] == "complete" and record["winner_hash"] == record["seed_hash"]
    replay = _replay_of(record)
    assert _export_status(record, out) == "inconclusive" and replay["reasons"] == ["winner-equals-baseline"]
    assert replay["patch_digest"] is None and not _replays(record) and state.holdout_calls > 0
    claims = _claims(record, out)
    assert claims["export"]["status"] == "inconclusive" and claims["improvement"]["status"] == "inconclusive"
    assert claims["runner"]["checks"]["holdout-coverage"] == "pass" and claims["runner"]["status"] == "supported"
    assert claims["apply"] is False


@pytest.mark.ac("gh-336:AC-1")
def test_a_tie_run_exports_no_patch_file_and_names_the_reason(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        capsys: pytest.CaptureFixture[str]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    state = Search(target)
    state.score_of = lambda _text, _split: 0.0
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert cast(dict[str, object], read(out / "run.json")["gate"])["reasons"] == ["winner-equals-baseline"]
    destination = tmp_path / "exported"
    _ = capsys.readouterr()
    assert main(["export", str(out), "--out", str(destination)]) == 0
    exported = cast(dict[str, object], json.loads(capsys.readouterr().out))
    assert exported["patches"] == [] and exported["reason"] == "winner-equals-baseline"
    assert sorted(path.name for path in destination.iterdir()) == ["report.json"]
    report = read(destination / "report.json")
    assert report["patches"] == [] and report["reason"] == "winner-equals-baseline"


@pytest.mark.ac("skillz-autoimprove-claims:AC-2")
def test_a_winner_with_crlf_line_endings_replays_to_the_same_identity(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    _target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals,
                                                      suffix="\r\nsecond line\r\n")
    winner = cast(dict[str, str], record["winner"])
    assert record["winner_hash"] != record["seed_hash"] and "\r\n" in winner["SKILL.md"]
    assert _replay_of(record)["reasons"] == [] and _export_status(record, out) == "supported"


@pytest.mark.ac("skillz-autoimprove-claims:AC-2")
def test_a_user_git_attributes_file_does_not_change_the_replayed_bytes(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, _done, _rewound = _gated(tmp_path, make_target, write_draft, approvals, suffix="\nsecond line\n")
    config = tmp_path / "user-config"
    (config / "git").mkdir(parents=True)
    _ = (config / "git" / "attributes").write_text("* text eol=crlf\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    after = _resume(target, out, Search(target))
    assert _replay_of(after)["reasons"] == [] and _export_status(after, out) == "supported"


@pytest.mark.ac("skillz-autoimprove-claims:AC-8")
def test_the_report_omits_the_replay_detail_that_holds_git_stderr(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, _done, _rewound = _gated(tmp_path, make_target, write_draft, approvals)
    _tamper_copy(monkeypatch, lambda copy: _overwrite(copy, "another seed\n"))
    after = _resume(target, out, Search(target))
    stderr = cast(str, _replay_of(after)["detail"]["stderr"])
    assert stderr
    destination = tmp_path / "exported"
    _ = export(out, destination)
    text = (destination / "report.json").read_text()
    report = cast(dict[str, Any], json.loads(text))  # pyright: ignore[reportExplicitAny]
    assert report["replay"]["reasons"] == ["patch-apply-failed"] and "detail" not in report["replay"]
    assert stderr not in text


@pytest.mark.ac("skillz-autoimprove-claims:AC-8")
def test_a_completed_run_whose_contract_no_longer_parses_omits_the_claims_with_a_reason(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    _target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    record["contract"] = {"schema_version": 0}
    result = summary(record, out)
    assert "claims" not in result and "contract" in cast(str, result["claims_unavailable"])
    assert result["calls_upper_bound"] is None


@pytest.mark.ac("skillz-autoimprove-claims:AC-14")
def test_a_replay_call_timeout_is_unscored_and_leaves_the_export_claim_inconclusive(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, done, _rewound = _gated(tmp_path, make_target, write_draft, approvals)
    victim = _first_holdout(done)
    _tag_replays(monkeypatch, victim, {}, fault=CallTimeout("the model call exceeded its per-call timeout of 240 seconds"))
    after = _resume(target, out, Search(target))
    replay = _replay_of(after)
    assert after["phase"] == "complete"
    assert replay["reasons"] == ["replay-evaluation-failed"] and _export_status(after, out) == "inconclusive"
    failed = [item for item in cast(list[dict[str, object]], after["outcomes"])
              if item.get("arm") == "replay" and item.get("status") == "evaluation-failed"]
    assert [(item["case_id"], item["reason"]) for item in failed] == [(victim, "timeout")]
    # The failed case adds no delta, so a timeout never counts as a 0.0 score.
    per_case = cast(dict[str, float], replay["per_case"])
    assert victim not in per_case and replay["cases"] == len(per_case) == 5
    assert _claims(after, out)["apply"] is False


@pytest.mark.parametrize("past_deadline", [False, True], ids=["in-time", "past-deadline"])
@pytest.mark.ac("skillz-autoimprove-claims:AC-15")
def test_replay_calls_keep_calls_by_role_summing_to_calls_after_a_post_deadline_resume(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver, past_deadline: bool) -> None:
    target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    by_role = cast(dict[str, int], record["calls_by_role"])
    assert record["calls"] == sum(by_role.values()) and _replays(record)
    spent = cast(int, record["calls"])
    if past_deadline:
        # Back to the gated phase with every call kept, past the deadline: the resume makes no call and keeps the role counts.
        record["outcomes"] = [item for item in cast(list[dict[str, object]], record["outcomes"]) if item.get("arm") != "replay"]
        del record["replay"]
        record["phase"] = "gated"
        seconds = cast(dict[str, float], record["budget"])["seconds"]
        record["started"] = time.time() - seconds - 10
        record["started_monotonic"] = time.monotonic() - seconds - 10
    else:
        # An in-time resume replays the dropped cases: task and judge calls join the recorded role counts.
        dropped = _replays(record)
        cost = cast(dict[str, int], record["estimate"])["replay_calls"] // len(dropped)
        record["outcomes"] = [item for item in cast(list[dict[str, object]], record["outcomes"]) if item.get("arm") != "replay"]
        del record["replay"]
        record["phase"] = "gated"
        record["calls"] = spent - len(dropped) * cost
        record["calls_by_role"] = {"task": by_role["task"] - len(dropped) * cost,
                                   **{role: count for role, count in by_role.items() if role != "task"}}
    write(out / "run.json", record)
    before = record["calls"]
    after = _resume(target, out, Search(target))
    assert after["phase"] == "complete"
    assert after["calls"] == sum(cast(dict[str, int], after["calls_by_role"]).values())
    if past_deadline:
        assert after["calls"] == before and _replay_of(after)["reasons"] == ["replay-budget-exhausted"]
    else:
        assert after["calls"] == spent and _replays(after)


@pytest.mark.ac("skillz-autoimprove-claims:AC-16")
def test_the_run_summary_reports_claims_next_to_retries_and_search_fault_counts(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    _target, out, _state, _calls, record = _finished(tmp_path, make_target, write_draft, approvals)
    result = summary(record, out)
    assert {"claims", "retries", "calls_by_role", "seconds_by_role", "replay"} <= set(result)
    assert result["retries"] == record["retries"] and result["calls_by_role"] == record["calls_by_role"]
    search = cast(dict[str, object], result["search"])
    assert {"scored", "failed", "failed_by_reason"} <= set(search) and search["failed"] == 0
    assert "detail" not in cast(dict[str, object], result["replay"])
    destination = tmp_path / "exported"
    _ = export(out, destination)
    report = cast(dict[str, Any], json.loads((destination / "report.json").read_text()))  # pyright: ignore[reportExplicitAny]
    assert report["claims"] == result["claims"] and report["retries"] == record["retries"]
    assert report["calls_by_role"] == record["calls_by_role"] and report["search"]["scored"] == search["scored"]

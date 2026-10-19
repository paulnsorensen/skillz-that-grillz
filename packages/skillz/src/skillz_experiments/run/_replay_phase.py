"""The replay phase: check that the exported patch reproduces the winner, then score the patched copy on the holdout.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import cast

from skillz_experiments._budget import BudgetExhausted
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import CodedError, mapping
from skillz_experiments.run._holdout import arm_scores, replay_outcomes, score_holdout
from skillz_experiments.run._outcomes import rejection_detail, timed_out
from skillz_experiments.run._patch import build_patch
from skillz_experiments.run._plan import case_calls
from skillz_experiments.run._record_fields import field, text_field, unit_score
from skillz_experiments.run._replay import STDERR_LIMIT, apply_patches, differences, prepare_copy
from skillz_experiments.run._replay import components as replay_components
from skillz_experiments.run._replay import drift as replay_drift
from skillz_experiments.run._state import RunState
from skillz_experiments.run._stop import check_timeout


def _conclude(state: RunState, reasons: list[str], patch_digest: str | None = None, *,
              detail: dict[str, object] | None = None, per_case: dict[str, float] | None = None,
              mean: float | None = None, se: float | None = None) -> None:
    """Record the replay reasons and evidence and end the phase. The claims report maps the reasons to a status."""
    state.record["replay"] = {
        "reasons": reasons,
        "patch_digest": patch_digest, "cases": len(per_case or {}), "mean_delta": mean, "se": se,
        "per_case": per_case or {}, "tolerance": state.statistics.min_gain, "detail": detail or {}}
    state.record["phase"] = "replayed"
    state.checkpoint()


def run_replay(state: RunState, built: Callable[[Candidate], Candidate]) -> None:
    """Check that the exported patch reproduces the winner, then score the patched copy once on the holdout.

    The copy sits under `out/replay`. Git applies the patches to it, as a user does. The rerun candidate
    comes from the patched files and goes through the harness like any holdout candidate.
    A resume skips each case that already has a scored replay outcome.
    """
    record, seed = state.record, state.seed
    if "winner-equals-baseline" in cast(list[object], mapping(field(record, "gate")).get("reasons", [])):
        _conclude(state, ["winner-equals-baseline"])
        return
    winner = field(record, "winner")
    try:
        patch = build_patch(field(record, "seed"), winner, Path(text_field(record, "target_root")))
    except CodedError as error:
        _conclude(state, ["patch-apply-failed"], detail={"code": error.code, "error": str(error)[:STDERR_LIMIT]})
        return
    copy = prepare_copy(state.out / "replay", seed, patch)
    failure = apply_patches(copy, patch)
    if failure is not None:
        _conclude(state, ["patch-apply-failed"], patch.digest, detail={"stderr": failure})
        return
    differing = differences(copy, seed, patch)
    if differing:
        _conclude(state, ["patch-bytes-differ"], patch.digest, detail={"differing": differing})
        return
    try:
        rebuilt = built(seed.changed(replay_components(copy, seed, patch, winner)))
    except CodedError as error:
        _conclude(state, ["replay-build-failed"], patch.digest,
                  detail={"code": error.code, "error": str(error)[:STDERR_LIMIT]})
        return
    except ValueError as error:
        _conclude(state, ["replay-identity-differs"], patch.digest, detail={"error": str(error)[:STDERR_LIMIT]})
        return
    if rebuilt.identity != text_field(record, "winner_hash"):
        _conclude(state, ["replay-identity-differs"], patch.digest,
                  detail={"expected": text_field(record, "winner_hash"), "found": rebuilt.identity})
        return
    shortfall = _score_replay(state, rebuilt)
    newest = replay_outcomes(state)
    holdout = state.cases_for("holdout")
    reasons: list[str] = []
    if shortfall is not None:
        reasons.append("replay-budget-exhausted")
    elif any(case.identifier not in newest for case in holdout):
        reasons.append("replay-evaluation-failed")
    detail = shortfall or {}
    if any(item.get("status") == "candidate-contract-rejected" for item in newest.values()):
        _conclude(state, [*reasons, "replay-contract-rejected"], patch.digest, detail=detail)
        return
    if any(item.get("loaded") is False for item in newest.values()):
        _conclude(state, [*reasons, "replay-not-loaded"], patch.digest, detail=detail)
        return
    winners = arm_scores(state.outcomes, "winner")
    per_case = {identifier: unit_score(item["score"]) - math.fsum(winners[identifier]) / len(winners[identifier])
                for identifier, item in sorted(newest.items()) if winners.get(identifier)}
    mean = se = None
    if per_case:
        mean, se, drifted = replay_drift(list(per_case.values()), state.statistics.min_gain)
        if drifted:
            reasons.append("replay-score-drift")
    _conclude(state, reasons, patch.digest, detail=detail, per_case=per_case, mean=mean, se=se)


def _score_replay(state: RunState, rebuilt: Candidate) -> dict[str, object] | None:
    """Evaluate `rebuilt` once on each holdout case that has no scored replay outcome.

    Return None when the replay has all its outcomes, else the shortfall detail when the budget ran short.
    The check runs before any call. A short budget makes no call and raises no stop.
    A final check timeout stops with the resumable `winner-check-timeout`, because the replay checks the winner.
    """
    done = replay_outcomes(state)
    todo = [case for case in state.cases_for("holdout") if case.identifier not in done]
    if not todo:
        return None

    def inspect(arm: str, result: dict[str, object]) -> None:
        del arm
        if timed_out(result):
            raise check_timeout("winner", rejection_detail(result))

    try:
        _ = state.budget.remaining()
        needed, left = sum(case_calls(state.contract, case) for case in todo), state.budget.maximum - state.budget.calls
        if left < needed:
            return {"needed": needed, "left": left}
        score_holdout(state, [(rebuilt, case, "replay", 0) for case in todo], inspect=inspect, keep_budget_stop=True)
    except BudgetExhausted as error:
        state.faults.take_budget_stop()
        return {"shortfall": "deadline" if "deadline" in str(error) else "calls"}
    return None

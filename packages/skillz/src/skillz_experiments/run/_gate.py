"""The gate phase: score the baseline and the winner on the unseen holdout, then record the verdict.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from skillz_experiments._candidate import Candidate, candidate_files
from skillz_experiments._cases import CodedError
from skillz_experiments.gate import Verdict, holdout_deltas
from skillz_experiments.gate import verdict as gate_verdict
from skillz_experiments.run._holdout import arm_scores, holdout_tokens, replay_need, score_holdout
from skillz_experiments.run._outcomes import holdout_unscored, is_failed, reason_counts, rejection_detail, timed_out
from skillz_experiments.run._plan import case_calls
from skillz_experiments.run._record_fields import field, int_field
from skillz_experiments.run._state import RunState
from skillz_experiments.run._stop import Stop, baseline_rejected, check_timeout


def run_gate(state: RunState, built: Callable[[Candidate], Candidate]) -> None:
    """Score baseline and winner on the unseen holdout, `repeats` times per case, then record the verdict."""
    holdout = state.cases_for("holdout")
    repeats = int_field(state.record, "repeats")
    seed, record, outcomes, statistics = state.seed, state.record, state.outcomes, state.statistics
    try:
        winner = built(seed.changed({name: candidate_files(field(record, "winner"))[name] for name in seed.editable}))
    except CodedError as error:
        raise Stop("build-failed", str(error), {"next": "the winner no longer builds; start a new run in a new directory"}) from None
    families = {case.identifier: case.family for case in holdout}
    if winner.identity == seed.identity:
        tie = gate_verdict({case.identifier: 0.0 for case in holdout}, thresholds=statistics, families=families)
        outcome = Verdict("inconclusive", 0.0, 0.0, tie.cases, ("winner-equals-baseline",), tie.skipped_families)
        record["gate"] = {**outcome.data(), "repeats": repeats}
    else:
        _ = state.budget.remaining()
        rejected = next((item for item in outcomes if item.get("arm") == "baseline"
                         and item.get("status") == "candidate-contract-rejected" and not timed_out(item)), None)
        if rejected is not None:
            raise baseline_rejected(rejection_detail(rejected))
        arms = {"baseline": seed, "winner": winner}
        done = {(item.get("arm"), item.get("case_id"), item.get("repeat")) for item in outcomes
                if item.get("split") == "holdout" and not holdout_unscored(item)}
        todo = [(arm, case, repeat) for arm in arms for case in holdout for repeat in range(repeats)
                if (arm, case.identifier, repeat) not in done]

        def inspect(arm: str, result: dict[str, object]) -> None:
            if timed_out(result):
                raise check_timeout(arm, rejection_detail(result))
            if arm == "baseline" and result.get("status") == "candidate-contract-rejected":
                raise baseline_rejected(rejection_detail(result))

        need = sum(case_calls(state.contract, case) for _, case, _ in todo)
        left = state.budget.maximum - state.budget.calls - replay_need(state)
        if left < need:
            raise Stop("gate-budget-exhausted", f"the holdout gate needs {need} more calls; "
                       + f"{left} remain in the approved budget after the replay reserve",
                       {"next": "start a new run in a new directory"})
        score_holdout(state, [(arms[arm], case, arm, repeat) for arm, case, repeat in todo], inspect=inspect)
        paired = holdout_deltas(arm_scores(outcomes, "baseline"), arm_scores(outcomes, "winner"))
        lost = [item for item in outcomes if item.get("split") == "holdout" and item.get("arm") in arms and is_failed(item)]
        if lost and not paired.deltas:
            raise CodedError("holdout-faulted", f"no holdout case has a scored baseline and winner pair; {len(lost)} holdout "
                             + f"evaluations failed ({reason_counts(lost)}); wait for the provider, then resume the run")
        tokens = ({case_id: arms_tokens for case_id, arms_tokens in holdout_tokens(outcomes).items()
                   if case_id in paired.deltas} if statistics.token_rule else None)
        outcome = replace(gate_verdict(paired.deltas, thresholds=statistics, families=families,
                                       tokens=tokens, se_floor=paired.se_floor,
                                       planned=len(holdout)), moves=paired.moves)
        record["gate"] = {**outcome.data(), "repeats": repeats}
    record["winner_hash"] = winner.identity
    record["phase"] = "gated"
    state.checkpoint()

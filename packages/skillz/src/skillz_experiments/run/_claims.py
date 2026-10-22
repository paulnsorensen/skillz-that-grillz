"""Report the three claims of a finished run: runner, export, and improvement. Each has its own evidence.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import cast

from skillz_experiments._cases import Case, mapping
from skillz_experiments.gate import holdout_deltas
from skillz_experiments.run._outcomes import by_repeat_order, holdout_scores
from skillz_experiments.run._record_fields import record_outcomes

ARMS = ("baseline", "winner")

# The replay reasons that refute the export claim. Any other reason leaves it inconclusive.
REPLAY_REFUTED = ("patch-apply-failed", "patch-bytes-differ", "replay-identity-differs", "replay-contract-rejected")


def _status(checks: Mapping[str, str]) -> str:
    values = set(checks.values())
    return "refuted" if "fail" in values else "inconclusive" if "unknown" in values else "supported"


def _runner_claim(record: dict[str, object], outcomes: Sequence[Mapping[str, object]],
                  arms: Mapping[str, Mapping[str, Mapping[int, float]]], holdout: Sequence[Case],
                  bound: int | None) -> dict[str, object]:
    repeats = cast(int, record.get("repeats"))
    same = record.get("winner_hash") == record.get("seed_hash")
    # A retained seed scores its baseline alone, so only the baseline slots can be missing.
    scored = ["baseline"] if same else list(arms)
    failed = {(item.get("arm"), item.get("case_id"), item.get("repeat")) for item in outcomes
              if item.get("split") == "holdout" and item.get("status") == "evaluation-failed"}
    missing = [(arm, case.identifier, repeat) for arm in scored for case in holdout
               for repeat in range(repeats) if repeat not in arms[arm].get(case.identifier, {})]
    coverage = "pass" if not missing else "unknown" if all(slot in failed for slot in missing) else "fail"
    gate = mapping(record.get("gate", {}))
    if same and ("baseline_scores" not in gate or "baseline_skipped" in gate):
        coverage = "unknown"
    calls, budget = record.get("calls"), mapping(record.get("budget", {}))
    approved = budget.get("maximum")
    checks = {
        "preflight": "pass" if isinstance(record.get("preflight"), dict) else "fail",
        "holdout-isolated": "fail" if any(item.get("arm") == "search" and item.get("split") == "holdout"
                                          for item in outcomes) else "pass",
        "holdout-coverage": coverage,
        "calls-within-approval": ("unknown" if type(calls) is not int or type(approved) is not int
                                  else "pass" if calls <= approved else "fail"),
        "calls-within-bound": ("unknown" if bound is None or type(calls) is not int
                               else "pass" if calls <= bound else "fail"),
    }
    estimate = mapping(record.get("estimate", {}))
    return {"status": _status(checks), "checks": checks,
            "calls": {"charged": calls, "approved": approved, "estimate": estimate.get("calls"), "upper_bound": bound}}


def _export_claim(record: dict[str, object]) -> dict[str, object]:
    """Map the replay reasons to the export status: a refuting reason refutes, any other reason is inconclusive."""
    replay = record.get("replay")
    if not isinstance(replay, dict):
        return {"status": "inconclusive", "reasons": ["replay-missing"], "patch_digest": None,
                "replay": {"cases": 0, "mean_delta": None, "se": None, "per_case": {}}}
    found = cast(dict[str, object], replay)
    reasons = cast(list[str], found.get("reasons", []))
    status = ("refuted" if any(reason in REPLAY_REFUTED for reason in reasons)
              else "inconclusive" if reasons else "supported")
    return {"status": status, "reasons": reasons, "patch_digest": found.get("patch_digest"),
            "replay": {key: found.get(key) for key in ("cases", "mean_delta", "se", "per_case")}}


def _families(record: dict[str, object], arms: Mapping[str, Mapping[str, Mapping[int, float]]],
              holdout: Sequence[Case]) -> dict[str, object]:
    """Return the holdout case count and the mean paired delta of each family. A paired delta is winner mean minus baseline mean."""
    paired = holdout_deltas(by_repeat_order(arms["baseline"]), by_repeat_order(arms["winner"])).deltas
    tie = record.get("winner_hash") == record.get("seed_hash")
    deltas: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    for case in holdout:
        counts[case.family] = counts.get(case.family, 0) + 1
        _ = deltas.setdefault(case.family, [])
        if tie:
            deltas[case.family].append(0.0)
        elif case.identifier in paired:
            deltas[case.family].append(paired[case.identifier])
    return {family: {"cases": counts[family],
                     "mean_delta": math.fsum(found) / len(found) if found else None}
            for family, found in sorted(deltas.items())}


def _improvement_claim(record: dict[str, object], arms: Mapping[str, Mapping[str, Mapping[int, float]]],
                       holdout: Sequence[Case]) -> dict[str, object]:
    gate = mapping(record.get("gate", {}))
    verdict = gate.get("verdict")
    reasons = [str(reason) for reason in cast(list[object], gate.get("reasons") or [])]
    status = {"promote": "supported", "reject": "refuted"}.get(str(verdict), "inconclusive")
    if verdict == "promote-cheaper":
        reasons.append("cheaper-not-better")
    return {"status": status, "verdict": verdict, "reasons": reasons, "delta": gate.get("delta"), "se": gate.get("se"),
            "cases": gate.get("cases"), "repeats": record.get("repeats"), "families": _families(record, arms, holdout)}


def claims(record: dict[str, object], cases: Sequence[Case], bound: int | None) -> dict[str, object]:
    """Return the `claims` block of a finished run, derived from its record and its frozen cases.

    `bound` is the planned worst-case call count, or None when it is unknown.
    `apply` is True only when all three claims hold. The improvement claim holds only for the verdict `promote`.
    """
    holdout = [case for case in cases if case.eligible and case.split == "holdout"]
    outcomes = record_outcomes(record)
    arms = {arm: holdout_scores(outcomes, arm) for arm in ARMS}
    runner = _runner_claim(record, outcomes, arms, holdout, bound)
    export, improvement = _export_claim(record), _improvement_claim(record, arms, holdout)
    supported = all(part["status"] == "supported" for part in (runner, export, improvement))
    return {"runner": runner, "export": export, "improvement": improvement, "apply": supported}

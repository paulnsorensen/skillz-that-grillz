"""The `cases-too-easy` stop and its baseline report: what the seed scored, what the run spent, and what never ran.

The report answers a diagnosis from the headroom outcomes alone. It makes no model call, and it claims no search,
no holdout result, no promotion, and no export.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from skillz_experiments._cases import MET_LEVEL, mapping
from skillz_experiments._records import read
from skillz_experiments._redact import redact
from skillz_experiments.gate import close
from skillz_experiments.run._outcomes import is_failed
from skillz_experiments.run._record_fields import record_outcomes
from skillz_experiments.run._stop import Stop

# The phases that never ran when the headroom pass stops the run. Export needs the phase `complete`.
NOT_RUN = {"search": "not-run", "holdout_gate": "not-run", "replay": "not-run", "export": "unavailable"}

NEXT = ("report the baseline to the user: its failing criteria, its coverage, and its spent calls; no search, holdout "
        + "gate, or export ran, so do not call export and claim no improvement; to fix a failing criterion, edit the "
        + "skill; to optimize, draft harder cases in a new run directory and ask the case question again")


def too_easy_message(headroom: Mapping[str, object]) -> str:
    """Return the `cases-too-easy` stop text from a recorded headroom block.

    The text comes from the block alone, so a resume gets the same stop when the run died before it recorded `failure`.
    """
    ceiling = cast(float, headroom["ceiling"])
    families = [(name, mapping(item)) for name, item in mapping(headroom["families"]).items()]
    named = "; ".join(f"{name} ({item['split']}) mean {cast(float, item['mean']):.2f}" for name, item in families
                      if cast(float, item["mean"]) >= ceiling or close(cast(float, item["mean"]), ceiling))
    return (f"the seed already scores at least {ceiling} on {named}; a search cannot show a gain on cases this easy; "
            + "read `baseline` for the failing criteria; to optimize, draft harder cases in a new run directory, "
            + "then call run again")


def _unmet(item: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """Return the level and the redacted reason of each criterion that the judged outcome `item` does not meet."""
    criteria = item.get("criteria")
    verdicts = cast(dict[str, object], criteria) if isinstance(criteria, dict) else {}
    unmet: dict[str, dict[str, object]] = {}
    for name, verdict in verdicts.items():
        given = mapping(cast(object, verdict)) if isinstance(verdict, dict) else {}
        level, met = given.get("level"), given.get("met")
        if met is False or (not isinstance(met, bool) and type(level) is int and level < MET_LEVEL):
            unmet[name] = {"level": level, "reason": redact(str(given.get("reason", "")))}
    return unmet


def _frozen_cases(out: Path) -> tuple[dict[str, str], Counter[str], int] | None:
    """Return the family of each scored case, the scored case count per split, and the pending case count.

    A `cases.json` that does not load returns None, so the report shows unknown coverage instead of a guess.
    """
    try:
        document = read(out / "cases.json")
        cases = [mapping(item) for item in cast(list[object], document["cases"])]
        pending = len(cast(list[object], document.get("pending", [])))
        return ({str(case["id"]): str(case.get("family")) for case in cases},
                Counter(str(case["split"]) for case in cases), pending)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _failing(seed: list[dict[str, object]], families: Mapping[str, str]) -> list[dict[str, object]]:
    """Return each seed outcome that scores below 1 or fails, with its unmet criteria, in record order."""
    failing: list[dict[str, object]] = []
    for item in seed:
        score, failed = item.get("score"), is_failed(item)
        if failed or type(score) not in (int, float) or cast(float, score) < 1:
            failing.append({"case_id": item.get("case_id"), "split": item.get("split"),
                            "family": families.get(str(item.get("case_id"))), "repeat": item.get("repeat"),
                            "score": None if failed else score, "status": item.get("status"), "criteria": _unmet(item)})
    return failing


def baseline_report(record: dict[str, object], out: Path) -> dict[str, object]:
    """Return the baseline of a run that the headroom pass stopped.

    Before the search, every train and validation outcome is a seed outcome. `failing` lists each one that scores
    below 1 or fails, so a failing family shows while another family saturates. `spent` holds the recorded counts.
    `approved_calls` and `estimate` are the approved ceiling and the plan, not spending.
    """
    seed = [item for item in record_outcomes(record) if item.get("split") in ("train", "validation")]
    frozen = _frozen_cases(out)
    estimate = mapping(record["estimate"]) if isinstance(record.get("estimate"), dict) else {}
    budget = mapping(record["budget"]) if isinstance(record.get("budget"), dict) else {}
    unscored: dict[str, object] = ({"holdout_cases": frozen[1]["holdout"], "pending_cases": frozen[2]}
                                   if frozen is not None else {"holdout_cases": None, "pending_cases": None})
    return NOT_RUN | {
        "headroom": record.get("headroom"), "failing": _failing(seed, frozen[0] if frozen is not None else {}),
        "scored": {"outcomes": len(seed), "failed": sum(is_failed(item) for item in seed),
                   "cases": len({str(item.get("case_id")) for item in seed})},
        "unscored": unscored,
        "spent": {key: record.get(key) for key in ("calls", "retries", "calls_by_role", "seconds_by_role")},
        "approved_calls": budget.get("maximum"), "estimate": {key: estimate.get(key) for key in ("calls", "seconds")}}


def too_easy_stop(record: dict[str, object], out: Path) -> Stop:
    """Return the `cases-too-easy` stop of the recorded headroom block, with the baseline report as its data."""
    headroom = mapping(record["headroom"])
    return Stop("cases-too-easy", too_easy_message(headroom), {"next": NEXT, "baseline": baseline_report(record, out)})

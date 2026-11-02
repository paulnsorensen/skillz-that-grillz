"""The `cases-too-easy` stop and its baseline report: what the seed scored, what the run spent, and what never ran.

The report answers a diagnosis from the headroom outcomes alone. It makes no model call, and it claims no search,
no holdout result, no promotion, and no export.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from skillz_experiments._cases import MET_LEVEL, digest, load_manifest, mapping
from skillz_experiments._redact import redact
from skillz_experiments.gate import close
from skillz_experiments.run._outcomes import FIELD_LIMIT, TRUNCATED, is_failed
from skillz_experiments.run._record_fields import criterion_levels, record_outcomes
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


def _cut(text: str) -> str:
    """Redact `text`, then cut it to `FIELD_LIMIT` characters with the `[truncated]` marker, as the proposer feedback does."""
    clean = redact(text)
    return clean if len(clean) <= FIELD_LIMIT else clean[:FIELD_LIMIT - len(TRUNCATED)] + TRUNCATED


def _unmet(item: dict[str, object]) -> dict[str, dict[str, object]]:
    """Return the level and the cut reason of each criterion below `MET_LEVEL`, by the rule that export uses."""
    levels = criterion_levels(item)
    criteria = cast(dict[str, dict[str, object]], item.get("criteria"))
    return {name: {"level": level, "reason": _cut(str(criteria[name].get("reason", "")))}
            for name, level in levels.items() if level < MET_LEVEL}


def _seed_outcomes(outcomes: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return one seed outcome per (split, case, repeat): the newest scored one, else the newest failed one.

    Before the search, each train and validation search outcome is a seed outcome. A resume of the headroom pass scores
    a run again when its earlier outcome failed, so a later score replaces that stale failure.
    """
    chosen: dict[tuple[object, object, object], dict[str, object]] = {}
    for item in outcomes:
        if item.get("arm") != "search" or item.get("split") not in ("train", "validation"):
            continue
        key = (item.get("split"), item.get("case_id"), item.get("repeat"))
        if key not in chosen or is_failed(chosen[key]) or not is_failed(item):
            chosen[key] = item
    return list(chosen.values())


def _frozen_cases(record: dict[str, object], out: Path) -> tuple[dict[str, str | None], Counter[str], int] | None:
    """Return the family of each scored case, the scored case count per split, and the pending case count.

    A `cases.json` that does not load, or that differs from the frozen `dataset_hash`, returns None. The report then
    shows unknown coverage instead of a guess.
    """
    try:
        document = load_manifest(out / "cases.json")
        if digest(document) != record.get("dataset_hash"):
            return None
        cases = [mapping(item) for item in cast(list[object], document["cases"])]
        pending = len(cast(list[object], document.get("pending", [])))
        return ({str(case["id"]): None if case.get("family") is None else str(case["family"]) for case in cases},
                Counter(str(case["split"]) for case in cases), pending)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _failing(seed: list[dict[str, object]], families: Mapping[str, str | None]) -> list[dict[str, object]]:
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

    `failing` lists each seed outcome that scores below 1 or fails, so a failing family shows while another family
    saturates. `scored.cases` counts the cases with a scored outcome. A record whose `outcomes` do not load gives
    `failing` and `scored` as None. `spent` holds the recorded counts, preflight calls included. `approved_calls` and
    `estimate` are the approved ceiling and the plan, not spending.
    """
    try:
        seed: list[dict[str, object]] | None = _seed_outcomes(record_outcomes(record))
    except ValueError:
        seed = None
    frozen = _frozen_cases(record, out)
    estimate = mapping(record["estimate"]) if isinstance(record.get("estimate"), dict) else {}
    budget = mapping(record["budget"]) if isinstance(record.get("budget"), dict) else {}
    unscored: dict[str, object] = ({"holdout_cases": frozen[1]["holdout"], "pending_cases": frozen[2]}
                                   if frozen is not None else {"holdout_cases": None, "pending_cases": None})
    scored = None if seed is None else {
        "outcomes": len(seed), "failed": sum(is_failed(item) for item in seed),
        "cases": len({str(item.get("case_id")) for item in seed if not is_failed(item)})}
    return NOT_RUN | {
        "headroom": record.get("headroom"),
        "failing": None if seed is None else _failing(seed, frozen[0] if frozen is not None else {}),
        "scored": scored, "unscored": unscored,
        "spent": {key: record.get(key) for key in ("calls", "retries", "calls_by_role", "seconds_by_role")},
        "approved_calls": budget.get("maximum"), "estimate": {key: estimate.get(key) for key in ("calls", "seconds")}}


def too_easy_stop(record: dict[str, object], out: Path) -> Stop:
    """Return the `cases-too-easy` stop of the recorded headroom block, with the baseline report as its data."""
    headroom = mapping(record["headroom"])
    return Stop("cases-too-easy", too_easy_message(headroom), {"next": NEXT, "baseline": baseline_report(record, out)})

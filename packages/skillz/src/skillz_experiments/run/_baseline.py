"""The `cases-too-easy` stop and the baseline state of a run record.

The stop carries the report that the seed measurement wrote. It makes no model call, and it claims no search,
no holdout result, no promotion, and no export.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from skillz_experiments._cases import MET_LEVEL, mapping
from skillz_experiments._redact import redact
from skillz_experiments._records import read
from skillz_experiments.gate import close
from skillz_experiments.run._outcomes import FIELD_LIMIT, TRUNCATED
from skillz_experiments.run._record_fields import criterion_levels
from skillz_experiments.run._stop import Stop

# The phases that never ran when the headroom pass stops the run. Export needs the phase `complete`.
NOT_RUN = {"search": "not-run", "holdout_gate": "not-run", "replay": "not-run", "export": "unavailable"}
REPORT_NAME = "report.json"


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


def unmet_criteria(item: dict[str, object]) -> dict[str, dict[str, object]]:
    """Return the level and the cut reason of each criterion below `MET_LEVEL`, by the rule that export uses."""
    levels = criterion_levels(item)
    criteria = cast(dict[str, dict[str, object]], item.get("criteria"))
    return {name: {"level": level, "reason": _cut(str(criteria[name].get("reason", "")))}
            for name, level in levels.items() if level < MET_LEVEL}


def baseline_finished(record: dict[str, object]) -> bool:
    """Return True when the baseline is measured and the run is not approved for search.

    `plan_mode` stays the stored state. The `headroom` block marks the end of the measurement.
    """
    return record.get("plan_mode") == "baseline" and isinstance(record.get("headroom"), dict)


def too_easy_stop(record: dict[str, object], out: Path) -> Stop:
    """Return the `cases-too-easy` stop of the recorded headroom block, with the written baseline report as its data."""
    return Stop("cases-too-easy", too_easy_message(mapping(record["headroom"])),
                {"next": "read the baseline report; to optimize, draft harder cases in a new run directory",
                 "baseline": read(out / REPORT_NAME)})

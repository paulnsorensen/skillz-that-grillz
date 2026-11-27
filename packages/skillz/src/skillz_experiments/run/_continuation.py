"""Check a finished baseline and approve the remaining search work."""
from __future__ import annotations

import time
from pathlib import Path
from typing import cast

from skillz_experiments._budget import BudgetUnapproved, approve
from skillz_experiments._candidate import candidate_files
from skillz_experiments._cases import CodedError, mapping
from skillz_experiments._records import write
from skillz_experiments.harness import ClaudeOptions, Configuration, stop_kind
from skillz_experiments.run._edit_plan import ReflectionNotes, editable_chars
from skillz_experiments.run._open import freeze_compatibility, frozen_inputs, verify_budget
from skillz_experiments.run._record_fields import field, int_field, text_field
from skillz_experiments.run._resume import refuse_too_easy
from skillz_experiments.run._signals import warn
from skillz_experiments.run._plan import preflight_calls
from skillz_experiments.run._sizing import Sizer, budget_data, raised_cap
from skillz_experiments.run._state import RunState
from skillz_experiments.search import Edit


def approve_search(record: dict[str, object], out: Path, approval: int | None, configuration: Configuration | None,
                   repeats: int | None = None) -> None:
    """Apply headroom before sizing search. Freeze the separate search approval and both start times.

    A baseline run that took no `--repeats` accepts `repeats` here, and the search approval freezes it.
    """
    headroom = record.get("headroom")
    if isinstance(headroom, dict):
        warnings = cast(dict[str, object], headroom).get("warnings")
        if isinstance(warnings, list):
            warn([item for item in cast(list[object], warnings) if isinstance(item, str)])
    refuse_too_easy(record, out)
    contract, imported = frozen_inputs(record, out, configuration)
    cases = [case for case in imported if case.eligible]
    options = (configuration.sizing_options() if configuration is not None
               else ClaudeOptions.parse(mapping(record.get("claude_options", {}))))
    _ = verify_budget(record, cases, contract, options, mapping(field(record, "budget")))
    given = record.get("repeats_given") is True or repeats is not None
    chosen = int_field(record, "repeats") if repeats is None else repeats
    chars = editable_chars(candidate_files(field(record, "seed")), cast(list[str], field(record, "editable")))
    notes = ReflectionNotes.for_edit(chars, cast(Edit, text_field(record, "edit")),
                                    offer_prose=record.get("offer_prose") is True)
    sizer = Sizer(contract, cases, options, raised_cap(approval, cases), given, sum(case.split == "holdout" for case in cases),
                  notes, {"statistics": record["statistics"]}, preflight_calls(record.get("harness")))
    plan = sizer.plan(chosen)
    shown = sizer.estimate(plan, chosen)
    try:
        budget = approve(plan.sized, approval, reserve=plan.reserve, reserve_time=plan.reserve_seconds)
    except BudgetUnapproved as error:
        raise sizer.stop(str(error), {"estimate": shown, "approve": "call run --search with --approve-budget CALLS",
                                      **notes.warnings}, chosen) from None
    if chosen != int_field(record, "repeats"):
        _ = record.pop("compatibility", None)
    record.update({"plan_mode": "search", "estimate": shown, "repeats": chosen, "repeats_given": given,
                   "budget": budget_data(budget) | {"prior_calls": int_field(record, "calls")},
                   "started": time.time(), "started_monotonic": time.monotonic()})
    freeze_compatibility(record, cases, contract, configuration)
    write(out / "run.json", record)


def close_provider(session: RunState, pending: BaseException | None) -> None:
    """Close the provider and checkpoint any close fault.

    With no pending exception, the run keeps its result and records the fault as `close_warning`. When the run
    itself raises another exception, that exception stays, the close fault goes to `close_failure` and a note, and a
    terminal close code also becomes the run's `failure_code`.
    """
    try:
        session.provider.close()
    except CodedError as error:
        detail = {"code": error.code, "message": str(error)}
        with session.lock:
            terminal = stop_kind(error.code) == "terminal" and stop_kind(session.record.get("failure_code")) != "terminal"
            if pending is not None:
                session.record["close_failure"] = detail
                if terminal:
                    session.record["failure_code"] = error.code
                    _ = session.record.pop("preflight", None)
                session.checkpoint()
            else:
                session.record["close_warning"] = detail
                session.checkpoint()
        if pending is not None:
            pending.add_note(f"closing the provider also failed with {error.code}: {error}")

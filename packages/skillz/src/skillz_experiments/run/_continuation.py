"""Check a finished baseline and approve the remaining search work."""
from __future__ import annotations

import time
from pathlib import Path
from typing import cast

from skillz_experiments._budget import BudgetUnapproved, approve
from skillz_experiments._candidate import candidate_files
from skillz_experiments._cases import CodedError, digest, load_cases, mapping
from skillz_experiments._contract import parse
from skillz_experiments._records import read, write
from skillz_experiments.harness import ClaudeOptions, Configuration, stop_kind
from skillz_experiments.run._edit_plan import ReflectionNotes, editable_chars
from skillz_experiments.run._open import verify_budget
from skillz_experiments.run._record_fields import field, int_field, text_field
from skillz_experiments.run._resume import refuse_too_easy
from skillz_experiments.run._signals import warn
from skillz_experiments.run._sizing import Sizer, budget_data, raised_cap
from skillz_experiments.run._state import RunState
from skillz_experiments.search import Edit


def approve_search(record: dict[str, object], out: Path, approval: int | None, configuration: Configuration | None) -> None:
    """Apply headroom before sizing search. Freeze the separate search approval and both start times."""
    headroom = record.get("headroom")
    if isinstance(headroom, dict):
        warnings = cast(dict[str, object], headroom).get("warnings")
        if isinstance(warnings, list):
            warn([item for item in cast(list[object], warnings) if isinstance(item, str)])
    refuse_too_easy(record, out)
    if configuration is not None and configuration.identity() != record.get("harness"):
        raise CodedError("harness-changed", "harness configuration differs from the frozen record; create a new run")
    contract = parse(field(record, "contract"), text_field(record, "contract_source"))
    if contract.identity != text_field(record, "contract_hash"):
        raise ValueError("contract differs from the frozen record; create a new run")
    path = out / "cases.json"
    if digest(read(path)) != text_field(record, "dataset_hash"):
        raise ValueError("dataset differs from the frozen record")
    cases = [case for case in load_cases(path, contract.grader_types()) if case.eligible]
    options = (configuration.sizing_options() if configuration is not None
               else ClaudeOptions.parse(mapping(record.get("claude_options", {}))))
    _ = verify_budget(record, cases, contract, options, mapping(field(record, "budget")))
    repeats = int_field(record, "repeats")
    chars = editable_chars(candidate_files(field(record, "seed")), cast(list[str], field(record, "editable")))
    notes = ReflectionNotes.for_edit(chars, cast(Edit, text_field(record, "edit")),
                                    offer_prose=record.get("offer_prose") is True)
    sizer = Sizer(contract, cases, options, raised_cap(approval, cases), record.get("repeats_given") is True,
                  sum(case.split == "holdout" for case in cases), notes, {"statistics": record["statistics"]})
    plan = sizer.plan(repeats)
    shown = sizer.estimate(plan, repeats)
    try:
        budget = approve(plan.sized, approval, reserve=plan.reserve, reserve_time=plan.reserve_seconds)
    except BudgetUnapproved as error:
        raise sizer.stop(str(error), {"estimate": shown, "approve": "call run --search with --approve-budget CALLS",
                                      **notes.warnings}, repeats) from None
    record.update({"plan_mode": "search", "estimate": shown,
                   "budget": budget_data(budget) | {"prior_calls": int_field(record, "calls")},
                   "started": time.time(), "started_monotonic": time.monotonic()})
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

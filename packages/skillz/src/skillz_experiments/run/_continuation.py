"""Check a finished baseline, pick the search objective, and approve the remaining search work."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Literal, cast, get_args

from skillz_experiments._budget import BudgetUnapproved, approve
from skillz_experiments._candidate import candidate_files
from skillz_experiments._cases import CodedError, mapping
from skillz_experiments._records import write
from skillz_experiments.gate import Statistics
from skillz_experiments.harness import ClaudeOptions, Configuration, stop_kind
from skillz_experiments.run._edit_plan import ReflectionNotes, editable_chars
from skillz_experiments.run._open import freeze_compatibility, frozen_inputs, verify_budget
from skillz_experiments.run._record_fields import field, int_field, text_field
from skillz_experiments.run._resume import frozen_statistics, refuse_too_easy
from skillz_experiments.run._signals import warn
from skillz_experiments.run._plan import preflight_calls
from skillz_experiments.run._sizing import Sizer, budget_data, raised_cap
from skillz_experiments.run._state import RunState
from skillz_experiments.search import Edit

Objective = Literal["quality", "tokens"]
"""`quality` searches for a strict gain. `tokens` also accepts a cheaper tie, for `promote-cheaper` (gh-757)."""

PROSE_TOKENS_WARNING = ("the token objective with the prose edit mode can edit Markdown only, and the task input "
                        + "tokens dwarf that text; to move steps into a helper CLI, start a new run directory with "
                        + "--edit prose+cli")


def search_objective(record: dict[str, object], given: str | None, search: bool,
                     statistics: Statistics | None = None) -> Objective:
    """Return the objective of this call. Each refusal comes before any model call.

    A record whose `plan_mode` is `search` keeps its recorded objective, and another `given` value refuses the resume.
    Else `given` applies only with `search`, and an omitted value means `quality`. `tokens` needs `min_token_saving`
    in `statistics`, or else in the frozen statistics of `record`, which only that case reads.
    """
    if given is not None and given not in get_args(Objective):
        raise ValueError("--objective must be quality or tokens")
    if record.get("plan_mode") == "search":
        recorded = cast(Objective, record.get("objective"))
        if given is not None and given != recorded:
            raise CodedError("run-config-differs", f"--objective {given} differs from the recorded objective {recorded}; "
                             + "resume with the recorded value or create a new run")
        return recorded
    if given is not None and not search:
        raise ValueError("--objective applies only to a --search call; add --search or omit --objective")
    if given == "tokens" and (statistics or frozen_statistics(record)).min_token_saving is None:
        raise ValueError("--objective tokens needs min_token_saving: set statistics.min_token_saving in the contract "
                         + "or pass --min-token-saving in a new run directory, or use --objective quality")
    return cast(Objective, given or "quality")


def approve_search(record: dict[str, object], out: Path, approval: int | None, configuration: Configuration | None,
                   repeats: int | None = None, objective: Objective = "quality") -> None:
    """Apply headroom, the candidate floor, then the search budget. Freeze the approval, the objective, and the start times.

    The token objective skips the `cases-too-easy` stop, because a cheaper tie needs no headroom.
    A baseline run that took no `--repeats` accepts `repeats` here, and the search approval freezes it.
    """
    headroom = record.get("headroom")
    if isinstance(headroom, dict):
        warnings = cast(dict[str, object], headroom).get("warnings")
        if isinstance(warnings, list):
            warn([item for item in cast(list[object], warnings) if isinstance(item, str)])
    refuse_too_easy(record, out, tokens=objective == "tokens")
    contract, imported = frozen_inputs(record, out, configuration)
    cases = [case for case in imported if case.eligible]
    options = (configuration.sizing_options() if configuration is not None
               else ClaudeOptions.parse(mapping(record.get("claude_options", {}))))
    _ = verify_budget(record, cases, contract, options, mapping(field(record, "budget")))
    given = record.get("repeats_given") is True or repeats is not None
    chosen = int_field(record, "repeats") if repeats is None else repeats
    chars = editable_chars(candidate_files(field(record, "seed")), cast(list[str], field(record, "editable")))
    edit = cast(Edit, text_field(record, "edit"))
    notes = ReflectionNotes.for_edit(chars, edit, offer_prose=record.get("offer_prose") is True)
    sizer = Sizer(contract, cases, options, raised_cap(approval, cases), given, sum(case.split == "holdout" for case in cases),
                  notes, {"statistics": record["statistics"], "objective": objective}, preflight_calls(record.get("harness")))
    plan = sizer.plan(chosen)
    sizer.refuse_underpowered(plan, chosen)
    shown = sizer.estimate(plan, chosen)
    try:
        budget = approve(plan.sized, approval, reserve=plan.reserve, reserve_time=plan.reserve_seconds)
    except BudgetUnapproved as error:
        prose = {"objective_warning": PROSE_TOKENS_WARNING} if objective == "tokens" and edit == "prose" else {}
        raise sizer.stop(str(error), {"estimate": shown, "approve": "call run --search with --approve-budget CALLS",
                                      **notes.warnings, **prose}, chosen) from None
    if chosen != int_field(record, "repeats"):
        _ = record.pop("compatibility", None)
    record.update({"plan_mode": "search", "objective": objective, "estimate": shown, "repeats": chosen,
                   "repeats_given": given, "budget": budget_data(budget) | {"prior_calls": int_field(record, "calls")},
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

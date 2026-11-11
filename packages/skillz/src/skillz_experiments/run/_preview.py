"""Free edit and cost preview without approval or run state."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from skillz_experiments._budget import ceilings
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._contract import Contract
from skillz_experiments._intake import IntakeCase
from skillz_experiments.harness import ClaudeOptions
from skillz_experiments.run._edit_plan import ReflectionNotes, plan_edit
from skillz_experiments.run._run import DEFAULT_REPEATS
from skillz_experiments.run._sizing import Sizer, raised_cap
from skillz_experiments.run._stop import Stop
from skillz_experiments.search import Edit


def _eligible(cases: Sequence[IntakeCase]) -> list[Case]:
    projected = [Case(case.identifier, case.family, case.split, case.request, case.files, case.expected,
                      case.source, True, kind=case.kind, journey=case.journey, regression=case.regression)
                 for case in cases if case.scored]
    return [case for case in projected if case.eligible]


def plan(target: Path, contract: Contract, cases: Sequence[IntakeCase] | None, *, edit: Edit | None = None,
         repeats: int | None = None, planning_call_cap: int | None = None,
         options: ClaudeOptions | None = None) -> dict[str, object]:
    """Return free planning facts. The app owns diagnostics and draft validation."""
    warnings: list[str] = []
    options = ClaudeOptions() if options is None else options
    scored = _eligible(cases) if cases is not None else []
    cap = 0
    cap_error: CodedError | None = None
    try:
        cap = raised_cap(planning_call_cap, scored, flag="--planning-call-cap")
        ceiling_calls, ceiling_seconds = ceilings(any(case.journey is not None for case in scored), cap)
    except CodedError as error:
        cap_error = error
        ceiling_calls, ceiling_seconds = None, None
    inputs: dict[str, object] = {"repeats": DEFAULT_REPEATS if repeats is None else repeats,
                                 "task_timeout": options.sandbox_seconds, "requested_cap": planning_call_cap,
                                 "effective_ceiling_calls": ceiling_calls, "effective_ceiling_seconds": ceiling_seconds}
    preview: dict[str, object] = {
        "edit": {"requested": edit, "resolved": None, "fallback_reason": None},
        "editable_files": None, "estimate": None, "inputs": inputs,
        "unavailable": {}, "warnings": warnings,
        "not_checked": ["model selection", "executable availability", "resolved command boundaries", "host readiness",
                        "provider behavior", "approval", "helper builds"]}
    try:
        free = plan_edit(target, contract, edit, warnings)
    except (CodedError, ValueError, OSError) as error:
        code = error.code if isinstance(error, CodedError) else "invalid"
        preview["unavailable"] = {"edit": code, "editable_files": code, "estimate": code}
        preview["_failure"] = {"code": code, "message": str(error), "file": "wedge.toml"}
        return preview
    notes = ReflectionNotes.size(free.seed, free.mode, offer_prose=edit is not None, emit=False)
    preview["edit"] = {"requested": edit, "resolved": free.mode,
                       "fallback_reason": warnings[0] if warnings else None}
    preview["editable_files"] = sorted(free.seed.editable)
    preview["warnings"] = warnings + list(notes.warnings.values())
    if repeats is not None and repeats < 1:
        preview["unavailable"] = {"estimate": "invalid-repeats"}
        preview["_failure"] = {"code": "invalid-repeats", "message": "repeats must be at least 1"}
        return preview
    if cap_error is not None:
        preview["unavailable"] = {"estimate": cap_error.code}
        preview["_failure"] = {"code": cap_error.code, "message": str(cap_error)}
        return preview
    if cases is None:
        preview["unavailable"] = {"estimate": "valid-draft-required"}
        return preview
    count = sum(case.split == "holdout" for case in scored)
    sizer = Sizer(contract, scored, options, cap, repeats is not None, count, notes, {})
    try:
        sized = sizer.plan(DEFAULT_REPEATS if repeats is None else repeats)
    except Stop as error:
        preview["unavailable"] = {"estimate": error.code}
        preview["alternatives"] = {key: value for key, value in error.data.items()
                                   if key in {"smallest_approval", "estimate", "repeats_table"}}
        preview["warnings"] = warnings + list(notes.warnings.values())
        return preview
    preview["estimate"] = sizer.estimate(sized, DEFAULT_REPEATS if repeats is None else repeats)
    preview["warnings"] = warnings + list(notes.warnings.values())
    return preview

"""One resumable autoimprove run: intake, one search, one holdout gate, one replay of the exported patch.

Phases: prepared, searched, gated, replayed, complete. `run.json` (schema 4) holds the checkpoint of each phase.
"""
from __future__ import annotations

import fcntl
import sys
import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import cast, get_args

from skillz_experiments._budget import (
    BudgetUnapproved,
    approve,
)
from skillz_experiments._candidate import NEW_CLI, Candidate, store_frozen
from skillz_experiments._cases import CodedError, Split, digest, load_cases
from skillz_experiments._contract import Contract
from skillz_experiments._intake import (
    DRAFT_NAME,
    approval_question,
    case_hash,
    check_helper_input,
    freeze,
    intake_contract,
    load_draft,
    skill_facts,
    split_cases,
    too_few,
)
from skillz_experiments._records import SCHEMA_VERSION, open_record, read, write
from skillz_experiments._seed import SEED_NAME, check_coverage, seed_cases, seed_inferred, seed_projection, seed_summary
from skillz_experiments.run._wedge_targets import TargetPlan, plan_targets, seal, settle
from skillz_experiments.gate import Statistics
from skillz_experiments.harness import (
    ClaudeOptions,
    Configuration,
    EnvironmentDiffers,
    check_selection,
    has_claude_sandbox,
    host_report,
    refuses_claude_options,
    stop_kind,
)
from skillz_experiments.run._export import summary
from skillz_experiments.run._history_store import Loaded
from skillz_experiments.run._history_store import load as load_history
from skillz_experiments.run._history_store import store_path
from skillz_experiments.run._history_store import write as write_history
from skillz_experiments.run._plan import editable_files, plan_estimate, scored_kinds, size_plan
from skillz_experiments.run._resume import (
    check_resume,
    check_statistics,
    frozen_statistics,
    open_lock,
    refuse_nono,
    refuse_terminated,
    refuse_too_easy,
    require_private,
    statistics_flag,
    resolve_statistics,
)
from skillz_experiments.run._open import engine_hash
from skillz_experiments.run._record_fields import text_field
from skillz_experiments.run._replay import is_inside
from skillz_experiments.run._session import Session
from skillz_experiments.run._state import Factory, HarnessName
from skillz_experiments.run._stop import Stop
from skillz_experiments.search import Edit

DEFAULT_SEED = 20261006


DEFAULT_REPEATS = 3


def _fall_back_to_prose(target: Path, skeleton: Candidate, contract: Contract,
                        error: CodedError) -> tuple[TargetPlan, Candidate]:
    """Plan an omitted `--edit` as `prose` after `prose+cli` found nothing to edit, and say why on stderr."""
    print(f"skillz-experiment: {error}; this run uses --edit prose", file=sys.stderr)
    wedge = plan_targets(target, skeleton.files, contract, "prose")
    files = skeleton.files | wedge.sources
    frozen = skeleton.frozen | wedge.frozen
    return wedge, Candidate(files, tuple(editable_files(files, contract, "prose", frozen.keys(), wedge)), contract, frozen=frozen)


def _prepare(target: Path, out: Path, model: str, adapter: HarnessName, edit: Edit | None, repeats: int, seed: int | None,
             approve_cases: str | None, approve_budget: int | None, *, resolve_harness: bool = False,
             options: ClaudeOptions, configuration: Configuration | None = None,
             statistics_given: Mapping[str, float] | None = None, judge_model: str | None,
             history: bool = True, requested_kinds: Sequence[str] = ()) -> Statistics:
    """Run the two approvals and write the prepared record. No model call happens here.

    The case approval covers the resolved statistics, so a changed or dropped flag stops at the case approval.

    A fresh run with a built-in harness first runs the free host checks, so a host problem stops the run
    before the user drafts or approves any case.
    """
    draft = out / DRAFT_NAME
    if not draft.is_file():
        hosts = [(adapter, None)] if resolve_harness else (configuration.host_selections() if configuration is not None else [])
        report = host_report(hosts) if hosts else None
        if report is not None and not report["ok"]:
            raise Stop("host-not-ready", "fix each failed host check, then call run again; no model call ran", report)
        data: dict[str, object] = {"draft": str(draft), "facts": skill_facts(target)}
        seed_file = out / SEED_NAME
        # Remove an old seed first, so a skill that lost or broke its evals.json leaves no stale seed file.
        seed_file.unlink(missing_ok=True)
        seeded = seed_cases(target)
        if seeded is not None:
            write(seed_file, seeded)
            split: dict[str, object] | None = None
            with suppress(CodedError, ValueError):
                planned = intake_contract(target)
                split = seed_projection(seeded, scored_kinds(planned), planned.journey_kinds())
            data |= {"seed_file": str(seed_file), "seed_summary": seed_summary(seeded, seed_inferred(target), split)}
        start = "" if seeded is None else f", starting from the seed cases in {seed_file}"
        raise Stop("cases-missing", f"write the case draft to {draft}{start}, then call run again",
                   data | ({"doctor": report} if report else {}))
    split_seed = DEFAULT_SEED if seed is None else seed
    contract = intake_contract(target)
    resolved = resolve_statistics(contract, statistics_given or {})
    kinds = scored_kinds(contract, requested_kinds)
    captures = configuration.judge_captures() if configuration is not None else has_claude_sandbox(adapter)
    for kind in kinds:
        if contract.kinds[kind].capture and not captures:
            raise CodedError("contract-capture-unsupported", f"the kind {kind} declares capture, and only "
                             + "the claude harness can show capture files to the judge; run with --harness claude")
    journeys = set(contract.journey_kinds())
    extra = [] if requested_kinds else [kind for kind in journeys if kind not in kinds]
    drafted = load_draft(draft, contract.journey_kinds())
    task_adapter = configuration.roles["task"].adapter if configuration is not None else adapter
    evaluated = {*kinds, *extra}
    if not has_claude_sandbox(task_adapter) and any(case.journey is not None and case.kind in evaluated for case in drafted):
        retry = "; or pass --kind task" if "task" in contract.kinds and "task" not in journeys else ""
        raise CodedError("journey-unsupported", "a journey case needs a task transport that can hold a conversation; "
                         + f"run with --harness claude{retry}")
    check_helper_input(contract, drafted)
    check_coverage(target, drafted)
    plain = [kind for kind in kinds if kind not in journeys]
    cases = split_cases(drafted, split_seed, plain[0] if plain else None, [kind for kind in kinds if kind in journeys], extra)
    unknown = sorted(set(resolved.family_budgets) - {case.family for case in cases})
    if unknown:
        raise CodedError("contract-family-unknown", f"the contract family_budgets names no draft family: {', '.join(unknown)}; "
                         + f"the draft families are {', '.join(sorted({case.family for case in cases}))}")
    shortfall = too_few(Counter(case.family for case in cases if case.scored and case.kind in kinds))
    if shortfall is not None:
        raise shortfall
    expected = case_hash(cases, split_seed, resolved.data())
    if resolve_harness or configuration is not None:
        chosen = configuration if configuration is not None else Configuration.split(adapter, model, judge_model, options)
        chosen.check_read_roots(out)
    if approve_cases != expected:
        raise Stop("cases-unapproved", "ask the user to approve the cases, then call run with --approve-cases HASH",
                   {"question": approval_question(cases, split_seed, contract, resolved.data()), "case_hash": expected,
                    "seed": split_seed, "statistics": resolved.data(),
                    **({} if configuration is not None else {"claude_options": options.data()})})
    freeze(out / "cases.json", cases, split_seed, expected, resolved.data())
    scored = [case for case in load_cases(out / "cases.json", contract.grader_types()) if case.eligible]
    count = {split: sum(case.split == split for case in scored) for split in cast(tuple[str, ...], get_args(Split))}
    skeleton = Candidate.capture(target, [], contract)
    mode: Edit = edit or "prose+cli"
    wedge = plan_targets(target, skeleton.files, contract, mode)
    files = skeleton.files | wedge.sources | ({NEW_CLI: ""} if wedge.capable is not None else {})
    frozen = skeleton.frozen | wedge.frozen
    try:
        seed_candidate = Candidate(files, tuple(editable_files(files, contract, mode, frozen.keys(), wedge)), contract, frozen=frozen)
    except CodedError as error:
        if edit is not None or error.code != "helper-missing":
            raise
        mode = "prose"
        wedge, seed_candidate = _fall_back_to_prose(target, skeleton, contract, error)
    plan = size_plan(contract, scored, repeats, (options if configuration is None else configuration.sizing_options()))
    shown = plan_estimate(plan, repeats, count["holdout"])
    shown_options = {} if configuration is not None else {"claude_options": options.data()}
    try:
        budget = approve(plan.sized, approve_budget, reserve=plan.reserve, reserve_time=plan.reserve_seconds)
    except BudgetUnapproved as error:
        raise Stop("budget-unapproved", str(error), {"estimate": shown, "approve": "call run with --approve-budget CALLS",
                                                     "statistics": resolved.data(), **shown_options}) from None
    offered = wedge.capable is not None
    wedge, layers = settle(wedge, out / "site")
    if offered and wedge.capable is None:
        files = skeleton.files | wedge.sources
        frozen = skeleton.frozen | wedge.frozen
        try:
            seed_candidate = Candidate(files, tuple(editable_files(files, contract, mode, frozen.keys(), wedge)), contract, frozen=frozen)
        except CodedError as error:
            if edit is not None or error.code != "helper-missing":
                raise
            mode, layers = "prose", {}
            wedge, seed_candidate = _fall_back_to_prose(target, skeleton, contract, error)
    seed_candidate, owned = seal(wedge, seed_candidate, target, layers)
    seed_frozen = store_frozen(seed_candidate, out / "frozen")
    write(out / "run.json", {
        "schema_version": SCHEMA_VERSION, "run_id": str(uuid.uuid4()), "history": history,
        "phase": "prepared", "calls": 0, "retries": 0, "calls_by_role": {},
        "seconds_by_role": {}, "model": model, "adapter": adapter,
        "judge_model_flag": judge_model,
        **({"claude_options": options.data()} if resolve_harness else {}),
        **({"harness": configuration.identity()} if configuration is not None else {}),
        "edit": mode, "repeats": repeats, "split_seed": split_seed, "cases_hash": expected,
        "target_root": str(target.resolve()), "dataset_hash": digest(read(out / "cases.json")),
        "seed_hash": seed_candidate.identity, "seed": seed_candidate.files, "editable": list(seed_candidate.editable),
        "seed_frozen": seed_frozen, "own_targets": owned,
        "contract": contract.data(), "contract_hash": contract.identity, "contract_source": contract.source,
        "estimate": shown, "engine_hash": engine_hash(), "statistics": resolved.data(),
        "budget": {"maximum": budget.maximum, "seconds": budget.seconds, "reserve": budget.reserve,
                   "reserve_seconds": budget.reserve_seconds, "approved": budget.approved,
                   **({"journey": True} if budget.journey else {})},
        "outcomes": [], "started": time.time(), "started_monotonic": time.monotonic()})
    return resolved


def _close(session: Session, pending: BaseException | None) -> None:
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


def _check_arguments(adapter: HarnessName, configuration: Configuration | None, factory: Factory | None,
                     edit: Edit | None, repeats: int | None,
                     values: Mapping[str, object]) -> tuple[ClaudeOptions, dict[str, object]]:
    """Check the run arguments before any work. Return the requested Claude options and the fields that the caller gave."""
    check_selection(adapter, configuration is not None)
    if edit is not None and edit not in get_args(Edit):
        raise ValueError("edit must be prose or prose+cli")
    if repeats is not None and repeats < 1:
        raise ValueError("repeats must be at least 1")
    given = {field: value for field, value in values.items() if value is not None}
    if given and configuration is None and refuses_claude_options(adapter):
        raise ValueError("--effort, --sandbox-read, and --sandbox-seconds apply only to --harness claude")
    if given and (configuration is not None or factory is not None):
        raise ValueError("--effort, --sandbox-read, and --sandbox-seconds apply only to the built-in harness; "
                         + "set them in the supplied configuration")
    requested = ClaudeOptions.parse(given)
    return requested, {field: getattr(requested, field) for field in given}


def _check_statistics_flags(statistics: Mapping[str, float] | None) -> dict[str, float]:
    """Check each statistics flag of the caller and return the accepted values."""
    accepted: dict[str, float] = {}
    for name, value in (statistics or {}).items():
        if name not in Statistics.flags():
            allowed = ", ".join(statistics_flag(flag) for flag in Statistics.flags())
            raise ValueError(f"unknown statistics flag: {name}; allowed: {allowed}")
        try:
            checked = Statistics.declared({name: value})[name]
        except ValueError as error:
            raise ValueError(f"{statistics_flag(name)} {str(error).removeprefix(f'statistics {name} ')}") from None
        accepted[name] = cast(float, checked)
    return accepted


def _record_history(session: Session, loaded: Loaded) -> None:
    """Add this run's attempts to the store, then write the history block of the search. A store fault adds a warning."""
    path = store_path(session.contract.skill, text_field(session.record, "target_root"))
    attempts = cast(dict[str, object], session.record["search"]).get("attempts")
    warning = write_history(path, session.record, text_field(session.record, "run_id"),
                            cast(list[dict[str, object]], attempts or []))
    if warning is not None and warning not in loaded.warnings:
        loaded.warnings.append(warning)
    with session.lock:
        cast(dict[str, object], session.record["search"])["history"] = loaded.summary()
        session.checkpoint()


def _search(session: Session) -> None:
    """Run the search with the compatible earlier attempts of the history store, then add this run's attempts to it.

    A run with `history` off neither reads nor writes the store. A store fault adds a warning and keeps the result.
    """
    if session.record.get("history") is not True:
        session.search()
        with session.lock:
            cast(dict[str, object], session.record["search"])["history"] = {"enabled": False}
            session.checkpoint()
        return
    path = store_path(session.contract.skill, text_field(session.record, "target_root"))
    loaded = load_history(path, session.record, text_field(session.record, "run_id"))
    session.search(earlier=loaded.attempts)
    _record_history(session, loaded)


def _repair_history(session: Session) -> None:
    """Write the store for a run that stopped after the `searched` checkpoint and before the store write.

    The write replaces the line of the same run id, so a repeat is safe. The block names no earlier run, because this
    resume did not use the history in its search.
    """
    search = session.record.get("search")
    if session.record.get("history") is True and isinstance(search, dict) and "history" not in search:
        _record_history(session, Loaded())


def _drive(session: Session) -> dict[str, object]:
    """Run the remaining phases of `session`, close the provider, and return the run summary."""
    pending: BaseException | None = None
    try:
        try:
            if session.phase == "prepared":
                session.check_name()
            session.preflight()
            if session.phase == "prepared":
                session.check_seed()
                _search(session)
            elif session.phase == "searched":
                _repair_history(session)
            if session.phase == "searched":
                session.gate()
            if session.phase == "gated":
                session.replay()
            if session.phase == "replayed":
                session.finish()
        except EnvironmentDiffers:
            raise
        except (OSError, ValueError, RuntimeError, OverflowError) as error:
            fault = session.authoritative(error)
            session.record_failure(fault)
            if fault is error:
                raise
            raise fault from error
    except BaseException as error:
        pending = error
        raise
    finally:
        _close(session, pending)
    return summary(session.record, session.out)


def run(target: Path, out: Path, model: str, *, adapter: HarnessName = "claude", live: bool = False,
        edit: Edit | None = None, repeats: int | None = None, seed: int | None = None,
        approve_cases: str | None = None, approve_budget: int | None = None,
        effort: str | None = None, sandbox_read: Sequence[str] | None = None, sandbox_seconds: int | None = None,
        factory: Factory | None = None, configuration: Configuration | None = None,
        statistics: Mapping[str, float] | None = None,
        judge_model: str | None = None, history: bool | None = None, kinds: Sequence[str] = ()) -> dict[str, object]:
    """Run or resume one autoimprove run. Each stop raises a `Stop` that carries the question data.

    `judge_model` sets the model of the judge calls. Task and reflection calls use `model`. The first run records it,
    and a resume that passes another value stops with `run-config-differs`.

    `effort`, `sandbox_read`, and `sandbox_seconds` set the Claude options of every role. The first run records
    them. A resume uses the recorded options. A value that a resume passes must match the record.

    `history` turns the private cross-run attempt history on or off. It is on by default. The first run records it,
    and a resume that passes another value stops with `run-config-differs`.
    """
    requested, given = _check_arguments(
        adapter, configuration, factory, edit, repeats,
        {"effort": effort, "sandbox_read": None if sandbox_read is None else tuple(sandbox_read),
         "sandbox_seconds": sandbox_seconds})
    if judge_model is not None and not judge_model.strip():
        raise ValueError("--judge-model must name a model")
    if judge_model is not None and (configuration is not None or factory is not None):
        raise ValueError("--judge-model applies only to the built-in harness; set the judge model in the supplied "
                         + "configuration")
    statistics_given = _check_statistics_flags(statistics)
    if is_inside(out, target):
        raise ValueError("--out must be outside the target skill directory")
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    require_private(out)
    path = out / "run.json"
    with open_lock(out) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop("run-in-progress", "another run holds this run directory",
                       {"next": "wait for the other run to finish, then call run again"}) from None
        if path.exists():
            record = open_record(path)
            refuse_terminated(record)
            refuse_too_easy(record)
            if record.get("phase") == "complete":
                # A completed run summarises without reading its statistics unless a flag asks to compare them.
                if statistics_given:
                    check_statistics(statistics_given, frozen_statistics(record))
                return summary(record, out)
            refuse_nono(record)
            frozen = frozen_statistics(record)
            check_statistics(statistics_given, frozen)
            check_resume(record, target, edit, repeats, seed, model, adapter, given, configuration is not None,
                         judge_model, history)
        else:
            frozen = _prepare(target, out, model, adapter, edit, repeats or DEFAULT_REPEATS, seed,
                              approve_cases, approve_budget, resolve_harness=factory is None and configuration is None,
                              options=requested, configuration=configuration,
                              statistics_given=statistics_given, judge_model=judge_model,
                              history=True if history is None else history, requested_kinds=kinds)
        if not live:
            raise Stop("live-required", "live model calls require --live", {"next": "call run again with --live"})
        session = Session(out, model, adapter, factory, configuration, statistics=frozen)
        return _drive(session)

"""One resumable autoimprove run: intake, one search, one holdout gate.

Phases: prepared, searched, gated, complete. `run.json` (schema 4) holds the checkpoint of each phase.
"""
from __future__ import annotations

import fcntl
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast, get_args

from skillz_experiments._budget import (
    BudgetUnapproved,
    approve,
)
from skillz_experiments._candidate import NEW_CLI, Candidate, store_frozen
from skillz_experiments._cases import CodedError, Split, digest, load_cases
from skillz_experiments._intake import (
    DRAFT_NAME,
    HOLDOUT_MINIMUM,
    approval_question,
    case_hash,
    freeze,
    intake_contract,
    load_draft,
    skill_facts,
    split_cases,
)
from skillz_experiments._records import SCHEMA_VERSION, open_record, read, write
from skillz_experiments._seed import check_coverage, seed_cases
from skillz_experiments._wedge_targets import plan_targets, seal, settle
from skillz_experiments.gate import Statistics
from skillz_experiments.harness import (
    TERMINAL_CODES as _TERMINAL_CODES,
)
from skillz_experiments.harness import (
    ClaudeOptions,
    Configuration,
    EnvironmentDiffers,
    doctor,
    resolve_read_roots,
    validate_isolation,
)
from skillz_experiments.run._export import summary
from skillz_experiments.run._plan import editable_files, plan_estimate, size_plan, sizing_options, supported_kind
from skillz_experiments.run._resume import (
    check_resume,
    check_statistics,
    frozen_statistics,
    open_lock,
    refuse_terminated,
    require_private,
    statistics_flag,
    resolve_statistics,
)
from skillz_experiments.run._session import Factory, HarnessName, engine_hash, is_inside, Session
from skillz_experiments.run._stop import Stop
from skillz_experiments.search import Edit

DEFAULT_SEED = 20261006


DEFAULT_REPEATS = 3


def _prepare(target: Path, out: Path, model: str, adapter: HarnessName, edit: Edit, repeats: int, seed: int | None,
             approve_cases: str | None, approve_budget: int | None, *, resolve_harness: bool = False,
             options: ClaudeOptions, configuration: Configuration | None = None, isolation: str = "claude",
             statistics_given: Mapping[str, float] | None = None) -> Statistics:
    """Run the two approvals and write the prepared record. No model call happens here.

    A fresh run with a built-in harness first runs the free host checks, so a host problem stops the run
    before the user drafts or approves any case.
    """
    draft = out / DRAFT_NAME
    if not draft.is_file():
        report = doctor(adapter, isolation) if resolve_harness else None
        if report is not None and not report["ok"]:
            raise Stop("host-not-ready", "fix each failed host check, then call run again; no model call ran", report)
        data: dict[str, object] = {"draft": str(draft), "facts": skill_facts(target)}
        seeded = seed_cases(target)
        if seeded is not None:
            data["seed_cases"] = seeded
        raise Stop("cases-missing", f"write the case draft to {draft}, then call run again",
                   data | ({"doctor": report} if report else {}))
    split_seed = DEFAULT_SEED if seed is None else seed
    contract = intake_contract(target)
    resolved = resolve_statistics(contract, statistics_given or {})
    kind = supported_kind(contract)
    judge = configuration.roles.get("judge") if configuration is not None else None
    judge_adapter = judge.adapter if judge is not None else adapter
    if contract.kinds[kind].capture and judge_adapter != "claude":
        raise CodedError("contract-capture-unsupported", f"the kind {kind} declares capture, and only "
                         + "the claude harness can show capture files to the judge; run with --harness claude")
    drafted = load_draft(draft)
    check_coverage(target, drafted)
    cases = split_cases(drafted, split_seed, kind)
    unknown = sorted(set(resolved.family_budgets) - {case.family for case in cases})
    if unknown:
        raise CodedError("contract-family-unknown", f"the contract family_budgets names no draft family: {', '.join(unknown)}; "
                         + f"the draft families are {', '.join(sorted({case.family for case in cases}))}")
    held = sum(case.split == "holdout" and case.kind == kind for case in cases)
    if held < HOLDOUT_MINIMUM:
        raise CodedError("cases-too-few", f"the holdout has {held} scored cases; the minimum is {HOLDOUT_MINIMUM}; "
                         + "add task cases in more families to the draft, then call run again")
    expected = case_hash(cases, split_seed)
    if resolve_harness or configuration is not None:
        chosen = configuration if configuration is not None else Configuration.single(adapter, model, isolation, options)
        for role in chosen.roles.values():
            _ = resolve_read_roots(role.options.sandbox_read, out)
    if approve_cases != expected:
        raise Stop("cases-unapproved", "ask the user to approve the cases, then call run with --approve-cases HASH",
                   {"question": approval_question(cases, split_seed, contract), "case_hash": expected,
                    "seed": split_seed, "statistics": resolved.data(),
                    **({} if configuration is not None else {"claude_options": options.data()})})
    freeze(out / "cases.json", cases, split_seed, expected)
    scored = [case for case in load_cases(out / "cases.json", contract.grader_types()) if case.eligible]
    count = {split: sum(case.split == split for case in scored) for split in cast(tuple[str, ...], get_args(Split))}
    skeleton = Candidate.capture(target, [], contract)
    wedge = plan_targets(target, skeleton.files, contract, edit)
    files = skeleton.files | wedge.sources | ({NEW_CLI: ""} if wedge.capable is not None else {})
    frozen = skeleton.frozen | wedge.frozen
    seed_candidate = Candidate(files, tuple(editable_files(files, contract, edit, frozen.keys(), wedge)), contract, frozen=frozen)
    plan = size_plan(contract, scored, repeats, sizing_options(configuration, options))
    shown = plan_estimate(plan, repeats, count["holdout"])
    shown_options = {} if configuration is not None else {"claude_options": options.data()}
    try:
        budget = approve(plan.sized, approve_budget, reserve=plan.holdout_calls, reserve_time=plan.reserve_seconds)
    except BudgetUnapproved as error:
        raise Stop("budget-unapproved", str(error), {"estimate": shown, "approve": "call run with --approve-budget CALLS",
                                                     "statistics": resolved.data(), **shown_options}) from None
    offered = wedge.capable is not None
    wedge, layers = settle(wedge, out / "site")
    if offered and wedge.capable is None:
        files = skeleton.files | wedge.sources
        frozen = skeleton.frozen | wedge.frozen
        seed_candidate = Candidate(files, tuple(editable_files(files, contract, edit, frozen.keys(), wedge)), contract, frozen=frozen)
    seed_candidate, owned = seal(wedge, seed_candidate, target, layers)
    seed_frozen = store_frozen(seed_candidate, out / "frozen")
    write(out / "run.json", {
        "schema_version": SCHEMA_VERSION, "phase": "prepared", "calls": 0, "model": model, "adapter": adapter,
        "isolation": isolation, **({"claude_options": options.data()} if resolve_harness else {}),
        "edit": edit, "repeats": repeats, "split_seed": split_seed, "cases_hash": expected,
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
            terminal = error.code in _TERMINAL_CODES and session.record.get("failure_code") not in _TERMINAL_CODES
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


def run(target: Path, out: Path, model: str, *, adapter: HarnessName = "claude", live: bool = False,
        edit: Edit | None = None, repeats: int | None = None, seed: int | None = None,
        approve_cases: str | None = None, approve_budget: int | None = None,
        effort: str | None = None, sandbox_read: Sequence[str] | None = None, sandbox_seconds: int | None = None,
        factory: Factory | None = None, configuration: Configuration | None = None,
        isolation: str = "claude", statistics: Mapping[str, float] | None = None) -> dict[str, object]:
    """Run or resume one autoimprove run. Each stop raises a `Stop` that carries the question data.

    `effort`, `sandbox_read`, and `sandbox_seconds` set the Claude options of every role. The first run records
    them. A resume uses the recorded options. A value that a resume passes must match the record.
    """
    if adapter not in ("claude", "codex"):
        raise ValueError("harness must be claude or codex")
    validate_isolation(adapter, isolation)
    if edit is not None and edit not in get_args(Edit):
        raise ValueError("edit must be prose or prose+cli")
    if repeats is not None and repeats < 1:
        raise ValueError("repeats must be at least 1")
    values = {"effort": effort, "sandbox_read": None if sandbox_read is None else tuple(sandbox_read),
              "sandbox_seconds": sandbox_seconds}
    given = {field: value for field, value in values.items() if value is not None}
    if given and adapter == "codex":
        raise ValueError("--effort, --sandbox-read, and --sandbox-seconds apply only to --harness claude")
    if given and (configuration is not None or factory is not None):
        raise ValueError("--effort, --sandbox-read, and --sandbox-seconds apply only to the built-in harness; "
                         + "set them in the supplied configuration")
    requested = ClaudeOptions.parse(given)
    given = {field: getattr(requested, field) for field in given}
    statistics_given: dict[str, float] = {}
    for name, value in (statistics or {}).items():
        if name not in Statistics.flags():
            allowed = ", ".join(statistics_flag(flag) for flag in Statistics.flags())
            raise ValueError(f"unknown statistics flag: {name}; allowed: {allowed}")
        try:
            checked = Statistics.declared({name: value})[name]
        except ValueError as error:
            raise ValueError(f"{statistics_flag(name)} {str(error).removeprefix(f'statistics {name} ')}") from None
        statistics_given[name] = cast(float, checked)
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
            if record.get("phase") == "complete":
                # A completed run summarises without reading its statistics unless a flag asks to compare them.
                if statistics_given:
                    check_statistics(statistics_given, frozen_statistics(record))
                return summary(record)
            frozen = frozen_statistics(record)
            check_statistics(statistics_given, frozen)
            check_resume(record, target, edit, repeats, seed, model, adapter, given, isolation)
        else:
            frozen = _prepare(target, out, model, adapter, edit or "prose", repeats or DEFAULT_REPEATS, seed,
                              approve_cases, approve_budget, resolve_harness=factory is None and configuration is None,
                              options=requested, configuration=configuration, isolation=isolation,
                              statistics_given=statistics_given)
        if not live:
            raise Stop("live-required", "live model calls require --live", {"next": "call run again with --live"})
        session = Session(out, model, adapter, factory, configuration, isolation, statistics=frozen)
        pending: BaseException | None = None
        try:
            try:
                if session.phase == "prepared":
                    session.check_name()
                session.preflight()
                if session.phase == "prepared":
                    session.check_seed()
                    session.search()
                if session.phase == "searched":
                    session.gate()
                if session.phase == "gated":
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
        return summary(session.record)

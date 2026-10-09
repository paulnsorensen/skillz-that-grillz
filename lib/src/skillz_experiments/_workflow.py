"""One resumable autoimprove run: intake, one search, one holdout gate. `export` stays write-only.

Phases: prepared, searched, gated, complete. `run.json` (schema 2) holds the checkpoint of each phase.
"""
from __future__ import annotations

import difflib
import errno
import fcntl
import json
import math
import os
import stat
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO, cast, final, get_args

from skillz_experiments._audit import identity as judge_identity
from skillz_experiments._candidate import Candidate, candidate_files
from skillz_experiments._cases import Case, CodedError, Split, digest, load_cases, mapping
from skillz_experiments._claude import (NOTICE_CODES as _NOTICE_CODES, TERMINAL_CODES as _TERMINAL_CODES, ClaudeOptions,
                                        resolve_read_roots)
from skillz_experiments._codex import VERSION
from skillz_experiments._contract import Contract, parse
from skillz_experiments._doctor import doctor
from skillz_experiments._gate import case_deltas, verdict as gate_verdict
from skillz_experiments._harness import Configuration, EnvironmentDiffers, Harness, validate_isolation
from skillz_experiments._intake import (DRAFT_NAME, HOLDOUT_MINIMUM, approval_question, case_hash, freeze, intake_contract,
                                        load_draft, skill_facts, split_cases)
from skillz_experiments._records import SCHEMA_VERSION, open_record, read, write
from skillz_experiments._runtime import (APPROVED_CALLS, APPROVED_SECONDS, MAX_CONCURRENT_CALLS, Budget, BudgetExhausted,
                                         BudgetUnapproved, Estimate, approve, estimate, gate_seconds)
from skillz_experiments._search import Edit, REFLECTION_MINIBATCH, overshoot, search as pareto_search

DEFAULT_SEED = 20261006
DEFAULT_REPEATS = 3
PREFLIGHT_CALLS = 3
SEARCH_CASE_FACTOR = 4
CALLS_PER_REFLECTION = 2 * REFLECTION_MINIBATCH
MINIMUM_SEARCH_MARGIN = 4
HOLDOUT_RETRIES_PER_ARM = 1
# Seconds that the wall clock may step back between prepare and resume, for example after an NTP step.
CLOCK_TOLERANCE = 5.0
HarnessName = str


class Stop(CodedError):
    """A non-interactive stop. The outer agent reads `data`, answers the question, and calls `run` again."""

    def __init__(self, code: str, message: str, data: dict[str, object]) -> None:
        super().__init__(code, message)
        self.data: dict[str, object] = {"stop": code, "message": message} | data


def _failed_outcome(case: Case, candidate: Candidate, error: Exception) -> dict[str, object]:
    """Return the outcome that records a failed search evaluation as a zero score."""
    return {"arm": "search", "split": case.split, "case_id": case.identifier, "score": 0.0,
            "status": "evaluation-failed", "failure": str(error), "search_candidate": candidate.identity}


def _baseline_rejected() -> Stop:
    return Stop("baseline-contract-rejected", "original candidate fails the frozen native/helper contract",
                {"next": "fix the skill or its contract, then start a new run in a new directory"})


def _unit_score(value: object) -> float:
    """Clamp a provider score to 0..1. A NaN counts as 0."""
    return min(1.0, max(0.0, float(cast(float, value))))


def _inside(path: Path, root: Path) -> bool:
    """Return whether `path` resolves, through any symlink, to `root` or below it."""
    return path.resolve().is_relative_to(root.resolve())


class _Provider(Protocol):
    def preflight(self) -> dict[str, object]: ...
    def close(self) -> None: ...
    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]: ...
    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]: ...


Factory = Callable[[str, Budget, Callable[[], None]], _Provider]


def _engine_hash() -> str:
    return digest({path.name: path.read_text() for path in Path(__file__).parent.glob("*.py")})


def _field(record: dict[str, object], key: str) -> object:
    if key not in record:
        raise ValueError(f"run record lacks {key}")
    return record[key]


def _text_field(record: dict[str, object], key: str) -> str:
    value = _field(record, key)
    if not isinstance(value, str):
        raise ValueError(f"run record field {key} must be text")
    return value


def _number_field(record: dict[str, object], key: str) -> float:
    value = _field(record, key)
    if type(value) not in (int, float):
        raise ValueError(f"run record field {key} must be a number")
    return cast(float, value)


def _int_field(record: dict[str, object], key: str) -> int:
    value = _field(record, key)
    if type(value) is not int or value < 0:
        raise ValueError(f"run record field {key} must be a nonnegative integer")
    return value


def _outcomes(record: dict[str, object]) -> list[dict[str, object]]:
    value = _field(record, "outcomes")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in cast(list[object], value)):
        raise ValueError("run record field outcomes must be a list of objects")
    return cast(list[dict[str, object]], value)


@dataclass(frozen=True)
class _Plan:
    metric_calls: int
    reflection_calls: int
    holdout_calls: int
    retry_calls: int
    sized: Estimate
    sandbox_gate: int = 0
    sandbox_total: int = 0

    @property
    def reserve_seconds(self) -> int:
        """Return the seconds that the holdout gate holds, never more than the whole run."""
        return min(gate_seconds(self.holdout_calls) + self.sandbox_gate, self.sized.seconds)


def _command_seconds(contract: Contract, cases: Sequence[Case], options: ClaudeOptions) -> int:
    """Return the sandbox time limit of one evaluation, or 0 when no scored case runs a command grader."""
    return options.sandbox_seconds if any(contract.grader(case.kind).type in ("command", "hybrid") for case in cases) else 0


def _plan(train: int, validation: int, holdout: int, per_evaluation: int, repeats: int, sandbox_seconds: int = 0) -> _Plan:
    """Size the run. Shrink the search share to fit the cap; stop when the fixed cost alone exceeds it.

    `metric` counts metric calls: one search evaluation of one case. A metric call costs `per_evaluation` model calls.
    `_Plan.metric_calls` and `reflection_calls` keep that unit. `holdout_calls` and `sized.calls` count model calls.
    `holdout_calls` is the gate reserve. It holds the gate and `HOLDOUT_RETRIES_PER_ARM` retries per arm,
    because a failed holdout call stays claimed.
    `sandbox_seconds` is the time limit of one command-grader evaluation. It adds time to the estimate and to the gate reserve.
    """
    holdout_evaluations = 2 * holdout * repeats + 2 * HOLDOUT_RETRIES_PER_ARM
    retry_calls = 2 * HOLDOUT_RETRIES_PER_ARM * per_evaluation
    holdout_calls = 2 * holdout * repeats * per_evaluation + retry_calls
    if PREFLIGHT_CALLS + holdout_calls > APPROVED_CALLS:
        raise Stop("budget-unapproved", f"the preflight and the holdout gate alone need {PREFLIGHT_CALLS + holdout_calls} "
                   + f"calls; the cap is {APPROVED_CALLS}; lower --repeats or the holdout size", {})

    def size(metric: int) -> Estimate:
        return estimate(preflight_calls=PREFLIGHT_CALLS, holdout=2 * holdout, calls_per_evaluation=per_evaluation,
                        repeats=repeats, search_calls=metric * per_evaluation, retry_calls=retry_calls,
                        reflection_calls=math.ceil(metric / CALLS_PER_REFLECTION))

    metric = SEARCH_CASE_FACTOR * (train + validation)
    while metric > 0 and size(metric).calls > APPROVED_CALLS:
        metric -= 1
    if metric < overshoot(validation) + MINIMUM_SEARCH_MARGIN:
        raise Stop("budget-unapproved", "too few calls are left for a search under the cap; "
                   + "lower --repeats or the holdout size", {})
    sandbox_gate = math.ceil(holdout_evaluations * sandbox_seconds / MAX_CONCURRENT_CALLS)
    sandbox_total = math.ceil((metric + holdout_evaluations) * sandbox_seconds / MAX_CONCURRENT_CALLS)
    sized = size(metric)
    return _Plan(metric, math.ceil(metric / CALLS_PER_REFLECTION), holdout_calls, retry_calls,
                 Estimate(sized.calls, sized.seconds + sandbox_total), sandbox_gate, sandbox_total)


def _editable(files: Mapping[str, str], contract: Contract, edit: Edit) -> list[str]:
    """Return the editable names: Markdown for `prose`, plus helper scripts for `prose+cli`."""
    base = list(contract.editable) or [name for name in files if name.endswith(".md") or name.startswith("scripts/")]
    if contract.helper is not None and contract.helper.path not in files:
        raise CodedError("helper-file-missing", f"the contract helper {contract.helper.path} is missing from the target")
    names = sorted(name for name in base if name.endswith(".md"))
    if not names:
        raise CodedError("prompt-components-missing", "search needs editable Markdown")
    if edit == "prose+cli":
        code = {name for name in base if not name.endswith(".md")}
        if contract.helper is not None:
            code.add(contract.helper.path)
        if not code:
            raise CodedError("helper-missing", "prose+cli needs a helper script to edit; add one under scripts/")
        names += sorted(code)
    absent = [name for name in names if name not in files]
    if absent:
        raise CodedError("helper-file-missing", f"editable files are missing from the target: {', '.join(absent)}")
    return names


def _scored_kind(contract: Contract) -> str:
    if "task" in contract.kinds:
        return "task"
    if len(contract.kinds) == 1:
        return next(iter(contract.kinds))
    raise CodedError("contract-kinds", f"the contract declares the kinds {', '.join(sorted(contract.kinds))}; "
                     + "run needs a contract with a `task` kind or exactly one kind")


def _supported_kind(contract: Contract) -> str:
    """Return the scored kind. Stop on an audit-graded kind: intake never reviews labels, so no case is eligible."""
    kind = _scored_kind(contract)
    if contract.kinds[kind].type == "audit":
        raise CodedError("contract-audit-unsupported", f"the scored kind {kind!r} uses the audit grader; "
                         + "run does not support audit-graded kinds yet")
    return kind


def _estimate(plan: _Plan, repeats: int, holdout_cases: int) -> dict[str, object]:
    """Return the estimate that `run.json` records. `_prepare` writes it and `_verify_budget` rebuilds it."""
    return {"calls": plan.sized.calls, "seconds": plan.sized.seconds, "search_calls": plan.metric_calls,
            "repeats": repeats, "holdout_cases": holdout_cases, "holdout_retry_calls": plan.retry_calls,
            "metric_calls": plan.metric_calls, "reflection_calls": plan.reflection_calls,
            **({"sandbox_seconds": plan.sandbox_total} if plan.sandbox_total else {})}


def _prepare(target: Path, out: Path, model: str, adapter: HarnessName, edit: Edit, repeats: int, seed: int | None,
             approve_cases: str | None, approve_budget: int | None, *, resolve_harness: bool = False,
             options: ClaudeOptions, configuration: Configuration | None = None, isolation: str = "claude") -> None:
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
        raise Stop("cases-missing", f"write the case draft to {draft}, then call run again",
                   data | ({"doctor": report} if report else {}))
    split_seed = DEFAULT_SEED if seed is None else seed
    contract = intake_contract(target)
    kind = _supported_kind(contract)
    cases = split_cases(load_draft(draft), split_seed, kind)
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
                    "seed": split_seed, "claude_options": options.data()})
    freeze(out / "cases.json", cases, split_seed, expected)
    scored = [case for case in load_cases(out / "cases.json", contract.grader_types()) if case.eligible]
    count = {split: sum(case.split == split for case in scored) for split in cast(tuple[str, ...], get_args(Split))}
    per_evaluation = max(contract.calls(case.kind) for case in scored)
    skeleton = Candidate.capture(target, [], contract)
    seed_candidate = Candidate(skeleton.files, tuple(_editable(skeleton.files, contract, edit)), contract)
    plan = _plan(count["train"], count["validation"], count["holdout"], per_evaluation, repeats,
                 _command_seconds(contract, scored, options))
    shown = _estimate(plan, repeats, count["holdout"])
    try:
        budget = approve(plan.sized, approve_budget, reserve=plan.holdout_calls, reserve_time=plan.reserve_seconds)
    except BudgetUnapproved as error:
        raise Stop("budget-unapproved", str(error), {"estimate": shown, "approve": "call run with --approve-budget CALLS",
                                                              "claude_options": options.data()}) from None
    write(out / "run.json", {
        "schema_version": SCHEMA_VERSION, "phase": "prepared", "calls": 0, "model": model, "adapter": adapter,
        "isolation": isolation, **({"claude_options": options.data()} if resolve_harness else {}),
        "edit": edit, "repeats": repeats, "split_seed": split_seed, "cases_hash": expected,
        "target_root": str(target.resolve()), "dataset_hash": digest(read(out / "cases.json")),
        "seed_hash": seed_candidate.identity, "seed": seed_candidate.files, "editable": list(seed_candidate.editable),
        "contract": contract.data(), "contract_hash": contract.identity, "contract_source": contract.source,
        "estimate": shown, "engine_hash": _engine_hash(),
        "budget": {"maximum": budget.maximum, "seconds": budget.seconds, "reserve": budget.reserve,
                   "reserve_seconds": budget.reserve_seconds, "approved": budget.approved},
        "outcomes": [], "started": time.time(), "started_monotonic": time.monotonic()})


@final
class _Session:
    def __init__(self, out: Path, model: str, adapter: HarnessName, factory: Factory | None,
                 supplied: Configuration | None, isolation: str = "claude") -> None:
        self.out = out
        self.lock = threading.RLock()
        self.record = open_record(out / "run.json")
        if _phase(self.record) not in _PHASES:
            raise Stop("run-record-tampered", "the recorded phase is unknown; create a new run", {})
        self.contract = parse(_field(self.record, "contract"), _text_field(self.record, "contract_source"))
        if self.contract.identity != _text_field(self.record, "contract_hash"):
            raise ValueError("contract differs from the frozen record; create a new run")
        imported, judged = self._import_cases()
        self.cases = [case for case in imported if case.eligible]
        editable = _field(self.record, "editable")
        if not isinstance(editable, list) or not all(isinstance(name, str) for name in cast(list[object], editable)):
            raise ValueError("run record field editable must be a list of text")
        self.seed = Candidate(candidate_files(_field(self.record, "seed")), tuple(cast(list[str], editable)), self.contract)
        self.edit: Edit = cast(Edit, _text_field(self.record, "edit"))
        self.outcomes = _outcomes(self.record)
        options = ClaudeOptions.parse(mapping(self.record.get("claude_options", {})))
        configuration = supplied if supplied is not None else (
            Configuration.single(adapter, model, isolation, options) if factory is None else None)
        self._freeze_identities(model, configuration, judged)
        self.budget = self._open_budget()
        self.provider: _Provider = (configuration.create(model, self.budget, self.checkpoint, out) if configuration is not None
                                    else cast(Factory, factory)(model, self.budget, self.checkpoint))
        self._fault: CodedError | None = None
        self._search_cases = {case.identifier: case for case in self.cases if case.split != "holdout"}

    @property
    def phase(self) -> str:
        return _text_field(self.record, "phase")

    def _import_cases(self) -> tuple[list[Case], bool]:
        """Load the frozen cases. Return them and whether any case needs a judge."""
        if _text_field(self.record, "dataset_hash") != digest(read(self.out / "cases.json")):
            raise ValueError("dataset differs from the frozen record")
        imported = load_cases(self.out / "cases.json", self.contract.grader_types())
        return imported, any(self.contract.judged(case.kind) for case in imported)

    def _freeze_identities(self, model: str, configuration: Configuration | None, judged: bool) -> None:
        if configuration is not None:
            target = self.record.get("target_root")
            if not isinstance(target, str):
                raise ValueError("run lacks candidate boundary; create a new run")
            configuration.check_boundary(Path(target))
            identity = configuration.identity()
            _ = self.record.setdefault("harness", identity)
            if self.record["harness"] != identity:
                raise ValueError("harness configuration differs from the frozen record; create a new run")
        if judged:
            judge_model = configuration.roles["judge"].model if configuration is not None else model
            _ = self.record.setdefault("judge_model", judge_model)
            _ = self.record.setdefault("judge", judge_identity(judge_model))
            if self.record["judge"] != judge_identity(judge_model):
                raise ValueError("judge differs from the frozen record; create a new run")
        if self.seed.identity != _text_field(self.record, "seed_hash"):
            raise ValueError("seed differs from the frozen record")
        _ = self.record.setdefault("engine_hash", _engine_hash())
        if self.record["engine_hash"] != _engine_hash():
            raise ValueError("evaluator differs from the frozen record; create a new run")

    def _open_budget(self) -> Budget:
        """Rebuild the approved budget. A resumed run keeps `approved=True` and the calls it already spent.

        Elapsed time is the larger of the monotonic and the wall-clock span since prepare. A reboot resets the
        monotonic clock, so a negative monotonic span falls back to the wall clock. The wall clock may step back
        by up to `CLOCK_TOLERANCE` seconds. The gate time reserve applies only inside `search`.
        """
        budget = mapping(_field(self.record, "budget"))
        self._verify_budget(budget)
        wall = time.time() - _number_field(self.record, "started")
        if not -CLOCK_TOLERANCE <= wall < math.inf:
            raise CodedError("clock-skew", "the wall clock is more than "
                             + f"{CLOCK_TOLERANCE:g} seconds earlier than the run start; set the clock right, or create a new run")
        monotonic = time.monotonic() - _number_field(self.record, "started_monotonic")
        elapsed = max(wall, monotonic, 0.0)
        seconds = _number_field(budget, "seconds")
        if seconds - elapsed <= 0:
            raise BudgetExhausted("global deadline exhausted")
        return Budget(_int_field(budget, "maximum"), seconds - elapsed, reserve=_int_field(budget, "reserve"),
                      calls=_int_field(self.record, "calls"), approved=budget.get("approved") is True)

    def _verify_budget(self, budget: dict[str, object]) -> None:
        """Stop when the recorded budget differs from the plan that the frozen cases, repeats, and edit give."""
        count = {split: sum(case.split == split for case in self.cases) for split in cast(tuple[str, ...], get_args(Split))}
        repeats = _int_field(self.record, "repeats")
        options = ClaudeOptions.parse(mapping(self.record.get("claude_options", {})))
        plan = _plan(count["train"], count["validation"], count["holdout"],
                     max(self.contract.calls(case.kind) for case in self.cases), repeats,
                     _command_seconds(self.contract, self.cases, options))
        expected = _estimate(plan, repeats, count["holdout"])
        recorded = mapping(_field(self.record, "estimate"))
        maximum = max(plan.sized.calls, 1)
        seconds = _number_field(budget, "seconds")
        if "reserve_seconds" not in budget:
            raise ValueError("run was prepared by an older runner; create a new run")

        def differs(found: object, wanted: object) -> bool:
            return isinstance(found, bool) or found != wanted

        if (any(differs(recorded.get(key), value) for key, value in expected.items())
                or differs(budget.get("maximum"), maximum)
                or differs(budget.get("reserve"), min(plan.holdout_calls, maximum))
                or differs(budget.get("reserve_seconds"), plan.reserve_seconds)
                or not 0 < seconds <= APPROVED_SECONDS):
            raise Stop("run-record-tampered", "the recorded budget differs from the plan of the frozen run; create a new run", {})

    def checkpoint(self) -> None:
        with self.lock:
            self.record["calls"] = self.budget.calls
            write(self.out / "run.json", self.record)

    def cases_for(self, split: Split) -> list[Case]:
        return sorted((case for case in self.cases if case.split == split), key=lambda case: case.identifier)

    def preflight(self) -> None:
        recorded = self.record.get("preflight")
        preflight = (self.provider.preflight(cast(dict[str, object], recorded) if isinstance(recorded, dict) else None)
                     if isinstance(self.provider, Harness) else self.provider.preflight())
        environment_hash = preflight.get("environment_hash")
        _ = self.record.setdefault("environment_hash", environment_hash)
        if self.record["environment_hash"] != environment_hash:
            raise EnvironmentDiffers("runtime environment differs from the frozen record")
        self.record["preflight"] = preflight
        if isinstance(self.provider, Harness) and any(
                role.adapter == "codex" for role in self.provider.configuration.roles.values()):
            self.record["codex_version"] = VERSION
        self.checkpoint()

    def evaluate_case(self, candidate: Candidate, case: Case, arm: str, *, holdout: bool = False,
                      repeat: int | None = None) -> dict[str, object]:
        """Score one case. The model call runs outside the lock; the record update runs inside it."""
        self._raise_fault()
        try:
            result = self.provider.evaluate(candidate, case, holdout=holdout)
        except CodedError as error:
            self._latch(error)
            raise
        result.update({"arm": arm, "split": case.split, "case_id": case.identifier})
        if arm == "search":
            result["search_candidate"] = candidate.identity
        if repeat is not None:
            result["repeat"] = repeat
        with self.lock:
            self.outcomes.append(result)
            self.checkpoint()
        return result

    def _evaluate_example(self, components: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        case = self._search_cases.get(cast(str, example))
        if case is None:
            raise ValueError("holdout must not enter optimization")
        try:
            candidate = self.seed.changed(components)
        except ValueError as error:
            return 0.0, {"task_correct": 0.0, "request": case.request, "rejected": str(error)}
        try:
            result = self.evaluate_case(candidate, case, "search")
        except CodedError:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            result = _failed_outcome(case, candidate, error)
            with self.lock:
                self.outcomes.append(result)
                self.checkpoint()
        self._keep_if_best(candidate, case)
        score = _unit_score(result["score"])
        feedback: dict[str, object] = {"task_correct": score, "request": case.request}
        if "failure" in result:
            feedback["failure"] = result["failure"]
        if "scores" in result:
            feedback["grader_scores"] = result["scores"]
        return score, feedback

    def _validation_means(self, validation: list[str]) -> dict[str, float]:
        """Return the mean score of each search candidate that has a score on every validation case."""
        scores: dict[str, dict[str, float]] = {}
        for item in self.outcomes:
            if item.get("arm") == "search" and item.get("split") == "validation" and "search_candidate" in item:
                scores.setdefault(cast(str, item["search_candidate"]), {})[cast(str, item["case_id"])] = _unit_score(item["score"])
        return {identity: sum(by_case.values()) / len(by_case) for identity, by_case in scores.items()
                if set(by_case) == set(validation)}

    def _keep_if_best(self, candidate: Candidate, case: Case) -> None:
        """Record the files of the best non-seed candidate when it completes the validation set. The record stays small."""
        if case.split != "validation" or candidate.identity == self.seed.identity:
            return
        with self.lock:
            validation = [item.identifier for item in self.cases_for("validation")]
            mean = self._validation_means(validation).get(candidate.identity)
            kept = self.record.get("best_validated")
            if mean is not None and (not isinstance(kept, dict) or mean > _number_field(cast(dict[str, object], kept), "mean")):
                self.record["best_validated"] = {"identity": candidate.identity, "mean": mean,
                                                 "files": {name: candidate.files[name] for name in self.seed.editable}}
                self.checkpoint()

    def _propose(self, candidate: dict[str, str], feedback: Mapping[str, Sequence[Mapping[str, object]]],
                 components: list[str]) -> dict[str, str]:
        self._raise_fault()
        prompt, schema = _reflection_request(self.edit, candidate, feedback, components)
        try:
            result = self.provider.invoke(prompt, schema=schema)
        except CodedError as error:
            self._latch(error)
            raise
        with self.lock:
            self.outcomes.append({"arm": "search", "split": "reflection", "usage": result.get("usage"),
                                  "latency_seconds": result.get("latency_seconds")})
            self.checkpoint()
        answer = mapping(result["answer"])
        if set(answer) != set(components) or not all(isinstance(value, str) for value in answer.values()):
            raise ValueError("reflection keys differ from the frozen components")
        return cast(dict[str, str], answer)

    def _latch(self, error: CodedError) -> None:
        """Keep one CodedError from a model call. A terminal code replaces a held fault with another code. A coded fault replaces a held budget stop. Otherwise the first fault stays."""
        with self.lock:
            held = self._fault
            if held is None:
                replace = True
            elif error.code in _TERMINAL_CODES:
                replace = held.code not in _TERMINAL_CODES
            else:
                replace = isinstance(held, BudgetExhausted) and not isinstance(error, BudgetExhausted)
            if replace:
                self._fault = error

    def take_budget_stop(self) -> None:
        """Clear a latched budget stop so the gate can run. Re-raise any other latched fault."""
        with self.lock:
            fault = self._fault
            if fault is not None and not isinstance(fault, BudgetExhausted):
                raise fault
            self._fault = None

    def authoritative(self, error: Exception) -> Exception:
        """Return the fault that ends the run: a latched fault other than a budget stop, else `error`."""
        with self.lock:
            fault = self._fault
        if fault is not None and not isinstance(fault, BudgetExhausted):
            return fault
        return error

    def record_failure(self, error: Exception) -> None:
        """Record the failure text and code, drop a stale code, and forget the preflight pass after a terminal code."""
        self.record["failure"] = str(error)
        code = error.code if isinstance(error, CodedError) else None
        if code is None:
            _ = self.record.pop("failure_code", None)
        else:
            self.record["failure_code"] = code
        if code in _TERMINAL_CODES:
            _ = self.record.pop("preflight", None)
        self.checkpoint()

    def _raise_fault(self) -> None:
        """Re-raise the first CodedError from a model call. A budget stop ends the search; any other code stops the run. GEPA turns each proposer exception into a skipped proposal."""
        with self.lock:
            fault = self._fault
        if fault is not None:
            raise fault

    def _search_outcomes(self) -> list[dict[str, object]]:
        """Return the recorded search evaluations on train and validation cases."""
        return [item for item in self.outcomes
                if item.get("arm") == "search" and item.get("split") in ("train", "validation")]

    def check_name(self) -> None:
        """Stop before any live call when the seed name matches a Claude Code command or a foreign skill."""
        check = cast(Callable[[Candidate], None] | None, getattr(self.provider, "check_name", None))
        if check is not None:
            check(self.seed)

    def check_seed(self) -> None:
        """Stop before any search call when the seed fails the local candidate contract check."""
        check = cast(Callable[[Candidate], bool] | None, getattr(self.provider, "check_candidate", None))
        if check is not None and not check(self.seed):
            raise _baseline_rejected()

    def search(self) -> None:
        """Run the one GEPA search over train and validation cases. Holdout cases never enter it."""
        files = {name: self.seed.files[name] for name in self.seed.editable}
        train = [case.identifier for case in self.cases_for("train")]
        validation = [case.identifier for case in self.cases_for("validation")]
        planned = _int_field(mapping(_field(self.record, "estimate")), "metric_calls")
        spent = len(self._search_outcomes())
        allowance = planned - spent - overshoot(len(validation))
        reason = "search-share-spent-best-validated"
        if allowance >= 1:
            # Search and reflection calls leave the gate its estimated time. Preflight and the gate use all of it.
            self.budget.reserve_time(_number_field(mapping(_field(self.record, "budget")), "reserve_seconds"))
            try:
                pareto_search(files, self.edit, cast(list[object], train), cast(list[object], validation),
                              self._evaluate_example, self._propose, metric_calls=allowance)
                self._raise_fault()
                reason = "validation-mean"
            except BudgetExhausted:
                self.take_budget_stop()
                reason = "budget-exhausted-best-validated"
            finally:
                self.budget.reserve_time(0.0)
        winner = self._best_validated(validation)
        if reason == "budget-exhausted-best-validated" and winner.identity == self.seed.identity:
            reason = "budget-exhausted-seed-retained"
        scored = self._search_outcomes()
        if scored and all(item.get("status") == "evaluation-failed" for item in scored):
            raise CodedError("search-failed", "every search evaluation failed: " + str(scored[-1]["failure"])
                             + "; check the model, then start a new run directory")
        self.record["winner"] = winner.files
        self.record["search"] = {"reason": reason, "retained_seed": winner.identity == self.seed.identity}
        self.record["phase"] = "searched"
        self.checkpoint()

    def _best_validated(self, validation: list[str]) -> Candidate:
        """Return the recorded candidate with the best validation mean. The seed wins ties."""
        means = self._validation_means(validation)
        kept = self.record.get("best_validated")
        if not isinstance(kept, dict):
            return self.seed
        kept = cast(dict[str, object], kept)
        identity = _text_field(kept, "identity")
        if identity in means and means[identity] > means.get(self.seed.identity, -1.0):
            return self.seed.changed(candidate_files(kept.get("files")))
        return self.seed

    def _holdout_scores(self, arm: str) -> dict[str, list[float]]:
        scores: dict[str, list[tuple[int, float]]] = {}
        for item in self.outcomes:
            if item.get("split") == "holdout" and item.get("arm") == arm:
                scores.setdefault(cast(str, item["case_id"]), []).append(
                    (cast(int, item["repeat"]), _unit_score(item["score"])))
        return {case_id: [score for _, score in sorted(pairs)] for case_id, pairs in scores.items()}

    def gate(self) -> None:
        """Score baseline and winner on the unseen holdout, `repeats` times per case, then record the verdict."""
        holdout = self.cases_for("holdout")
        repeats = _int_field(self.record, "repeats")
        winner = self.seed.changed({name: candidate_files(_field(self.record, "winner"))[name]
                                    for name in self.seed.editable})
        if winner.identity == self.seed.identity:
            self.record["gate"] = {"verdict": "inconclusive", "delta": 0.0, "se": 0.0, "cases": len(holdout),
                                   "repeats": repeats, "reason": "winner-equals-baseline"}
        else:
            _ = self.budget.remaining()
            if any(item.get("arm") == "baseline" and item.get("status") == "candidate-contract-rejected"
                   for item in self.outcomes):
                raise _baseline_rejected()
            arms = {"baseline": self.seed, "winner": winner}
            done = {(item.get("arm"), item.get("case_id"), item.get("repeat")) for item in self.outcomes
                    if item.get("split") == "holdout"}
            todo = [(arm, case, repeat) for arm in arms for case in holdout for repeat in range(repeats)
                    if (arm, case.identifier, repeat) not in done]

            def score(task: tuple[str, Case, int]) -> None:
                arm, case, repeat = task
                result = self.evaluate_case(arms[arm], case, arm, holdout=True, repeat=repeat)
                if arm == "baseline" and result.get("status") == "candidate-contract-rejected":
                    raise _baseline_rejected()

            need = sum(self.contract.calls(case.kind) for _, case, _ in todo)
            if self.budget.maximum - self.budget.calls < need:
                raise Stop("gate-budget-exhausted", f"the holdout gate needs {need} more calls; "
                           + f"{self.budget.maximum - self.budget.calls} remain in the approved budget",
                           {"next": "start a new run in a new directory"})
            with ThreadPoolExecutor(MAX_CONCURRENT_CALLS) as pool:
                _ = list(pool.map(score, todo))
            outcome = gate_verdict(case_deltas(self._holdout_scores("baseline"), self._holdout_scores("winner")))
            self.record["gate"] = {"verdict": outcome.verdict, "delta": outcome.delta, "se": outcome.se,
                                   "cases": outcome.cases, "repeats": repeats}
        self.record["winner_hash"] = winner.identity
        self.record["phase"] = "gated"
        self.checkpoint()

    def finish(self) -> None:
        self.record["phase"] = "complete"
        self.checkpoint()


_STE_RULE = ("Write every proposed Markdown component in ASD-STE100 Simplified Technical English: "
             "active voice, present tense, one instruction per sentence, at most 20 words per procedural sentence, "
             "and at most 25 words per descriptive sentence. ")


def _reflection_request(edit: Edit, candidate: dict[str, str],
                        feedback: Mapping[str, Sequence[Mapping[str, object]]],
                        components: list[str]) -> tuple[str, dict[str, object]]:
    schema: dict[str, object] = {"type": "object",
        "properties": {key: {"type": "string"} for key in components},
        "required": components, "additionalProperties": False}
    if edit == "prose+cli":
        instruction = ("Improve the supplied skill text and helper script components. "
                       + "Keep each script's command-line contract and output format. ")
    else:
        instruction = "Improve only the supplied skill text components. Preserve the helper CLI contract. "
    head = instruction + _STE_RULE + "Return complete component contents. Do not alter independent checks or permissions.\n"
    return head + json.dumps({"candidate": candidate, "feedback": feedback}, default=str), schema


_PHASES = ("prepared", "searched", "gated", "complete")


def _phase(record: dict[str, object]) -> str:
    return _text_field(record, "phase")


def _config_differs(field: str) -> CodedError:
    return CodedError("run-config-differs", f"{field} differs from the frozen record; resume with the first-run value or create a new run")


def _require_private(out: Path) -> None:
    """Stop unless `out` is a real directory, owned by this user, that no other user can enter."""
    status = out.lstat()
    if not stat.S_ISDIR(status.st_mode):
        raise CodedError("out-unsafe", f"{out} is not a plain directory; use a private directory from `mktemp -d`")
    if status.st_uid != os.getuid() or status.st_mode & 0o077:
        raise CodedError("out-unsafe", f"{out} must be owned by you and closed to other users (mode 0700); "
                         + "use a private directory from `mktemp -d`")


def _refuse_terminated(record: dict[str, object]) -> None:
    """Stop when the run record has a terminal code."""
    stopped = record.get("failure_code")
    if stopped in _TERMINAL_CODES:
        raise Stop("run-terminated", f"the run stopped with {stopped}; start a new run directory",
                   {"next": "start a new run in a new directory", "failure_code": stopped})


def _close(session: _Session, pending: BaseException | None) -> None:
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


def _open_lock(out: Path) -> TextIO:
    """Open `run.lock` without following a symlink."""
    try:
        descriptor = os.open(out / "run.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.EMLINK):
            raise CodedError("out-unsafe", f"{out / 'run.lock'} is a symlink; remove it or use a new directory") from None
        raise
    return os.fdopen(descriptor, "w")


_OPTION_FLAGS = (("--effort", "effort"), ("--sandbox-read", "sandbox_read"), ("--sandbox-seconds", "sandbox_seconds"))


def _check_resume(record: dict[str, object], target: Path, edit: Edit | None, repeats: int | None,
                  seed: int | None, model: str, adapter: HarnessName, given: Mapping[str, object],
                  isolation: str = "claude") -> None:
    """Stop when a resume names another model, adapter, isolation, target, edit, repeats, seed, or Claude option than
    the first run.
    """
    for flag, value, field in ("--model", model, "model"), ("--harness", adapter, "adapter"):
        if _text_field(record, field) != value:
            raise _config_differs(flag)
    if record.get("isolation", "claude") != isolation:
        raise _config_differs("--isolation")
    for flag, value, field in ("--edit", edit, "edit"), ("--repeats", repeats, "repeats"), ("--seed", seed, "split_seed"):
        if value is not None and value != record.get(field):
            raise _config_differs(flag)
    recorded = ClaudeOptions.parse(mapping(record.get("claude_options", {})))
    for flag, field in _OPTION_FLAGS:
        if field in given and given[field] != getattr(recorded, field):
            raise _config_differs(flag)
    if str(target.resolve()) != record.get("target_root"):
        raise _config_differs("the target skill directory")


def run(target: Path, out: Path, model: str, *, adapter: HarnessName = "claude", live: bool = False,
        edit: Edit | None = None, repeats: int | None = None, seed: int | None = None,
        approve_cases: str | None = None, approve_budget: int | None = None,
        effort: str | None = None, sandbox_read: Sequence[str] | None = None, sandbox_seconds: int | None = None,
        factory: Factory | None = None, configuration: Configuration | None = None,
        isolation: str = "claude") -> dict[str, object]:
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
    if _inside(out, target):
        raise ValueError("--out must be outside the target skill directory")
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_private(out)
    path = out / "run.json"
    with _open_lock(out) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop("run-in-progress", "another run holds this run directory",
                       {"next": "wait for the other run to finish, then call run again"}) from None
        if path.exists():
            record = open_record(path)
            _refuse_terminated(record)
            if record.get("phase") == "complete":
                return summary(record)
            _check_resume(record, target, edit, repeats, seed, model, adapter, given, isolation)
        else:
            _prepare(target, out, model, adapter, edit or "prose", repeats or DEFAULT_REPEATS, seed, approve_cases,
                     approve_budget, resolve_harness=factory is None and configuration is None, options=requested,
                     configuration=configuration, isolation=isolation)
        if not live:
            raise Stop("live-required", "live model calls require --live", {"next": "call run again with --live"})
        session = _Session(out, model, adapter, factory, configuration, isolation)
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


def summary(record: dict[str, object]) -> dict[str, object]:
    keys = ("schema_version", "phase", "calls", "model", "adapter", "edit", "codex_version", "harness", "judge",
            "gate", "search", "estimate", "contract_hash", "contract_source", "isolation", "claude_options")
    result = {key: record.get(key) for key in keys}
    if "close_warning" in record:
        code = mapping(record["close_warning"]).get("code")
        hint = "no action needed" if code in _NOTICE_CODES else "log in again before the next run"
        result["close_warning"] = {"code": code, "hint": hint}
    return result


def _export_outcome(item: dict[str, object]) -> dict[str, object]:
    allowed = {"arm", "split", "case_id", "repeat", "score", "status", "loaded", "helper_executed", "candidate_hash",
               "case_hash", "latency_seconds", "judge_latency_seconds", "evidence_valid", "matched", "false_positives",
               "false_negatives", "precision", "recall", "detection_f1", "severity_accuracy", "actionability_rate", "scores"}
    result = {key: value for key, value in item.items() if key in allowed}
    for key in ("usage", "task_usage", "judge_usage"):
        if key in item:
            value = item[key]
            result[key] = None if value is None else {
                name: mapping(value).get(name) for name in ("input_tokens", "cached_input_tokens", "output_tokens")}
    return result


def export(out: Path, destination: Path) -> dict[str, object]:
    """Write the winner as a private patch and a redacted report. Never apply it."""
    record = open_record(out / "run.json")
    if record.get("phase") != "complete":
        raise ValueError("export requires a completed run")
    if record.get("engine_hash") != _engine_hash():
        raise ValueError("evaluator differs from the frozen record; create a new run")
    if "judge" in record:
        judge_model = _text_field(record, "judge_model" if "judge_model" in record else "model")
        if record["judge"] != judge_identity(judge_model):
            raise ValueError("judge differs from the frozen record; create a new run")
    if not os.path.lexists(destination) and _inside(destination, Path(_text_field(record, "target_root"))):
        raise CodedError("export-into-target", "the export destination must be outside the target skill directory")
    seed, candidate = candidate_files(_field(record, "seed")), candidate_files(_field(record, "winner"))
    lines: list[str] = []
    for name in seed:
        if seed[name] != candidate[name]:
            lines.extend(difflib.unified_diff(seed[name].splitlines(keepends=True),
                                              candidate[name].splitlines(keepends=True),
                                              fromfile="a/" + name, tofile="b/" + name))
    patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
    destination.mkdir(mode=0o700)
    descriptor = os.open(destination / "candidate.patch", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        _ = stream.write(patch)
    report = summary(record) | {"sharing": "private-local-only",
                                "outcomes": [_export_outcome(item) for item in _outcomes(record)], "cost_usd": None}
    write(destination / "report.json", report)
    return {"export": str(destination), "sharing": "private-local-only", "installed": False}

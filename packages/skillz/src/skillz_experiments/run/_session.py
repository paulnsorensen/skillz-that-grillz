"""The per-run state machine, its provider seam, outcome records, and fault helpers.
"""
from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter
import hashlib
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Protocol, TypeVar, cast, final, get_args

from skillz_experiments._audit import identity as judge_identity
from skillz_experiments._budget import (
    MAX_CONCURRENT_CALLS,
    SECONDS_PER_CALL,
    Budget,
    SEARCH_ROLES,
    BudgetExhausted,
    CallTimeout,
    ceilings,
)
from skillz_experiments._candidate import Candidate, candidate_files, load_frozen
from skillz_experiments._cases import Case, CodedError, Split, digest, load_cases, mapping
from skillz_experiments._contract import parse
from skillz_experiments.evaluation import CheckMemo, task_tokens
from skillz_experiments._records import open_record, read, write
from skillz_experiments._wedge_targets import Rebuilder, reopen
from skillz_experiments.gate import Statistics, Verdict, close, holdout_deltas
from skillz_experiments.gate import verdict as gate_verdict
from skillz_experiments.harness import (
    ClaudeOptions,
    Configuration,
    EnvironmentDiffers,
    Harness,
    stop_kind,
)
from skillz_experiments.run._patch import build_patch
from skillz_experiments.run._plan import case_calls, plan_estimate, size_plan
from skillz_experiments.run._replay import STDERR_LIMIT, apply_patches, differences, prepare_copy
from skillz_experiments.run._replay import components as replay_components
from skillz_experiments.run._replay import drift as replay_drift
from skillz_experiments.run._stop import Stop
from skillz_experiments.search import HYPOTHESIS, Denylist, Edit, overshoot
from skillz_experiments.search import is_reflection as _is_reflection
from skillz_experiments.search import Progress, Signal, assess
from skillz_experiments.search import accept as accept_proposal
from skillz_experiments.search import request as reflection_request
from skillz_experiments.search import check as check_leakage
from skillz_experiments.search import attempts as build_attempts
from skillz_experiments.search import protected as protected_cases
from skillz_experiments.search import search as pareto_search

# Seconds that the wall clock may step back between prepare and resume, for example after an NTP step.
CLOCK_TOLERANCE = 5.0


HarnessName = str

LEAKAGE_REJECTED = "candidate-leakage-rejected"

# The phases after the gate. A run in one of them makes no model call without a deadline check.
POST_GATE_PHASES = ("gated", "replayed")

# The search stops with `search-faulted` when at least this many search evaluations finished and more than half failed,
# or when this many of the newest ones failed in a row. The values are defaults, not measured limits.
FAULT_MIN_FINISHED = 4
FAULT_STREAK = 5
_FAULT_REASON = re.compile(r"\bfails: ([a-z][a-z-]*)")

# The modules whose source decides a score, next to every file in `gate/`. Follow a module when a later wave moves it into a slice.
SCORING_FILES = ("evaluation/_evaluator.py", "evaluation/_memo.py", "_evaluation.py", "_graders.py", "_audit.py", "_contract.py")


def _faulted_reflection(item: Mapping[str, object]) -> bool:
    """Return whether a reflection row records a skipped or timed-out call, which proposes nothing."""
    return item.get("status") in ("skipped", "timeout") and "proposed" not in item


def _failed_outcome(case: Case, candidate: Candidate, error: Exception, *, arm: str = "search",
                    repeat: int | None = None) -> dict[str, object]:
    """Return the outcome that records a failed evaluation. Its 0.0 score is a placeholder that no decision reads.

    A holdout or replay failure names its arm and repeat.
    """
    outcome: dict[str, object] = {"arm": arm, "split": case.split, "case_id": case.identifier, "score": 0.0,
                                  "status": "evaluation-failed", "failure": str(error)}
    outcome["reason"] = _fault_reason(error)
    if arm == "search":
        outcome["search_candidate"] = candidate.identity
    if repeat is not None:
        outcome["repeat"] = repeat
    return outcome


def _fault_reason(error: Exception) -> str:
    """Return the stable reason of a failed evaluation: `timeout`, the reason that an adapter names after `fails:`, else `error`."""
    if isinstance(error, CallTimeout):
        return "timeout"
    match = _FAULT_REASON.search(str(error))
    return match.group(1) if match else "error"


def _is_failed(item: Mapping[str, object]) -> bool:
    return item.get("status") == "evaluation-failed"


def _reason_counts(failed: Sequence[Mapping[str, object]]) -> dict[str, int]:
    return dict(sorted(Counter(str(item.get("reason", "error")) for item in failed).items()))


def search_counts(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Return the scored and failed counts of the search evaluations in `rows`, and the failed counts by reason."""
    failed = [item for item in rows if _is_failed(item)]
    return {"scored": len(rows) - len(failed), "failed": len(failed), "failed_by_reason": _reason_counts(failed)}


def _search_fault(rows: Sequence[Mapping[str, object]]) -> CodedError | None:
    """Return `search-faulted` when the search evaluations in `rows` fail in a majority or in a streak, else None."""
    failed = [item for item in rows if _is_failed(item)]
    streak = 0
    for item in reversed(rows):
        if not _is_failed(item):
            break
        streak += 1
    majority = len(rows) >= FAULT_MIN_FINISHED and 2 * len(failed) > len(rows)
    if not (majority or streak >= FAULT_STREAK):
        return None
    return CodedError("search-faulted", f"{len(failed)} of {len(rows)} search evaluations failed ({_reason_counts(failed)}; "
                      + f"the newest: {failed[-1].get('failure')}); wait for the provider, then resume the run in the same directory")


def _baseline_rejected(detail: str | None = None) -> Stop:
    return Stop("baseline-contract-rejected", "original candidate fails the frozen native/helper contract",
                {"next": "fix the skill or its contract, then start a new run in a new directory"}
                | cast(dict[str, object], {} if detail is None else {"detail": detail}))


def _rejection_detail(outcome: dict[str, object]) -> str | None:
    """Return the rejection reason of `outcome` when it is a string. A value read back from run.json can be any type."""
    detail = outcome.get("rejection")
    return detail if isinstance(detail, str) else None


def _case_ids(feedback: Mapping[str, Sequence[Mapping[str, object]]]) -> list[str]:
    """Return the train case ids in the feedback that the proposer saw, first seen first."""
    names = (record.get("case_id") for records in feedback.values() for record in records)
    return list(dict.fromkeys(name for name in names if isinstance(name, str)))


def _feedback(result: dict[str, object], case: Case) -> tuple[float, dict[str, object]]:
    """Return the GEPA score and feedback of one search outcome."""
    if _is_failed(result):
        # GEPA needs a number. The session ignores this 0.0 in every decision and drops the record from reflection feedback.
        return 0.0, {"unscored": True, "request": case.request}
    score = unit_score(result["score"])
    feedback: dict[str, object] = {"task_correct": score, "request": case.request, "case_id": case.identifier}
    if "scores" in result:
        feedback["grader_scores"] = result["scores"]
    return score, feedback


def is_inside(path: Path, root: Path) -> bool:
    """Return whether `path` resolves, through any symlink, to `root` or below it."""
    return path.resolve().is_relative_to(root.resolve())


class _Provider(Protocol):
    def preflight(self) -> dict[str, object]: ...
    def close(self) -> None: ...
    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: CheckMemo) -> dict[str, object]: ...
    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]: ...
    def check_name(self, candidate: Candidate) -> None: ...
    def check_candidate(self, candidate: Candidate) -> bool: ...

    @property
    def rejection(self) -> str | None: ...


Factory = Callable[[str, Budget, Callable[[], None]], _Provider]


def engine_hash() -> str:
    package = Path(__file__).parent.parent
    return digest({path.relative_to(package).as_posix(): path.read_text() for path in package.rglob("*.py")})


def _differences(recorded: object, observed: object, name: str = "") -> list[str]:
    """Name each preflight evidence key whose value differs, with both values. Keys sit in dotted paths."""
    if isinstance(recorded, dict) and isinstance(observed, dict):
        before, after = cast(dict[str, object], recorded), cast(dict[str, object], observed)
        return [line for key in sorted(set(before) | set(after))
                for line in _differences(before.get(key), after.get(key), f"{name}.{key}" if name else key)]
    return [] if recorded == observed else [f"{name} (recorded {str(cast(object, recorded))[:80]!r}, observed {str(observed)[:80]!r})"]


def leakage_digest(package: Path) -> str:
    """Digest the source of the leakage check. A rule change decides which proposals get scored, so it starts a new cohort."""
    return digest({"search/_leakage.py": (package / "search" / "_leakage.py").read_text()})


def scoring_digest(package: Path) -> str:
    """Digest the source of the scoring modules under `package`: every file in `gate/`, then `SCORING_FILES`.

    A scoring change gives a new digest. An edit to `search/`, `run/`, or `harness/` keeps it.
    """
    paths = [*sorted((package / "gate").rglob("*.py")), *(package / name for name in SCORING_FILES)]
    return digest({path.relative_to(package).as_posix(): path.read_text() for path in paths})


def field(record: dict[str, object], key: str) -> object:
    if key not in record:
        raise ValueError(f"run record lacks {key}")
    return record[key]


def text_field(record: dict[str, object], key: str) -> str:
    value = field(record, key)
    if not isinstance(value, str):
        raise ValueError(f"run record field {key} must be text")
    return value


def judge_flag(record: dict[str, object]) -> str | None:
    """Return the frozen `--judge-model` value, or None when the first run passed none."""
    value = record.get("judge_model_flag")
    if value is not None and not isinstance(value, str):
        raise ValueError("run record field judge_model_flag must be text or null")
    return value


def _number_field(record: dict[str, object], key: str) -> float:
    value = field(record, key)
    if type(value) not in (int, float):
        raise ValueError(f"run record field {key} must be a number")
    return cast(float, value)


def _int_field(record: dict[str, object], key: str) -> int:
    value = field(record, key)
    if type(value) is not int or value < 0:
        raise ValueError(f"run record field {key} must be a nonnegative integer")
    return value


def _count_map(value: object) -> dict[str, float]:
    """Load a recorded map of role to count or seconds. A missing map is empty."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("run record role counts must map roles to nonnegative numbers")
    loaded: dict[str, float] = {}
    for key, item in cast(dict[object, object], value).items():
        if not isinstance(key, str) or type(item) not in (int, float) or cast(float, item) < 0:
            raise ValueError("run record role counts must map roles to nonnegative numbers")
        loaded[key] = float(cast(float, item))
    return loaded


def record_outcomes(record: dict[str, object]) -> list[dict[str, object]]:
    value = field(record, "outcomes")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in cast(list[object], value)):
        raise ValueError("run record field outcomes must be a list of objects")
    return cast(list[dict[str, object]], value)


def unit_score(value: object) -> float:
    """Clamp a provider score to 0..1. A NaN counts as 0."""
    return min(1.0, max(0.0, float(cast(float, value))))


Outcome = TypeVar("Outcome", bound=Mapping[str, object])


def scored_holdout(outcomes: Iterable[Outcome], arms: Collection[str]) -> Iterator[Outcome]:
    """Yield the holdout outcomes of `arms` in order. An `evaluation-failed` outcome is unscored and drops out."""
    for item in outcomes:
        if item.get("split") == "holdout" and item.get("arm") in arms and item.get("status") != "evaluation-failed":
            yield item


def holdout_scores(outcomes: Sequence[Mapping[str, object]], arm: str) -> dict[str, dict[int, float]]:
    """Return the scored holdout scores of `arm` by case and repeat. The newest outcome of a case and repeat wins."""
    found: dict[str, dict[int, float]] = {}
    for item in scored_holdout(outcomes, (arm,)):
        found.setdefault(str(item.get("case_id")), {})[cast(int, item.get("repeat"))] = unit_score(item["score"])
    return found


def by_repeat_order(scores: Mapping[str, Mapping[int, float]]) -> dict[str, list[float]]:
    """Return the scores of each case as a list in repeat order."""
    return {case_id: [by_repeat[repeat] for repeat in sorted(by_repeat)] for case_id, by_repeat in scores.items()}


@final
class Session:
    def __init__(self, out: Path, model: str, adapter: HarnessName, factory: Factory | None,
                 supplied: Configuration | None, *, statistics: Statistics) -> None:
        self.out = out
        self.lock = threading.RLock()
        self.record = open_record(out / "run.json")
        self.statistics = statistics
        if _phase(self.record) not in _PHASES:
            raise Stop("run-record-tampered", "the recorded phase is unknown; create a new run", {})
        self.contract = parse(field(self.record, "contract"), text_field(self.record, "contract_source"))
        if self.contract.identity != text_field(self.record, "contract_hash"):
            raise ValueError("contract differs from the frozen record; create a new run")
        imported, judged = self._import_cases()
        self.cases = [case for case in imported if case.eligible]
        editable = field(self.record, "editable")
        if not isinstance(editable, list) or not all(isinstance(name, str) for name in cast(list[object], editable)):
            raise ValueError("run record field editable must be a list of text")
        self.seed = Candidate(candidate_files(field(self.record, "seed")), tuple(cast(list[str], editable)), self.contract,
                              frozen=self._seed_frozen())
        self.edit: Edit = cast(Edit, text_field(self.record, "edit"))
        self.outcomes = record_outcomes(self.record)
        options = ClaudeOptions.parse(mapping(self.record.get("claude_options", {})))
        configuration = supplied if supplied is not None else (
            Configuration.split(adapter, model, judge_flag(self.record), options) if factory is None else None)
        self.sizing = options if configuration is None else configuration.sizing_options()
        self._freeze_identities(model, configuration, judged)
        self._freeze_compatibility(configuration)
        self.budget = self._open_budget()
        self._fault: CodedError | None = None
        self._ended_proposal: str | None = None
        self._search_cases = {case.identifier: case for case in self.cases if case.split != "holdout"}
        # Rebuilt from the frozen cases on every open. It never reaches `run.json` or an export.
        self._denylist = Denylist.build(self._search_cases.values())
        self._leak_rules: dict[str, str | None] = {}
        # One contract check per candidate identity in this session (ADR-013). It never reaches `run.json`.
        self._checks = CheckMemo()
        # Recorded search outcomes that a resumed search reuses, one per earlier call. `search` fills it.
        self._reusable: dict[tuple[str, str, str], list[dict[str, object]]] = {}
        # The live search evaluations of the running search. Reused outcomes do not count.
        self._live = 0
        # The count of recorded search evaluations when this session started its search. The fault rule counts after it.
        self._fault_base = 0
        # Set before the provider gets `self.checkpoint`, which reads it.
        self._leak_rejections = self._recorded_rejections()
        self.provider: _Provider = (configuration.create(model, self.budget, self.checkpoint, out, self.contract.surface,
                                                                  self.seed.files) if configuration is not None
                                    else cast(Factory, factory)(model, self.budget, self.checkpoint))
        self.rebuilder = self._open_rebuilder()

    def _seed_frozen(self) -> dict[str, bytes]:
        """Load the frozen seed files from the run directory. A missing or changed file is tampering."""
        try:
            return load_frozen(mapping(field(self.record, "seed_frozen")), self.out / "frozen")
        except CodedError as error:
            raise Stop(error.code, str(error), {}) from None

    def _recorded_rejections(self) -> dict[str, set[str]]:
        """Load the rejected proposal digests per rule from the record. They hold no case text."""
        recorded = mapping(self.record.get("leakage_rejected", {}))
        found: dict[str, set[str]] = {}
        for rule, keys in recorded.items():
            if not isinstance(keys, list) or not all(isinstance(key, str) for key in cast(list[object], keys)):
                raise ValueError("run record field leakage_rejected must map rules to lists of text")
            found[rule] = set(cast(list[str], keys))
        return found

    def _open_rebuilder(self) -> Rebuilder | None:
        """Reopen the site layers of the editable own targets. Return None when no target has editable sources."""
        try:
            return reopen(Path(text_field(self.record, "target_root")), mapping(field(self.record, "own_targets")),
                          self.seed, self.out / "site")
        except CodedError as error:
            raise Stop(error.code, str(error), {"next": "start a new run in a new directory"}) from None

    def _built(self, candidate: Candidate) -> Candidate:
        """Return `candidate` with the host-built `.pyz` of each own target that its sources changed."""
        return candidate if self.rebuilder is None else self.rebuilder.apply(candidate)

    @property
    def phase(self) -> str:
        return text_field(self.record, "phase")

    def _import_cases(self) -> tuple[list[Case], bool]:
        """Load the frozen cases. Return them and whether any case needs a judge."""
        if text_field(self.record, "dataset_hash") != digest(read(self.out / "cases.json")):
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
                raise CodedError("harness-changed", "harness configuration differs from the frozen record; create a new run")
        if judged:
            judge_model = configuration.roles["judge"].model if configuration is not None else model
            _ = self.record.setdefault("judge_model", judge_model)
            _ = self.record.setdefault("judge", judge_identity(judge_model))
            if self.record["judge"] != judge_identity(judge_model):
                raise ValueError("judge differs from the frozen record; create a new run")
        if self.seed.identity != text_field(self.record, "seed_hash"):
            raise ValueError("seed differs from the frozen record")
        _ = self.record.setdefault("engine_hash", engine_hash())
        if self.record["engine_hash"] != engine_hash():
            raise ValueError("evaluator differs from the frozen record; create a new run")

    def _freeze_compatibility(self, configuration: Configuration | None) -> None:
        """Freeze the compatibility fingerprint once. It runs after `_freeze_identities`, so `judge_model` is final.

        A supplied configuration decides the task fields through its task role, which can override the CLI flags.
        """
        if "compatibility" in self.record:
            return
        task = configuration.roles["task"] if configuration is not None else None
        splits = {split: sorted(case.identifier for case in self.cases if case.split == split)
                  for split in ("train", "validation")}
        fields: dict[str, object] = {
            "contract_hash": text_field(self.record, "contract_hash"), "skill": self.contract.skill,
            "adapter": task.adapter if task else text_field(self.record, "adapter"),
            "task_model": task.model if task else text_field(self.record, "model"),
            "judge_model": self.record.get("judge_model"),
            "claude_options": task.options.data() if task else self.record.get("claude_options", {}),
            "edit": text_field(self.record, "edit"),
            "repeats": field(self.record, "repeats"), "statistics": field(self.record, "statistics"),
            "split_digest": digest(splits), "scoring_digest": scoring_digest(Path(__file__).parent.parent),
            "leakage_digest": leakage_digest(Path(__file__).parent.parent)}
        self.record["compatibility"] = {"fingerprint": digest(fields), "fields": fields}

    def _open_budget(self) -> Budget:
        """Rebuild the approved budget. A resumed run keeps `approved=True` and the calls it already spent.

        Elapsed time is the larger of the monotonic and the wall-clock span since prepare. A reboot resets the
        monotonic clock, so a negative monotonic span falls back to the wall clock. The wall clock may step back
        by up to `CLOCK_TOLERANCE` seconds. The gate time reserve applies only inside `search`.
        """
        budget = mapping(field(self.record, "budget"))
        sandbox_seconds = self._verify_budget(budget)
        calls = _int_field(self.record, "calls")
        by_role = ({role: int(count) for role, count in _count_map(self.record.get("calls_by_role")).items()}
                   if "calls_by_role" in self.record else {"unattributed": calls} if calls else {})
        seconds_by_role = _count_map(self.record.get("seconds_by_role"))
        retries = _int_field(self.record, "retries") if "retries" in self.record else 0
        call_seconds = SECONDS_PER_CALL + sandbox_seconds
        wall = time.time() - _number_field(self.record, "started")
        if not -CLOCK_TOLERANCE <= wall < math.inf:
            raise CodedError("clock-skew", "the wall clock is more than "
                             + f"{CLOCK_TOLERANCE:g} seconds earlier than the run start; set the clock right, or create a new run")
        monotonic = time.monotonic() - _number_field(self.record, "started_monotonic")
        elapsed = max(wall, monotonic, 0.0)
        seconds = _number_field(budget, "seconds")
        approved, journey = budget.get("approved") is True, budget.get("journey") is True
        if seconds - elapsed <= 0:
            if _phase(self.record) not in POST_GATE_PHASES:
                raise BudgetExhausted("global deadline exhausted")
            # A gated or replayed run makes no model call without a deadline check, so the replay ends on its own.
            return Budget.expired(_int_field(budget, "maximum"), reserve=_int_field(budget, "reserve"), calls=calls,
                                  approved=approved, journey=journey, calls_by_role=by_role,
                                  seconds_by_role=seconds_by_role, retries=retries, call_seconds=call_seconds)
        return Budget(_int_field(budget, "maximum"), seconds - elapsed, reserve=_int_field(budget, "reserve"),
                      calls=calls, approved=approved, journey=journey, calls_by_role=by_role,
                      seconds_by_role=seconds_by_role, retries=retries, call_seconds=call_seconds)

    def _verify_budget(self, budget: dict[str, object]) -> int:
        """Stop when the recorded budget differs from the plan that the frozen cases, repeats, and edit give.

        Return the sandbox seconds of one evaluation, which add to the per-call time estimate.
        """
        count = {split: sum(case.split == split for case in self.cases) for split in cast(tuple[str, ...], get_args(Split))}
        repeats = _int_field(self.record, "repeats")
        plan = size_plan(self.contract, self.cases, repeats, self.sizing)
        expected = plan_estimate(plan, repeats, count["holdout"])
        recorded = mapping(field(self.record, "estimate"))
        maximum = max(plan.sized.calls, 1)
        seconds = _number_field(budget, "seconds")
        if "reserve_seconds" not in budget or ("sandbox_seconds" in expected and "sandbox_seconds" not in recorded):
            raise ValueError("run was prepared by an older runner; create a new run")

        def differs(found: object, wanted: object) -> bool:
            return isinstance(found, bool) or found != wanted

        if (any(differs(recorded.get(key), value) for key, value in expected.items())
                or differs(budget.get("maximum"), maximum)
                or differs(budget.get("reserve"), min(plan.reserve, maximum))
                or differs(budget.get("reserve_seconds"), plan.reserve_seconds)
                or (budget.get("journey") is True) != plan.sized.journey
                or not 0 < seconds <= ceilings(plan.sized.journey)[1]):
            raise Stop("run-record-tampered", "the recorded budget differs from the plan of the frozen run; create a new run", {})
        return plan.sandbox_seconds

    def checkpoint(self) -> None:
        with self.lock:
            spent = self.budget.snapshot()
            self.record["calls"] = spent.calls
            self.record["retries"] = spent.retries
            self.record["calls_by_role"] = dict(sorted(spent.calls_by_role.items()))
            self.record["seconds_by_role"] = {role: round(seconds, 3) for role, seconds in sorted(spent.seconds_by_role.items())}
            self.record["leakage_rejected"] = {rule: sorted(keys) for rule, keys in sorted(self._leak_rejections.items())}
            write(self.out / "run.json", self.record)

    def cases_for(self, split: Split) -> list[Case]:
        return sorted((case for case in self.cases if case.split == split), key=lambda case: case.identifier)

    def preflight(self) -> None:
        if self.record.get("phase") in POST_GATE_PHASES:
            try:
                _ = self.budget.remaining()
            except BudgetExhausted:
                # Past the deadline the replay makes no model call, so the live preflight has nothing to protect.
                return
        recorded = self.record.get("preflight")
        before = self.budget.calls
        try:
            preflight = (self.provider.preflight(cast(dict[str, object], recorded) if isinstance(recorded, dict) else None)
                         if isinstance(self.provider, Harness) else self.provider.preflight())
        finally:
            claimed = self.budget.calls - before
            with self.lock:
                earlier = self.record.get("preflight_calls", 0)
                self.record["preflight_calls"] = (earlier if type(earlier) is int else 0) + claimed
                if claimed:
                    # A preflight that fails after its claims is not checkpointed later, so save the count now.
                    self.checkpoint()
        environment_hash = preflight.get("environment_hash")
        _ = self.record.setdefault("environment_hash", environment_hash)
        if self.record["environment_hash"] != environment_hash:
            before = mapping(cast(dict[str, object], recorded)) if isinstance(recorded, dict) else {}
            changed = _differences(before.get("roles", before), preflight.get("roles", preflight))
            evidence = "; ".join(changed[:6]) or "the environment hash only"
            raise EnvironmentDiffers("runtime environment differs from the frozen record; changed evidence: "
                                     + evidence + "; restore the first-run environment and resume, "
                                     + "or start a new run")
        self.record["preflight"] = preflight
        if isinstance(self.provider, Harness):
            self.record |= self.provider.run_facts
        self.checkpoint()

    def evaluate_case(self, candidate: Candidate, case: Case, arm: str, *, holdout: bool = False,
                      repeat: int | None = None) -> dict[str, object]:
        """Score one case. The model call runs outside the lock; the record update runs inside it."""
        self._raise_fault()
        try:
            result = self.provider.evaluate(candidate, case, holdout=holdout, checks=self._checks)
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

    def _leak_rule(self, components: dict[str, str]) -> str | None:
        """Return the leakage rule that the proposal breaks, else None. Each distinct proposal is checked once per session."""
        key = digest(components)
        with self.lock:
            if key in self._leak_rules:
                return self._leak_rules[key]
        leak = check_leakage(self._denylist, self.seed.files, components)
        rule = None if leak is None else leak.rule
        with self.lock:
            first = key not in self._leak_rules
            rule = self._leak_rules.setdefault(key, rule)
            if first and rule is not None:
                self._leak_rejections.setdefault(rule, set()).add(key)
                self.checkpoint()
            return rule

    def _evaluate_example(self, components: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        case = self._search_cases.get(cast(str, example))
        if case is None:
            raise ValueError("holdout must not enter optimization")
        candidate = self._admit(components)
        if not isinstance(candidate, Candidate):
            return 0.0, {"task_correct": 0.0, "request": case.request, "case_id": case.identifier} | candidate
        recorded = self._reuse(candidate, case)
        if recorded is not None:
            self._keep_if_best(candidate, case)
            return _feedback(recorded, case)
        with self.lock:
            self._live += 1
        try:
            result = self.evaluate_case(candidate, case, "search")
        except CodedError:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            result = _failed_outcome(case, candidate, error)
            with self.lock:
                self.outcomes.append(result)
                self.checkpoint()
            self._stop_if_faulted()
        self._keep_if_best(candidate, case)
        return _feedback(result, case)

    def _stop_if_faulted(self) -> None:
        """Latch and raise `search-faulted` when the recorded search evaluations meet the failure rule."""
        with self.lock:
            fault = _search_fault(self._search_outcomes()[self._fault_base:])
        if fault is not None:
            self._latch(fault)
            raise fault

    def _reuse(self, candidate: Candidate, case: Case) -> dict[str, object] | None:
        """Return the next recorded outcome of `candidate` on `case`, or None. Each recorded outcome serves one evaluation."""
        with self.lock:
            queue = self._reusable.get((candidate.identity, case.split, case.identifier))
            return queue.pop(0) if queue else None

    def _recorded_for_reuse(self) -> int:
        """Load the scored search outcomes of the record into the reuse queues. Return their count.

        A failed evaluation stays out, so a resume tries it again.
        """
        with self.lock:
            self._reusable.clear()
            self._live = 0
            self._fault_base = len(self._search_outcomes())
            for item in self._search_outcomes():
                if "search_candidate" in item and item.get("status") != "evaluation-failed":
                    key = (cast(str, item["search_candidate"]), cast(str, item["split"]), cast(str, item["case_id"]))
                    self._reusable.setdefault(key, []).append(item)
            return sum(len(queue) for queue in self._reusable.values())

    def _share_spent(self, allowance: int) -> bool:
        """Return whether the live search evaluations of this search reach `allowance`. GEPA checks it before each iteration."""
        with self.lock:
            return self._live >= allowance

    def _admit(self, components: dict[str, str]) -> Candidate | dict[str, object]:
        """Return the built candidate of `components`, or the rejection that the leakage check or the build gives."""
        rule = self._leak_rule(components)
        if rule is not None:
            return {"rejected": LEAKAGE_REJECTED, "reason": LEAKAGE_REJECTED, "rule": rule}
        try:
            return self._built(self.seed.changed(components))
        except ValueError as error:
            rejected: dict[str, object] = {"rejected": str(error)}
            if isinstance(error, CodedError):
                rejected["reason"] = error.code
            return rejected

    def _validation_outcomes(self) -> list[dict[str, object]]:
        """Return the outcomes of search candidates on validation cases."""
        return [item for item in self.outcomes if _is_search_validation(item)]

    def _validation_means(self, validation: list[str]) -> dict[str, float]:
        """Return the mean score of each search candidate that has a score on every validation case."""
        return _complete_means(_add_validation_scores({}, self.outcomes), set(validation))

    def _validation_tokens(self, validation: list[str], identities: tuple[str, ...]) -> dict[str, float | None]:
        """Return the mean task tokens of each listed candidate that has an outcome on every validation case.

        A candidate with a missing count on any case maps to None.
        """
        counts: dict[str, dict[str, int | None]] = {}
        for item in self._validation_outcomes():
            if item["search_candidate"] in identities:
                counts.setdefault(cast(str, item["search_candidate"]), {})[cast(str, item["case_id"])] = task_tokens(item)
        return {identity: _mean_tokens(list(by_case.values())) for identity, by_case in counts.items()
                if set(by_case) == set(validation)}

    def _tied_cheaper(self, validation: list[str], challenger: str, incumbent: str) -> bool:
        """Return whether a token field is set and the challenger has known, lower mean validation task tokens."""
        if not self.statistics.token_rule:
            return False
        tokens = self._validation_tokens(validation, (challenger, incumbent))
        new, old = tokens.get(challenger), tokens.get(incumbent)
        return new is not None and old is not None and new < old

    def _beats(self, validation: list[str], mean: float, other: float, challenger: str, incumbent: str) -> bool:
        """Return whether `mean` is above `other`, or ties it and the challenger is cheaper."""
        if close(mean, other):
            return self._tied_cheaper(validation, challenger, incumbent)
        return mean > other

    def _keep_if_best(self, candidate: Candidate, case: Case) -> None:
        """Record the files of the best non-seed candidate when it completes the validation set. The record stays small.

        A mean tie goes to lower mean task tokens while a token field is set.
        """
        if case.split != "validation" or candidate.identity == self.seed.identity:
            return
        with self.lock:
            validation = [item.identifier for item in self.cases_for("validation")]
            mean = self._validation_means(validation).get(candidate.identity)
            kept = self.record.get("best_validated")
            if mean is None:
                return
            if isinstance(kept, dict):
                kept_record = cast(dict[str, object], kept)
                kept_mean = _number_field(kept_record, "mean")
                if not self._beats(validation, mean, kept_mean, candidate.identity, text_field(kept_record, "identity")):
                    return
            self.record["best_validated"] = {"identity": candidate.identity, "mean": mean,
                                             "files": {name: candidate.files[name] for name in self.seed.editable}}
            self.checkpoint()

    def _propose(self, candidate: dict[str, str], feedback: Mapping[str, Sequence[Mapping[str, object]]],
                 components: list[str]) -> dict[str, str]:
        self._raise_fault()
        asked = hashlib.sha256(repr((sorted(candidate.items()), feedback)).encode()).hexdigest()
        with self.lock:
            if self._ended_proposal == asked:
                # GEPA retries a failed proposal once. The retry must not run a second reflection call.
                self._ended_proposal = None
                raise ValueError("the reflection of this proposal ended with no candidate")
            stall = self._stall()
            progress = Progress(spent=len(self._search_outcomes()), planned=self._search_ceiling(),
                                proposal=1 + sum(1 for item in self._reflections() if not _faulted_reflection(item)), stalled=stall.streak, advice=stall.advice)
            history = build_attempts(self.outcomes, self._seed_ref(), self._validation_ids())
            protect = protected_cases(self.outcomes, self._best_identity())
        feedback = {name: [item for item in items if not _is_unscored(item)] for name, items in feedback.items()}
        if not any(feedback.values()):
            with self.lock:
                self._ended_proposal = asked
                self.outcomes.append({"arm": "search", "split": "reflection", "status": "skipped",
                                      "reason": "no-scored-feedback"})
                self.checkpoint()
            raise ValueError("every feedback record is unscored; the proposal ends with no candidate")
        prompt, schema = reflection_request(self.edit, candidate, feedback, components, progress=progress,
                                            cheaper=self.statistics.token_rule, history=history, protect=protect)
        try:
            result = self.provider.invoke(prompt, schema=schema)
        except CodedError as error:
            self._latch(error)
            raise
        except CallTimeout as error:
            with self.lock:
                self.outcomes.append({"arm": "search", "split": "reflection", "status": "timeout", "reason": "timeout",
                                      "failure": str(error)})
                self._ended_proposal = asked
                self.checkpoint()
            raise ValueError("the reflection call timed out; the proposal ends with no candidate") from error
        refusal: ValueError | None = None
        proposal: dict[str, str] = {}
        edits = 0
        hypothesis: str | None = None
        hypothesis_dropped: str | None = None
        try:
            proposal, edits, hypothesis, hypothesis_dropped = accept_proposal(result["answer"], components, candidate)
        except ValueError as error:
            refusal = error
        if hypothesis is not None:
            # The hypothesis never enters the components, so a leak drops the hypothesis and keeps the proposal.
            leak = check_leakage(self._denylist, {}, {HYPOTHESIS: hypothesis})
            if leak is not None:
                hypothesis, hypothesis_dropped = None, leak.rule
        with self.lock:
            outcome: dict[str, object] = {"arm": "search", "split": "reflection", "usage": result.get("usage"),
                                          "latency_seconds": result.get("latency_seconds"), "parent": digest(candidate),
                                          "components_digest": None if refusal is not None else digest(proposal),
                                          "minibatch": _case_ids(feedback), "hypothesis": hypothesis,
                                          "hypothesis_dropped": hypothesis_dropped,
                                          "rejected": None if refusal is None else "reflection-shape"}
            if refusal is not None:
                outcome["rejected_coded"] = True
            if edits > progress.budget():
                # The budget is advisory. The count shows whether enforcement is worth the wasted reflection calls.
                outcome["over_budget"] = True
            self.outcomes.append(outcome)
            self.checkpoint()
        if refusal is not None:
            raise refusal
        try:
            admitted = self._admit(proposal)
        except Exception as error:
            # GEPA catches every proposer exception, retries once, and then skips the proposal. The latch ends the run at the next fault check.
            fault = CodedError("proposal-build-failed", f"the proposal build failed: {error}; fix the cause in that error, then resume")
            with self.lock:
                # The stall signal reads this proposal as unknown, because its candidate never got a score.
                outcome["status"] = "proposal-build-failed"
                outcome["rejected"] = "proposal-build-failed"
                outcome["rejected_coded"] = True
                self.checkpoint()
            self._latch(fault)
            raise fault from error
        with self.lock:
            if isinstance(admitted, Candidate):
                outcome["proposed"] = admitted.identity
            else:
                outcome["rejected"] = str(admitted.get("reason") or admitted["rejected"])
                outcome["rejected_coded"] = admitted.get("reason") is not None
            self.checkpoint()
        return proposal

    def _seed_ref(self) -> tuple[str, str]:
        """Return the digest of the seed component texts and the seed identity. The digest names the parent of a first proposal."""
        return digest({name: self.seed.files[name] for name in self.seed.editable}), self.seed.identity

    def _validation_ids(self) -> list[str]:
        return [case.identifier for case in self.cases_for("validation")]

    def _best_identity(self) -> str:
        """Return the identity of the best validated candidate, or the seed when the seed is best."""
        return self._best_validated(self._validation_ids()).identity

    def _text_dropped(self) -> dict[str, int]:
        """Return the count of each rule that dropped a hypothesis. The recorded outcomes hold it, so a resume keeps it."""
        rules = [cast(str, item["hypothesis_dropped"]) for item in self._reflections()
                 if isinstance(item.get("hypothesis_dropped"), str)]
        return dict(sorted(Counter(rules).items()))

    def _stall(self) -> Signal:
        """Return the stall signal that the recorded outcomes give. The caller holds the lock."""
        validation = {case.identifier for case in self.cases_for("validation")}
        return assess(stall_observations(self.outcomes, validation), band=self.statistics.min_gain)

    def _stalled(self) -> bool:
        """Return whether the stall signal is at `stop`. GEPA calls this before each iteration."""
        with self.lock:
            return self._stall().level == "stop"

    def _reflections(self) -> list[dict[str, object]]:
        """Return the recorded reflection calls, one per proposal."""
        return [item for item in self.outcomes if _is_reflection(item)]

    def _over_budget(self) -> int:
        """Return the proposals above the edit budget stated in their prompt."""
        return sum(1 for item in self._reflections() if item.get("over_budget") is True)

    def _search_ceiling(self) -> int:
        """Return the GEPA call budget: the estimate less the overshoot margin. The last iteration can pass it by up to that margin."""
        planned = _int_field(mapping(field(self.record, "estimate")), "metric_calls")
        return planned - overshoot(len(self.cases_for("validation")))

    def _latch(self, error: CodedError) -> None:
        """Keep one CodedError from a model call. A terminal code replaces a held fault with another code. A coded fault replaces a held budget stop. Otherwise the first fault stays."""
        with self.lock:
            held = self._fault
            if held is None:
                replace = True
            elif stop_kind(error.code) == "terminal":
                replace = stop_kind(held.code) != "terminal"
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
        if stop_kind(code) == "terminal":
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
        self.provider.check_name(self.seed)

    def check_seed(self) -> None:
        """Stop before any search call when the seed fails the local candidate contract check."""
        passed, rejection = self._checks.check(self.provider, self.seed)
        if not passed:
            raise _baseline_rejected(rejection)

    def search(self) -> None:
        """Run the one GEPA search over train and validation cases. Holdout cases never enter it."""
        files = {name: self.seed.files[name] for name in self.seed.editable}
        train = [case.identifier for case in self.cases_for("train")]
        validation = [case.identifier for case in self.cases_for("validation")]
        spent = len(self._search_outcomes())
        allowance = self._search_ceiling() - spent
        reason = "search-share-spent-best-validated"
        timed_out = False
        if allowance >= 1 and self._stalled():
            # A run that stalled before a resume skips GEPA, which scores the seed again before its first stop check.
            reason = "converged-stalled"
        elif allowance >= 1:
            # Search and reflection calls leave the gate its estimated time. Preflight and the gate use all of it.
            self.budget.reserve_time(_number_field(mapping(field(self.record, "budget")), "reserve_seconds"))
            # GEPA counts a reused outcome as a metric call, so the engine bound adds the reusable outcomes.
            # The search can ask for fewer of them, so the live evaluations alone end the search at the allowance.
            reusable = self._recorded_for_reuse()
            try:
                stopped = pareto_search(files, self.edit, cast(list[object], train), cast(list[object], validation),
                                        self._evaluate_example, self._propose, metric_calls=allowance + reusable,
                                        stalled=self._stalled, spent=lambda: self._share_spent(allowance))
                self._raise_fault()
                reason = "converged-stalled" if stopped else "validation-mean"
            except BudgetExhausted:
                self.take_budget_stop()
                reason = "budget-exhausted-best-validated"
                # Read before the `finally` gives the reserved time back: the search deadline, not the call cap, ended it.
                timed_out = self.budget.out_of_time()
            finally:
                self.budget.reserve_time(0.0)
        winner = self._best_validated(validation)
        if reason == "budget-exhausted-best-validated" and winner.identity == self.seed.identity:
            reason = "budget-exhausted-seed-retained"
        scored = self._search_outcomes()
        ceiling = self._search_ceiling()
        time_stop = timed_out and 2 * len(scored) < ceiling
        if time_stop:
            reason = "time-exhausted"
        if scored and all(_is_failed(item) for item in scored):
            raise CodedError("search-failed", "every search evaluation failed: " + str(scored[-1]["failure"])
                             + "; check the model, then start a new run directory")
        self.record["winner"] = winner.files
        with self.lock:
            rejections = {rule: len(keys) for rule, keys in sorted(self._leak_rejections.items())}
        earlier = self.record.get("search")
        base = cast(dict[str, object], earlier) if isinstance(earlier, dict) else {}
        self.record["search"] = base | {
            "reason": reason,
            "retained_seed": winner.identity == self.seed.identity,
            "leakage_rejections": rejections, **search_counts(scored),
            "proposal_over_budget": self._over_budget(),
            "attempts": build_attempts(self.outcomes, self._seed_ref(), self._validation_ids()),
            "text_dropped": self._text_dropped(),
        }
        if time_stop:
            with self.lock:
                seconds = {role: round(self.budget.seconds_by_role.get(role, 0.0), 3) for role in SEARCH_ROLES}
            cast(dict[str, object], self.record["search"])["seconds_by_role"] = seconds
        self.record["phase"] = "searched"
        self.checkpoint()

    def _best_validated(self, validation: list[str]) -> Candidate:
        """Return the recorded candidate with the best validation mean.

        The seed wins ties, unless a token field is set and the candidate has fewer mean validation task tokens.
        """
        means = self._validation_means(validation)
        kept = self.record.get("best_validated")
        if not isinstance(kept, dict):
            return self.seed
        kept = cast(dict[str, object], kept)
        identity = text_field(kept, "identity")
        if identity in means:
            seed_mean = means.get(self.seed.identity, -1.0)
            if self._beats(validation, means[identity], seed_mean, identity, self.seed.identity):
                return self.seed.changed(candidate_files(kept.get("files")))
        return self.seed

    def _holdout_outcomes(self) -> list[dict[str, object]]:
        """Return the scored holdout outcomes of the baseline and the winner.

        The newest outcome of an (arm, case, repeat) wins.
        """
        newest: dict[tuple[object, object, object], dict[str, object]] = {}
        for item in scored_holdout(self.outcomes, ("baseline", "winner")):
            newest[(item["arm"], item["case_id"], item.get("repeat"))] = item
        return list(newest.values())

    def _holdout_scores(self, arm: str) -> dict[str, list[float]]:
        return by_repeat_order(holdout_scores(self.outcomes, arm))

    def _holdout_tokens(self) -> dict[str, tuple[float | None, float | None]]:
        """Return each holdout case's mean task tokens per arm over its repeats. A missing count makes the mean None."""
        counts: dict[str, dict[str, list[int | None]]] = {}
        for item in self._holdout_outcomes():
            counts.setdefault(cast(str, item["case_id"]), {"baseline": [], "winner": []})[
                cast(str, item["arm"])].append(task_tokens(item))
        return {case_id: (_mean_tokens(arms["baseline"]), _mean_tokens(arms["winner"]))
                for case_id, arms in counts.items()}

    def gate(self) -> None:
        """Score baseline and winner on the unseen holdout, `repeats` times per case, then record the verdict."""
        holdout = self.cases_for("holdout")
        repeats = _int_field(self.record, "repeats")
        try:
            winner = self._built(self.seed.changed({name: candidate_files(field(self.record, "winner"))[name]
                                                    for name in self.seed.editable}))
        except CodedError as error:
            raise Stop("build-failed", str(error), {"next": "the winner no longer builds; start a new run in a new directory"}) from None
        families = {case.identifier: case.family for case in holdout}
        if winner.identity == self.seed.identity:
            tie = gate_verdict({case.identifier: 0.0 for case in holdout}, thresholds=self.statistics, families=families)
            outcome = Verdict("inconclusive", 0.0, 0.0, tie.cases, ("winner-equals-baseline",), tie.skipped_families)
            self.record["gate"] = {**outcome.data(), "repeats": repeats}
        else:
            _ = self.budget.remaining()
            rejected = next((item for item in self.outcomes if item.get("arm") == "baseline"
                             and item.get("status") == "candidate-contract-rejected"), None)
            if rejected is not None:
                raise _baseline_rejected(_rejection_detail(rejected))
            arms = {"baseline": self.seed, "winner": winner}
            done = {(item.get("arm"), item.get("case_id"), item.get("repeat")) for item in self.outcomes
                    if item.get("split") == "holdout" and item.get("status") != "evaluation-failed"}
            todo = [(arm, case, repeat) for arm in arms for case in holdout for repeat in range(repeats)
                    if (arm, case.identifier, repeat) not in done]

            def inspect(arm: str, result: dict[str, object]) -> None:
                if arm == "baseline" and result.get("status") == "candidate-contract-rejected":
                    raise _baseline_rejected(_rejection_detail(result))

            need = sum(case_calls(self.contract, case) for _, case, _ in todo)
            left = self.budget.maximum - self.budget.calls - self._replay_need()
            if left < need:
                raise Stop("gate-budget-exhausted", f"the holdout gate needs {need} more calls; "
                           + f"{left} remain in the approved budget after the replay reserve",
                           {"next": "start a new run in a new directory"})
            self._score_holdout([(arms[arm], case, arm, repeat) for arm, case, repeat in todo], inspect=inspect)
            paired = holdout_deltas(self._holdout_scores("baseline"), self._holdout_scores("winner"))
            lost = [item for item in self.outcomes if item.get("split") == "holdout" and item.get("arm") in arms and _is_failed(item)]
            if lost and not paired.deltas:
                raise CodedError("holdout-faulted", f"no holdout case has a scored baseline and winner pair; {len(lost)} holdout "
                                 + f"evaluations failed ({_reason_counts(lost)}); wait for the provider, then resume the run")
            tokens = ({case_id: arms_tokens for case_id, arms_tokens in self._holdout_tokens().items()
                       if case_id in paired.deltas} if self.statistics.token_rule else None)
            outcome = replace(gate_verdict(paired.deltas, thresholds=self.statistics, families=families,
                                           tokens=tokens, se_floor=paired.se_floor,
                                           planned=len(holdout)), moves=paired.moves)
            self.record["gate"] = {**outcome.data(), "repeats": repeats}
        self.record["winner_hash"] = winner.identity
        self.record["phase"] = "gated"
        self.checkpoint()

    def _conclude(self, reasons: list[str], patch_digest: str | None = None, *, detail: dict[str, object] | None = None,
                  per_case: dict[str, float] | None = None, mean: float | None = None, se: float | None = None) -> None:
        """Record the replay reasons and evidence and end the phase. The claims report maps the reasons to a status."""
        self.record["replay"] = {
            "reasons": reasons,
            "patch_digest": patch_digest, "cases": len(per_case or {}), "mean_delta": mean, "se": se,
            "per_case": per_case or {}, "tolerance": self.statistics.min_gain, "detail": detail or {}}
        self.record["phase"] = "replayed"
        self.checkpoint()

    def replay(self) -> None:
        """Check that the exported patch reproduces the winner, then score the patched copy once on the holdout.

        The copy sits under `out/replay`. Git applies the patches to it, as a user does. The rerun candidate
        comes from the patched files and goes through the harness like any holdout candidate.
        A resume skips each case that already has a scored replay outcome.
        """
        if "winner-equals-baseline" in cast(list[object], mapping(field(self.record, "gate")).get("reasons", [])):
            self._conclude(["winner-equals-baseline"])
            return
        winner = field(self.record, "winner")
        try:
            patch = build_patch(field(self.record, "seed"), winner, Path(text_field(self.record, "target_root")))
        except CodedError as error:
            self._conclude(["patch-apply-failed"], detail={"code": error.code, "error": str(error)[:STDERR_LIMIT]})
            return
        copy = prepare_copy(self.out / "replay", self.seed, patch)
        failure = apply_patches(copy, patch)
        if failure is not None:
            self._conclude(["patch-apply-failed"], patch.digest, detail={"stderr": failure})
            return
        differing = differences(copy, self.seed, patch)
        if differing:
            self._conclude(["patch-bytes-differ"], patch.digest, detail={"differing": differing})
            return
        try:
            rebuilt = self._built(self.seed.changed(replay_components(copy, self.seed, patch, winner)))
        except CodedError as error:
            self._conclude(["replay-build-failed"], patch.digest,
                           detail={"code": error.code, "error": str(error)[:STDERR_LIMIT]})
            return
        except ValueError as error:
            self._conclude(["replay-identity-differs"], patch.digest, detail={"error": str(error)[:STDERR_LIMIT]})
            return
        if rebuilt.identity != text_field(self.record, "winner_hash"):
            self._conclude(["replay-identity-differs"], patch.digest,
                           detail={"expected": text_field(self.record, "winner_hash"), "found": rebuilt.identity})
            return
        shortfall = self._score_replay(rebuilt)
        newest = self._replay_outcomes()
        holdout = self.cases_for("holdout")
        reasons: list[str] = []
        if shortfall is not None:
            reasons.append("replay-budget-exhausted")
        elif any(case.identifier not in newest for case in holdout):
            reasons.append("replay-evaluation-failed")
        detail = shortfall or {}
        if any(item.get("status") == "candidate-contract-rejected" for item in newest.values()):
            self._conclude([*reasons, "replay-contract-rejected"], patch.digest, detail=detail)
            return
        if any(item.get("loaded") is False for item in newest.values()):
            self._conclude([*reasons, "replay-not-loaded"], patch.digest, detail=detail)
            return
        winners = self._holdout_scores("winner")
        per_case = {identifier: unit_score(item["score"]) - math.fsum(winners[identifier]) / len(winners[identifier])
                    for identifier, item in sorted(newest.items()) if winners.get(identifier)}
        mean = se = None
        if per_case:
            mean, se, drifted = replay_drift(list(per_case.values()), self.statistics.min_gain)
            if drifted:
                reasons.append("replay-score-drift")
        self._conclude(reasons, patch.digest, detail=detail, per_case=per_case, mean=mean, se=se)

    def _replay_outcomes(self) -> dict[str, dict[str, object]]:
        """Return the newest scored replay outcome of each holdout case. An `evaluation-failed` outcome is unscored."""
        with self.lock:
            return {cast(str, item["case_id"]): item for item in self.outcomes
                    if item.get("arm") == "replay" and item.get("split") == "holdout" and item.get("status") != "evaluation-failed"}

    def _replay_need(self) -> int:
        """Return the calls that the replay still needs: the cost of each holdout case without a scored replay outcome."""
        done = self._replay_outcomes()
        return sum(case_calls(self.contract, case) for case in self.cases_for("holdout") if case.identifier not in done)

    def _score_replay(self, rebuilt: Candidate) -> dict[str, object] | None:
        """Evaluate `rebuilt` once on each holdout case that has no scored replay outcome.

        Return None when the replay has all its outcomes, else the shortfall detail when the budget ran short.
        The check runs before any call. A short budget makes no call and raises no stop.
        """
        done = self._replay_outcomes()
        todo = [case for case in self.cases_for("holdout") if case.identifier not in done]
        if not todo:
            return None
        try:
            _ = self.budget.remaining()
            needed, left = sum(case_calls(self.contract, case) for case in todo), self.budget.maximum - self.budget.calls
            if left < needed:
                return {"needed": needed, "left": left}
            self._score_holdout([(rebuilt, case, "replay", 0) for case in todo], keep_budget_stop=True)
        except BudgetExhausted as error:
            self.take_budget_stop()
            return {"shortfall": "deadline" if "deadline" in str(error) else "calls"}
        return None

    def _score_holdout(self, tasks: Sequence[tuple[Candidate, Case, str, int]], *,
                       inspect: Callable[[str, dict[str, object]], None] | None = None,
                       keep_budget_stop: bool = False) -> None:
        """Evaluate each (candidate, case, arm, repeat) task on the holdout. A plain failure becomes a failed outcome.

        A CodedError ends the work. `inspect` sees the arm and the result of each task that returns one.
        With `keep_budget_stop`, a budget stop first records a failed outcome, so the calls that it charged keep an outcome.
        """
        def record(case: Case, candidate: Candidate, error: Exception, arm: str, repeat: int) -> None:
            with self.lock:
                self.outcomes.append(_failed_outcome(case, candidate, error, arm=arm, repeat=repeat))
                self.checkpoint()

        def score(task: tuple[Candidate, Case, str, int]) -> None:
            candidate, case, arm, repeat = task
            with self.lock:
                latched = self._fault is not None
            try:
                result = self.evaluate_case(candidate, case, arm, holdout=True, repeat=repeat)
            except BudgetExhausted as error:
                # A task that meets an already latched stop made no call, so it keeps no outcome.
                if keep_budget_stop and not latched:
                    record(case, candidate, error, arm, repeat)
                raise
            except CodedError:
                raise
            except (OSError, ValueError, RuntimeError) as error:
                record(case, candidate, error, arm, repeat)
                return
            if inspect is not None:
                inspect(arm, result)

        with ThreadPoolExecutor(MAX_CONCURRENT_CALLS) as pool:
            _ = list(pool.map(score, tasks))

    def finish(self) -> None:
        self.record["phase"] = "complete"
        self.checkpoint()


def _mean_tokens(counts: Sequence[int | None]) -> float | None:
    """Return the mean of `counts`, or None when it is empty, lacks a count, or overflows a float."""
    known = [count for count in counts if count is not None]
    if not counts or len(known) != len(counts):
        return None
    try:
        return math.fsum(known) / len(known)
    except OverflowError:
        return None


def stall_observations(outcomes: Sequence[Mapping[str, object]], validation: set[str]) -> list[float | None]:
    """Return one stall observation per recorded proposal, oldest first.

    The search outcomes after a reflection and before the next one belong to its `proposed` candidate. The observation
    is the rise of that candidate's validation mean over the best mean of every earlier candidate, the seed included.
    It is None when an evaluation of the candidate failed or the host build of the proposal failed. It is 0.0 when the
    proposal has no identity or its candidate does not complete the validation set.
    """
    marks = [index for index, item in enumerate(outcomes) if _is_reflection(item)]
    seen = _add_validation_scores({}, outcomes[:marks[0]] if marks else [])
    observations: list[float | None] = []
    for start, end in zip(marks, [*marks[1:], len(outcomes)]):
        best = max(_complete_means(seen, validation).values(), default=0.0)
        window = outcomes[start + 1:end]
        reflection = outcomes[start]
        proposed = reflection.get("proposed")
        earlier = seen.get(proposed, {}) if isinstance(proposed, str) else {}
        observations.append(None if reflection.get("status") is not None
                            else _observation(window, proposed, validation, best, earlier))
        _ = _add_validation_scores(seen, window)
    return observations


def _observation(window: Sequence[Mapping[str, object]], identity: object, validation: set[str],
                 best: float, earlier: Mapping[str, float]) -> float | None:
    """Return the observation of one proposal from the outcomes in its window.

    Only the evaluations of the proposed candidate count, up to the end of its validation set. GEPA can score the
    candidate again as the next parent before the next reflection. That later score does not change the observation.
    `earlier` holds the validation scores of the candidate from before the window. A resume reuses those scores, so
    the window does not repeat them.
    """
    scores = dict(earlier)
    for item in window:
        if identity is None or set(scores) == validation:
            break
        if item.get("search_candidate") != identity:
            continue
        if _is_failed(item):
            return None
        if item.get("split") == "validation":
            scores[cast(str, item["case_id"])] = unit_score(item["score"])
    if set(scores) != validation:
        return 0.0
    return max(0.0, math.fsum(scores.values()) / len(scores) - best)


def _is_search_validation(item: Mapping[str, object]) -> bool:
    return item.get("arm") == "search" and item.get("split") == "validation" and "search_candidate" in item


def _is_unscored(record: Mapping[str, object]) -> bool:
    """Return whether a feedback record of GEPA, or a value nested in it, marks an unscored evaluation."""
    return record.get("unscored") is True or any(isinstance(value, Mapping) and cast(Mapping[str, object], value).get("unscored") is True
                                                 for value in record.values())


def _add_validation_scores(scores: dict[str, dict[str, float]],
                           items: Sequence[Mapping[str, object]]) -> dict[str, dict[str, float]]:
    """Add each search validation score in `items` to `scores` by candidate and case, and return `scores`.

    A later score for a case replaces the earlier one. An `evaluation-failed` outcome has no score and adds none.
    """
    for item in items:
        if _is_search_validation(item) and not _is_failed(item):
            scores.setdefault(cast(str, item["search_candidate"]), {})[cast(str, item["case_id"])] = unit_score(item["score"])
    return scores


def _complete_means(scores: Mapping[str, Mapping[str, float]], validation: set[str]) -> dict[str, float]:
    """Return the mean score of each candidate that has a score on every validation case."""
    return {identity: math.fsum(by_case.values()) / len(by_case) for identity, by_case in scores.items()
            if set(by_case) == validation}


_PHASES = ("prepared", "searched", "gated", "replayed", "complete")


def _phase(record: dict[str, object]) -> str:
    return text_field(record, "phase")

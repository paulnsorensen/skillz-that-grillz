"""The per-run state machine, its provider seam, outcome records, and fault helpers.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast, final, get_args

from skillz_experiments._audit import identity as judge_identity
from skillz_experiments._budget import (
    MAX_CONCURRENT_CALLS,
    Budget,
    BudgetExhausted,
    ceilings,
)
from skillz_experiments._candidate import Candidate, candidate_files, load_frozen
from skillz_experiments._cases import Case, CodedError, Split, digest, load_cases, mapping
from skillz_experiments._contract import parse
from skillz_experiments._evaluator import task_tokens
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
from skillz_experiments.run._plan import case_calls, plan_estimate, size_plan
from skillz_experiments.run._stop import Stop
from skillz_experiments.search import Denylist, Edit, accept_proposal, overshoot, reflection_request
from skillz_experiments.search import check as check_leakage
from skillz_experiments.search import search as pareto_search

# Seconds that the wall clock may step back between prepare and resume, for example after an NTP step.
CLOCK_TOLERANCE = 5.0


HarnessName = str

LEAKAGE_REJECTED = "candidate-leakage-rejected"

# The flat modules whose source decides a score, next to every file in `gate/`. Follow a module when a later wave moves it into a slice.
SCORING_FILES = ("_evaluator.py", "_evaluation.py", "_graders.py", "_audit.py", "_contract.py")


def _failed_outcome(case: Case, candidate: Candidate, error: Exception, *, arm: str = "search",
                    repeat: int | None = None) -> dict[str, object]:
    """Return the outcome that records a failed evaluation as a zero score. A holdout failure names its arm and repeat."""
    outcome: dict[str, object] = {"arm": arm, "split": case.split, "case_id": case.identifier, "score": 0.0,
                                  "status": "evaluation-failed", "failure": str(error)}
    if arm == "search":
        outcome["search_candidate"] = candidate.identity
    if repeat is not None:
        outcome["repeat"] = repeat
    return outcome


def _baseline_rejected(detail: str | None = None) -> Stop:
    return Stop("baseline-contract-rejected", "original candidate fails the frozen native/helper contract",
                {"next": "fix the skill or its contract, then start a new run in a new directory"}
                | cast(dict[str, object], {} if detail is None else {"detail": detail}))


def _unit_score(value: object) -> float:
    """Clamp a provider score to 0..1. A NaN counts as 0."""
    return min(1.0, max(0.0, float(cast(float, value))))


def is_inside(path: Path, root: Path) -> bool:
    """Return whether `path` resolves, through any symlink, to `root` or below it."""
    return path.resolve().is_relative_to(root.resolve())


class _Provider(Protocol):
    def preflight(self) -> dict[str, object]: ...
    def close(self) -> None: ...
    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]: ...
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


def record_outcomes(record: dict[str, object]) -> list[dict[str, object]]:
    value = field(record, "outcomes")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in cast(list[object], value)):
        raise ValueError("run record field outcomes must be a list of objects")
    return cast(list[dict[str, object]], value)


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
        self._search_cases = {case.identifier: case for case in self.cases if case.split != "holdout"}
        # Rebuilt from the frozen cases on every open. It never reaches `run.json` or an export.
        self._denylist = Denylist.build(self._search_cases.values())
        self._leak_rules: dict[str, str | None] = {}
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
                      calls=_int_field(self.record, "calls"), approved=budget.get("approved") is True,
                      journey=budget.get("journey") is True)

    def _verify_budget(self, budget: dict[str, object]) -> None:
        """Stop when the recorded budget differs from the plan that the frozen cases, repeats, and edit give."""
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
                or differs(budget.get("reserve"), min(plan.holdout_calls, maximum))
                or differs(budget.get("reserve_seconds"), plan.reserve_seconds)
                or (budget.get("journey") is True) != plan.sized.journey
                or not 0 < seconds <= ceilings(plan.sized.journey)[1]):
            raise Stop("run-record-tampered", "the recorded budget differs from the plan of the frozen run; create a new run", {})

    def checkpoint(self) -> None:
        with self.lock:
            self.record["calls"] = self.budget.calls
            self.record["leakage_rejected"] = {rule: sorted(keys) for rule, keys in sorted(self._leak_rejections.items())}
            write(self.out / "run.json", self.record)

    def cases_for(self, split: Split) -> list[Case]:
        return sorted((case for case in self.cases if case.split == split), key=lambda case: case.identifier)

    def preflight(self) -> None:
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
        rule = self._leak_rule(components)
        if rule is not None:
            return 0.0, {"task_correct": 0.0, "request": case.request, "rejected": LEAKAGE_REJECTED,
                         "reason": LEAKAGE_REJECTED, "rule": rule}
        try:
            candidate = self._built(self.seed.changed(components))
        except ValueError as error:
            rejected: dict[str, object] = {"task_correct": 0.0, "request": case.request, "rejected": str(error)}
            if isinstance(error, CodedError):
                rejected["reason"] = error.code
            return 0.0, rejected
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

    def _validation_outcomes(self) -> list[dict[str, object]]:
        """Return the outcomes of search candidates on validation cases."""
        return [item for item in self.outcomes if item.get("arm") == "search" and item.get("split") == "validation"
                and "search_candidate" in item]

    def _validation_means(self, validation: list[str]) -> dict[str, float]:
        """Return the mean score of each search candidate that has a score on every validation case."""
        scores: dict[str, dict[str, float]] = {}
        for item in self._validation_outcomes():
            scores.setdefault(cast(str, item["search_candidate"]), {})[cast(str, item["case_id"])] = _unit_score(item["score"])
        return {identity: math.fsum(by_case.values()) / len(by_case) for identity, by_case in scores.items()
                if set(by_case) == set(validation)}

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
        prompt, schema = reflection_request(self.edit, candidate, feedback, components,
                                            cheaper=self.statistics.token_rule)
        try:
            result = self.provider.invoke(prompt, schema=schema)
        except CodedError as error:
            self._latch(error)
            raise
        with self.lock:
            self.outcomes.append({"arm": "search", "split": "reflection", "usage": result.get("usage"),
                                  "latency_seconds": result.get("latency_seconds")})
            self.checkpoint()
        return accept_proposal(result["answer"], components)

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
        if not self.provider.check_candidate(self.seed):
            raise _baseline_rejected(self.provider.rejection)

    def search(self) -> None:
        """Run the one GEPA search over train and validation cases. Holdout cases never enter it."""
        files = {name: self.seed.files[name] for name in self.seed.editable}
        train = [case.identifier for case in self.cases_for("train")]
        validation = [case.identifier for case in self.cases_for("validation")]
        planned = _int_field(mapping(field(self.record, "estimate")), "metric_calls")
        spent = len(self._search_outcomes())
        allowance = planned - spent - overshoot(len(validation))
        reason = "search-share-spent-best-validated"
        if allowance >= 1:
            # Search and reflection calls leave the gate its estimated time. Preflight and the gate use all of it.
            self.budget.reserve_time(_number_field(mapping(field(self.record, "budget")), "reserve_seconds"))
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
        with self.lock:
            rejections = {rule: len(keys) for rule, keys in sorted(self._leak_rejections.items())}
        self.record["search"] = {"reason": reason, "retained_seed": winner.identity == self.seed.identity,
                                 "leakage_rejections": rejections}
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

        An `evaluation-failed` outcome is unscored and drops out. The newest outcome of an (arm, case, repeat) wins.
        """
        newest: dict[tuple[object, object, object], dict[str, object]] = {}
        for item in self.outcomes:
            if (item.get("split") == "holdout" and item.get("arm") in ("baseline", "winner")
                    and item.get("status") != "evaluation-failed"):
                newest[(item["arm"], item["case_id"], item.get("repeat"))] = item
        return list(newest.values())

    def _holdout_scores(self, arm: str) -> dict[str, list[float]]:
        scores: dict[str, list[tuple[int, float]]] = {}
        for item in self._holdout_outcomes():
            if item["arm"] == arm:
                scores.setdefault(cast(str, item["case_id"]), []).append(
                    (cast(int, item["repeat"]), _unit_score(item["score"])))
        return {case_id: [score for _, score in sorted(pairs)] for case_id, pairs in scores.items()}

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
            self.record["gate"] = {**outcome.data(), "repeats": repeats, "reason": "winner-equals-baseline"}
        else:
            _ = self.budget.remaining()
            if any(item.get("arm") == "baseline" and item.get("status") == "candidate-contract-rejected"
                   for item in self.outcomes):
                raise _baseline_rejected()
            arms = {"baseline": self.seed, "winner": winner}
            done = {(item.get("arm"), item.get("case_id"), item.get("repeat")) for item in self.outcomes
                    if item.get("split") == "holdout" and item.get("status") != "evaluation-failed"}
            todo = [(arm, case, repeat) for arm in arms for case in holdout for repeat in range(repeats)
                    if (arm, case.identifier, repeat) not in done]

            def score(task: tuple[str, Case, int]) -> None:
                arm, case, repeat = task
                try:
                    result = self.evaluate_case(arms[arm], case, arm, holdout=True, repeat=repeat)
                except CodedError:
                    raise
                except (OSError, ValueError, RuntimeError) as error:
                    with self.lock:
                        self.outcomes.append(_failed_outcome(case, arms[arm], error, arm=arm, repeat=repeat))
                        self.checkpoint()
                    return
                if arm == "baseline" and result.get("status") == "candidate-contract-rejected":
                    raise _baseline_rejected()

            need = sum(case_calls(self.contract, case) for _, case, _ in todo)
            if self.budget.maximum - self.budget.calls < need:
                raise Stop("gate-budget-exhausted", f"the holdout gate needs {need} more calls; "
                           + f"{self.budget.maximum - self.budget.calls} remain in the approved budget",
                           {"next": "start a new run in a new directory"})
            with ThreadPoolExecutor(MAX_CONCURRENT_CALLS) as pool:
                _ = list(pool.map(score, todo))
            paired = holdout_deltas(self._holdout_scores("baseline"), self._holdout_scores("winner"))
            tokens = ({case_id: arms_tokens for case_id, arms_tokens in self._holdout_tokens().items()
                       if case_id in paired.deltas} if self.statistics.token_rule else None)
            outcome = replace(gate_verdict(paired.deltas, thresholds=self.statistics, families=families,
                                           tokens=tokens, se_floor=paired.se_floor,
                                           planned=len(holdout)), moves=paired.moves)
            self.record["gate"] = {**outcome.data(), "repeats": repeats}
        self.record["winner_hash"] = winner.identity
        self.record["phase"] = "gated"
        self.checkpoint()

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


_PHASES = ("prepared", "searched", "gated", "complete")


def _phase(record: dict[str, object]) -> str:
    return text_field(record, "phase")

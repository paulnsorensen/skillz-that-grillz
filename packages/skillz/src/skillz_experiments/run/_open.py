"""Open a recorded run: freeze its identities and compatibility fingerprint, and rebuild the approved budget.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import cast, get_args

from skillz_experiments._audit import GraderKind
from skillz_experiments._audit import identity as judge_identity
from skillz_experiments._budget import SECONDS_PER_CALL, Budget, BudgetExhausted, ceilings
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError, Split, digest, mapping
from skillz_experiments._contract import RUBRIC_GRADERS, Contract
from skillz_experiments.harness import ClaudeOptions, Configuration
from skillz_experiments.run._plan import VALIDATION_REPEATS, plan_estimate, size_plan
from skillz_experiments.run._record_fields import (
    POST_GATE_PHASES, count_map, field, int_field, number_field, phase, text_field,
)
from skillz_experiments.run._stop import Stop

# Seconds that the wall clock may step back between prepare and resume, for example after an NTP step.
CLOCK_TOLERANCE = 5.0

# The modules whose source decides a score, next to every file in `gate/`. Follow a module when a later wave moves it into a slice.
SCORING_FILES = ("evaluation/_evaluator.py", "evaluation/_memo.py", "evaluation/_scoring.py", "_graders.py", "_audit.py", "_contract.py")


def engine_hash() -> str:
    package = Path(__file__).parent.parent
    return digest({path.relative_to(package).as_posix(): path.read_text() for path in package.rglob("*.py")})


def leakage_digest(package: Path) -> str:
    """Digest the source of the leakage check. A rule change decides which proposals get scored, so it starts a new cohort."""
    return digest({"search/_leakage.py": (package / "search" / "_leakage.py").read_text()})


def scoring_digest(package: Path) -> str:
    """Digest the source of the scoring modules under `package`: every file in `gate/`, then `SCORING_FILES`.

    A scoring change gives a new digest. An edit to `search/`, `run/`, or `harness/` keeps it.
    """
    paths = [*sorted((package / "gate").rglob("*.py")), *(package / name for name in SCORING_FILES)]
    return digest({path.relative_to(package).as_posix(): path.read_text() for path in paths})


def judge_kind(contract: Contract) -> GraderKind:
    """Return `criteria` when a kind of `contract` uses a criteria-level rubric judge, else `audit`.

    A contract can declare an audit kind next to a rubric kind, but `run` refuses to score an audit kind
    (`contract-audit-unsupported` in `_plan.py`). So each judge that a run calls is a rubric judge.
    """
    return "criteria" if any(kind in RUBRIC_GRADERS for kind in contract.grader_types().values()) else "audit"


def freeze_identities(record: dict[str, object], seed: Candidate, model: str, configuration: Configuration | None,
                      judged: bool, contract: Contract) -> None:
    if configuration is not None:
        target = record.get("target_root")
        if not isinstance(target, str):
            raise ValueError("run lacks candidate boundary; create a new run")
        configuration.check_boundary(Path(target))
        identity = configuration.identity()
        _ = record.setdefault("harness", identity)
        if record["harness"] != identity:
            raise CodedError("harness-changed", "harness configuration differs from the frozen record; create a new run")
    if judged:
        judge_model = configuration.roles["judge"].model if configuration is not None else model
        _ = record.setdefault("judge_model", judge_model)
        expected = judge_identity(judge_model, judge_kind(contract))
        _ = record.setdefault("judge", expected)
        if record["judge"] != expected:
            raise ValueError("judge differs from the frozen record; create a new run")
    if seed.identity != text_field(record, "seed_hash"):
        raise ValueError("seed differs from the frozen record")
    _check_validation_policy(record)
    if "seconds_per_call" not in mapping(field(record, "estimate")):
        raise ValueError("run was prepared by an older runner; create a new run")
    _ = record.setdefault("engine_hash", engine_hash())
    if record["engine_hash"] != engine_hash():
        raise ValueError("evaluator differs from the frozen record; create a new run")


def validation_policy() -> dict[str, object]:
    """Return the validation policy of this runner: the runs of one search candidate on one validation case."""
    return {"repeats": VALIDATION_REPEATS}


def _check_validation_policy(record: dict[str, object]) -> None:
    """Stop a run whose frozen validation policy differs from this runner's, or that has none.

    `_prepare` freezes the policy. The check runs before the engine-hash check, so the stop names the cause.
    A completed run never opens a session, so a record from an older runner stays readable by `export`.
    """
    recorded = record.get("validation_policy")
    if recorded != validation_policy():
        found = "no validation_policy" if recorded is None else f"validation_policy {recorded!r}"
        raise CodedError("validation-policy-differs", f"the run record froze {found}, and this runner uses "
                         + f"{validation_policy()!r}; finish the run with the runner that prepared it, or create a new run")


def freeze_compatibility(record: dict[str, object], cases: list[Case], contract: Contract,
                         configuration: Configuration | None) -> None:
    """Freeze the compatibility fingerprint once. It runs after `freeze_identities`, so `judge_model` is final.

    A supplied configuration decides the task fields through its task role, which can override the CLI flags.
    """
    if "compatibility" in record:
        return
    task = configuration.roles["task"] if configuration is not None else None
    splits = {split: sorted(case.identifier for case in cases if case.split == split)
              for split in ("train", "validation")}
    fields: dict[str, object] = {
        "contract_hash": text_field(record, "contract_hash"), "skill": contract.skill,
        "adapter": task.adapter if task else text_field(record, "adapter"),
        "task_model": task.model if task else text_field(record, "model"),
        "judge_model": record.get("judge_model"),
        "claude_options": task.options.data() if task else record.get("claude_options", {}),
        "edit": text_field(record, "edit"),
        "repeats": field(record, "repeats"), "statistics": field(record, "statistics"),
        "validation_policy": field(record, "validation_policy"),
        "split_digest": digest(splits), "scoring_digest": scoring_digest(Path(__file__).parent.parent),
        "leakage_digest": leakage_digest(Path(__file__).parent.parent)}
    record["compatibility"] = {"fingerprint": digest(fields), "fields": fields}


def open_budget(record: dict[str, object], cases: list[Case], contract: Contract, sizing: ClaudeOptions) -> Budget:
    """Rebuild the approved budget. A resumed run keeps `approved=True` and the calls it already spent.

    Elapsed time is the larger of the monotonic and the wall-clock span since prepare. A reboot resets the
    monotonic clock, so a negative monotonic span falls back to the wall clock. The wall clock may step back
    by up to `CLOCK_TOLERANCE` seconds. The gate time reserve applies only inside `search`.
    """
    budget = mapping(field(record, "budget"))
    sandbox_seconds = verify_budget(record, cases, contract, sizing, budget)
    calls = int_field(record, "calls")
    by_role = ({role: int(count) for role, count in count_map(record.get("calls_by_role")).items()}
               if "calls_by_role" in record else {"unattributed": calls} if calls else {})
    seconds_by_role = count_map(record.get("seconds_by_role"))
    retries = int_field(record, "retries") if "retries" in record else 0
    call_seconds = SECONDS_PER_CALL + sandbox_seconds
    wall = time.time() - number_field(record, "started")
    if not -CLOCK_TOLERANCE <= wall < math.inf:
        raise CodedError("clock-skew", "the wall clock is more than "
                         + f"{CLOCK_TOLERANCE:g} seconds earlier than the run start; set the clock right, or create a new run")
    monotonic = time.monotonic() - number_field(record, "started_monotonic")
    elapsed = max(wall, monotonic, 0.0)
    seconds = number_field(budget, "seconds")
    approved, journey = budget.get("approved") is True, budget.get("journey") is True
    if seconds - elapsed <= 0:
        if phase(record) not in POST_GATE_PHASES:
            raise BudgetExhausted("global deadline exhausted")
        # A gated or replayed run makes no model call without a deadline check, so the replay ends on its own.
        return Budget.expired(int_field(budget, "maximum"), reserve=int_field(budget, "reserve"), calls=calls,
                              approved=approved, journey=journey, calls_by_role=by_role,
                              seconds_by_role=seconds_by_role, retries=retries, call_seconds=call_seconds)
    return Budget(int_field(budget, "maximum"), seconds - elapsed, reserve=int_field(budget, "reserve"),
                  calls=calls, approved=approved, journey=journey, calls_by_role=by_role,
                  seconds_by_role=seconds_by_role, retries=retries, call_seconds=call_seconds)


def verify_budget(record: dict[str, object], cases: list[Case], contract: Contract, sizing: ClaudeOptions,
                  budget: dict[str, object]) -> int:
    """Stop when the recorded budget differs from the plan that the frozen cases, repeats, and edit give.

    Return the sandbox seconds of one evaluation, which add to the per-call time estimate.
    """
    count = {split: sum(case.split == split for case in cases) for split in cast(tuple[str, ...], get_args(Split))}
    repeats = int_field(record, "repeats")
    plan = size_plan(contract, cases, repeats, sizing)
    expected = plan_estimate(plan, repeats, count["holdout"])
    recorded = mapping(field(record, "estimate"))
    maximum = max(plan.sized.calls, 1)
    seconds = number_field(budget, "seconds")
    if ("reserve_seconds" not in budget or "replay_calls" not in recorded or "headroom" not in recorded
            or ("sandbox_seconds" in expected and "sandbox_seconds" not in recorded)):
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

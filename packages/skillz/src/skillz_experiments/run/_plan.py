"""Plan the calls and seconds for a run and resolve the editable files.
"""
from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import cast, get_args

from skillz_experiments._budget import (
    ESTIMATE_SECONDS_PER_CALL,
    JOURNEY_CALLS,
    JOURNEY_SECONDS,
    MAX_CONCURRENT_CALLS,
    Estimate,
    ceilings,
    estimate,
    gate_seconds,
)
from skillz_experiments._candidate import NEW_CLI, WEDGE_PREFIX
from skillz_experiments._cases import Case, CodedError, Split
from skillz_experiments._contract import Contract
from skillz_experiments._intake import IntakeCase
from skillz_experiments.run._wedge_targets import TargetPlan, mcp_text, never_editable
from skillz_experiments.harness import ClaudeOptions, Configuration
from skillz_experiments.run._stop import Stop
from skillz_experiments.search import REFLECTION_MINIBATCH, Edit, search_allowance

PREFLIGHT_CALLS = 6
"""Three roles, each with two preflight calls: the Bash sandbox check and the Read file-access check (gh-176)."""


def preflight_calls(harness: object) -> int:
    """Return the preflight allowance of a frozen role identity: two calls per Claude role, never under `PREFLIGHT_CALLS`.

    `Harness.preflight` probes each role once. A Claude role claims two budgeted calls. A run with no identity
    (the dry-run plan) and a run with the three default roles both keep `PREFLIGHT_CALLS`. An explicit simulator adds two.
    """
    return max(PREFLIGHT_CALLS, Configuration.claude_preflight_calls(harness))


SEARCH_CASE_FACTOR = 4

VALIDATION_REPEATS = 2
"""The runs of one search candidate on one validation case. `run.json` freezes it as `validation_policy.repeats`."""


def repeat_indices(split: str) -> tuple[int | None, ...]:
    """Return the repeat index of each search run of a case on `split`. A train case runs once, with no repeat index."""
    return tuple(range(VALIDATION_REPEATS)) if split == "validation" else (None,)


JOURNEY_SEARCH_CALLS = 460

CALLS_PER_REFLECTION = 2 * REFLECTION_MINIBATCH

MINIMUM_SEARCH_MARGIN = 4

HOLDOUT_RETRIES_PER_ARM = 1


@dataclass(frozen=True)
class Plan:
    metric_calls: int
    reflection_calls: int
    holdout_calls: int
    retry_calls: int
    sized: Estimate
    replay_calls: int = 0
    sandbox_gate: int = 0
    sandbox_total: int = 0
    sandbox_seconds: int = 0
    headroom_outcomes: int = 0

    @property
    def reserve(self) -> int:
        """Return the calls that the search leaves for the holdout gate and the replay."""
        return self.holdout_calls + self.replay_calls

    @property
    def reserve_seconds(self) -> int:
        """Return the seconds that the holdout gate and the replay hold, never more than the seconds cap."""
        return min(gate_seconds(self.reserve) + self.sandbox_gate, ceilings(self.sized.journey, self.sized.cap)[1])


def command_seconds(contract: Contract, cases: Sequence[Case | IntakeCase], options: ClaudeOptions) -> int:
    """Return the sandbox time of one evaluation: one limit per sandbox run, taken at the costliest scored kind.

    A command or hybrid grader runs one sandbox. A `capture` argv runs one more. The result is 0 when no scored case runs one.
    """
    runs = max((int(grader.type in ("command", "hybrid", "journey")) + int(bool(grader.capture))
                for grader in (contract.grader(case.kind) for case in cases)), default=0)
    return runs * options.sandbox_seconds


def case_calls(contract: Contract, case: Case | IntakeCase) -> int:
    """Return the worst-case invocation count of one evaluation of `case`. A journey case counts its `max_turns`."""
    return contract.calls(case.kind, case.journey.max_turns if case.journey is not None else 1)


def _plan(train: int, validation: int, holdout: int, per_evaluation: int, repeats: int, sandbox_seconds: int = 0,
          journey: bool = False, holdout_costs: Sequence[int] | None = None, cap: int = 0,
          preflight: int = PREFLIGHT_CALLS) -> Plan:
    """Size the search and reserve the gate, retries, and replay after the baseline measured the seed.

    The call and time estimates exclude the seed validation pass, the train headroom, and the baseline slots
    that the gate reuses. `preflight` is still one allowance for adapters that cannot reuse a recorded live pass.
    """
    max_calls, max_seconds = ceilings(journey, cap)
    costs = list(holdout_costs) if holdout_costs is not None else [per_evaluation] * holdout
    gate_repeats = 2 * repeats - min(repeats, 2)
    holdout_evaluations = holdout * (gate_repeats + 1) + 2 * HOLDOUT_RETRIES_PER_ARM
    retry_calls = 2 * HOLDOUT_RETRIES_PER_ARM * (max(costs) if costs else per_evaluation)
    holdout_pass = sum(costs)
    holdout_calls = holdout_pass * gate_repeats + retry_calls
    if preflight + holdout_calls + holdout_pass > max_calls:
        raise Stop("budget-unapproved", f"the preflight, the holdout gate, and the replay alone need "
                   + f"{preflight + holdout_calls + holdout_pass} calls; the cap is {max_calls}"
                   + "; lower --repeats or the holdout size", {})
    slow_gate_extra = gate_seconds(holdout_calls + holdout_pass) - estimate(search_calls=holdout_calls + holdout_pass).seconds
    validation_credit = VALIDATION_REPEATS * validation

    def sandbox_total(metric: int) -> int:
        evaluated = max(0, metric - validation_credit) + holdout_evaluations
        return math.ceil(evaluated * sandbox_seconds / MAX_CONCURRENT_CALLS)

    def size(metric: int) -> Estimate:
        return estimate(preflight_calls=preflight, holdout=holdout_calls - retry_calls,
                        search_calls=max(0, metric - validation_credit) * per_evaluation,
                        retry_calls=retry_calls, replay_calls=holdout_pass,
                        reflection_calls=math.ceil(metric / CALLS_PER_REFLECTION), journey=journey, cap=cap)

    def fits(metric: int) -> bool:
        sized = size(metric)
        return sized.calls <= max_calls and sized.seconds + sandbox_total(metric) + slow_gate_extra <= max_seconds

    if not fits(0):
        bare = size(0)
        raise Stop("budget-unapproved", "the preflight, the holdout gate, the replay, and their sandbox "
                   + f"time alone need {bare.seconds + sandbox_total(0) + slow_gate_extra} seconds; the cap is {max_seconds}"
                   + "; lower --repeats, the holdout size, or --sandbox-seconds", {})
    validation_runs = VALIDATION_REPEATS * validation
    metric = SEARCH_CASE_FACTOR * (train + validation_runs)
    if journey:
        metric = max(metric, JOURNEY_SEARCH_CALLS // per_evaluation)
    while metric > 0 and not fits(metric):
        metric -= 1
    allowance = search_allowance(metric, validation_runs)
    if allowance < MINIMUM_SEARCH_MARGIN:
        raise Stop("budget-unapproved", f"too few calls are left for a search under the cap: {metric} metric calls leave "
                   + f"{allowance} after the seed pass over {validation} validation cases and the overshoot margin, "
                   + f"and the search needs {MINIMUM_SEARCH_MARGIN}; lower --repeats or the holdout size, "
                   + "or change --seed or the case set", {})
    sandbox_gate = math.ceil(holdout_evaluations * sandbox_seconds / MAX_CONCURRENT_CALLS)
    sized, total = size(metric), sandbox_total(metric)
    return Plan(metric_calls=metric, reflection_calls=math.ceil(metric / CALLS_PER_REFLECTION), holdout_calls=holdout_calls,
                retry_calls=retry_calls, sized=Estimate(sized.calls, sized.seconds + total, journey, cap),
                replay_calls=holdout_pass, sandbox_gate=sandbox_gate, sandbox_total=total, sandbox_seconds=sandbox_seconds,
                headroom_outcomes=train)


def size_plan(contract: Contract, cases: Sequence[Case], repeats: int, options: ClaudeOptions, cap: int = 0,
              preflight: int = PREFLIGHT_CALLS) -> Plan:
    """Size search from per-case costs. Charge each metric call at the mean cost of the searched cases."""
    split = {name: [case_calls(contract, case) for case in cases if case.split == name]
             for name in cast(tuple[str, ...], get_args(Split))}
    searched = split["train"] + VALIDATION_REPEATS * split["validation"]
    per_evaluation = max(1, math.ceil(sum(searched) / len(searched))) if searched else 1
    return _plan(len(split["train"]), len(split["validation"]), len(split["holdout"]), per_evaluation, repeats,
                 command_seconds(contract, cases, options), journey=any(case.journey is not None for case in cases),
                 holdout_costs=split["holdout"], cap=cap, preflight=preflight)


def editable_files(files: Mapping[str, str], contract: Contract, edit: Edit, frozen: Collection[str] = (),
              plan: TargetPlan | None = None) -> list[str]:
    """Return the editable names: Markdown for `prose`, plus helper scripts and own wedge sources for `prose+cli`.

    A frozen file (a `.pyz`, a binary, a large file) is never editable. A helper that names an own `.pyz`
    resolves to its target, whose sources join through `plan`. Any other frozen helper stops the run.
    """
    helper = contract.helper.path if contract.helper is not None else None
    never = [name for name in contract.editable if never_editable(name)]
    if never:
        raise CodedError("editable-build-file", f"the contract editable list names {', '.join(never)}; "
                         + "wedge.toml, uv.lock, and .pyz files define or hold a build and are never editable")
    pinned = [name for name in contract.editable if name in frozen]
    if pinned:
        raise CodedError("editable-frozen", f"the contract editable list names {', '.join(pinned)}; each is a frozen file "
                         + "(binary, not UTF-8, or over the text limit), so no proposal can edit it")
    if edit == "prose":
        code_names = [name for name in contract.editable if not name.endswith(".md")]
        if code_names:
            raise CodedError("editable-needs-cli", f"the contract editable list names {', '.join(code_names)}, which "
                             + "are not Markdown; run with --edit prose+cli or remove them from editable")
    if helper is not None and helper not in files and (plan is None or plan.helper_target(contract) is None):
        if helper in frozen and edit != "prose":
            raise CodedError("helper-frozen", f"the contract helper {helper} is a frozen file (binary, not UTF-8, "
                             + "or over the text limit) and not the .pyz of an own wedge target, so no proposal can edit it; "
                             + "pass --edit prose when you want no helper edit")
        elif helper not in frozen:
            raise CodedError("helper-file-missing", f"the contract helper {helper} is missing from the target")
    mcp = mcp_text(files, contract)
    named = [*contract.editable, *([helper] if helper is not None else [])]
    run = [name for name in named if contract.read_only(name, mcp)]
    if run:
        raise CodedError("contract-surface", "contract surface files the MCP config runs are read-only, so editable and "
                         + f"the helper cannot hold {', '.join(run)}")
    base = list(contract.editable) or [name for name in files if (name.endswith(".md") or name.startswith("scripts/"))
                                       and not name.startswith(WEDGE_PREFIX) and not never_editable(name)
                                       and not contract.read_only(name, mcp)]
    names = sorted(name for name in base if name.endswith(".md"))
    if not names:
        raise CodedError("prompt-components-missing", "search needs editable Markdown")
    if edit == "prose+cli":
        code = {name for name in base if not name.endswith(".md")}
        if helper is not None and helper in files:
            code.add(helper)
        if plan is not None:
            code |= set(plan.sources)
            if plan.capable is not None:
                code.add(NEW_CLI)
        if not code:
            reasons: Mapping[str, str] = plan.reasons if plan is not None else {}
            frozen_why = "".join(f"; {name} is frozen because {why}" for name, why in reasons.items())
            if plan is not None and plan.dropped:
                frozen_why += f"; @new-cli is unavailable because {plan.dropped}"
            raise CodedError("helper-missing", "prose+cli needs a helper script to edit; add one under scripts/" + frozen_why)
        names += sorted(code)
    absent = [name for name in names if name not in files]
    if absent:
        raise CodedError("helper-file-missing", f"editable files are missing from the target: {', '.join(absent)}")
    return names


def _default_kinds(contract: Contract) -> tuple[str, ...]:
    journeys = contract.journey_kinds()
    if "task" in contract.kinds:
        return ("task",)
    if journeys and len(journeys) == len(contract.kinds):
        return journeys
    if len(contract.kinds) == 1:
        return tuple(contract.kinds)
    raise CodedError("contract-kinds", f"the contract declares the kinds {', '.join(sorted(contract.kinds))}; "
                     + "run needs a contract with a `task` kind, only journey kinds, or exactly one kind; "
                     + "name the scored kinds with `--kind KIND` (repeat the flag for more)")


def scored_kinds(contract: Contract, requested: Sequence[str] = ()) -> tuple[str, ...]:
    """Return the scored kinds: the `--kind` selection, else the contract default.

    Stop on an undeclared kind and on an audit-graded kind: intake never reviews labels, so no case is eligible.
    Stop when the selection names more than one kind without the journey grader: every draft task case takes one
    grading kind, so a second such kind would never score as itself.
    """
    chosen = tuple(dict.fromkeys(requested)) or _default_kinds(contract)
    plain = [kind for kind in chosen if kind in contract.kinds and kind not in contract.journey_kinds()]
    if len(plain) > 1:
        raise CodedError("contract-kinds", f"--kind names {', '.join(plain)}; a run scores at most one kind "
                         + "that does not use the journey grader")
    for kind in chosen:
        if kind not in contract.kinds:
            raise CodedError("contract-kinds", f"--kind {kind} names no declared kind; "
                             + f"the contract declares {', '.join(sorted(contract.kinds))}")
        # Audit grading is internal and deferred: it will ship with activation grading for trigger and near-miss cases (`_intake.py`).
        if contract.kinds[kind].type == "audit":
            raise CodedError("contract-audit-unsupported", f"the scored kind {kind!r} uses the audit grader; "
                             + "run does not support audit-graded kinds yet")
    return chosen


def plan_estimate(plan: Plan, repeats: int, holdout_cases: int) -> dict[str, object]:
    """Return the `run.json` estimate that `verify_budget` checks. `_prepare` adds the unchecked `reflection` block."""
    journey = ({"journey": {"calls": plan.sized.calls, "seconds": plan.sized.seconds,
                            "ceiling_calls": JOURNEY_CALLS, "ceiling_seconds": JOURNEY_SECONDS}}
               if plan.sized.journey else {})
    return {"calls": plan.sized.calls, "seconds": plan.sized.seconds, "search_calls": plan.metric_calls,
            "repeats": repeats, "holdout_cases": holdout_cases, "holdout_retry_calls": plan.retry_calls,
            "replay_calls": plan.replay_calls, "seconds_per_call": ESTIMATE_SECONDS_PER_CALL,
            "headroom": {"outcomes": plan.headroom_outcomes},
            "metric_calls": plan.metric_calls, "reflection_calls": plan.reflection_calls,
            **({"sandbox_seconds": plan.sandbox_total} if plan.sandbox_total else {}), **journey}

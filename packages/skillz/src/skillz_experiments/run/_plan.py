"""Plan the calls and seconds for a run and resolve the editable files.
"""
from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import cast, get_args

from skillz_experiments._budget import (
    JOURNEY_CALLS,
    JOURNEY_SECONDS,
    MAX_CONCURRENT_CALLS,
    Estimate,
    ceilings,
    estimate,
    gate_seconds,
    seconds_cap_note,
)
from skillz_experiments._candidate import NEW_CLI, WEDGE_PREFIX
from skillz_experiments._cases import Case, CodedError, Split
from skillz_experiments._contract import Contract
from skillz_experiments.run._wedge_targets import TargetPlan, mcp_text, never_editable
from skillz_experiments.harness import ClaudeOptions
from skillz_experiments.run._stop import Stop
from skillz_experiments.search import REFLECTION_MINIBATCH, Edit, search_allowance

PREFLIGHT_CALLS = 6
"""Three roles, each with two preflight calls: the Bash sandbox check and the Read file-access check (gh-176)."""


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
class _Plan:
    metric_calls: int
    reflection_calls: int
    holdout_calls: int
    retry_calls: int
    sized: Estimate
    replay_calls: int = 0
    sandbox_gate: int = 0
    sandbox_total: int = 0
    sandbox_seconds: int = 0
    headroom_calls: int = 0
    headroom_outcomes: int = 0

    @property
    def reserve(self) -> int:
        """Return the calls that the search leaves for the holdout gate and the replay."""
        return self.holdout_calls + self.replay_calls

    @property
    def reserve_seconds(self) -> int:
        """Return the seconds that the holdout gate and the replay hold, never more than the whole run."""
        return min(gate_seconds(self.reserve) + self.sandbox_gate, self.sized.seconds)


def _command_seconds(contract: Contract, cases: Sequence[Case], options: ClaudeOptions) -> int:
    """Return the sandbox time of one evaluation: one limit per sandbox run, taken at the costliest scored kind.

    A command or hybrid grader runs one sandbox. A `capture` argv runs one more. The result is 0 when no scored case runs one.
    """
    runs = max((int(grader.type in ("command", "hybrid", "journey")) + int(bool(grader.capture))
                for grader in (contract.grader(case.kind) for case in cases)), default=0)
    return runs * options.sandbox_seconds


def case_calls(contract: Contract, case: Case) -> int:
    """Return the worst-case invocation count of one evaluation of `case`. A journey case counts its `max_turns`."""
    return contract.calls(case.kind, case.journey.max_turns if case.journey is not None else 1)


def _plan(train: int, validation: int, holdout: int, per_evaluation: int, repeats: int, sandbox_seconds: int = 0,
          journey: bool = False, holdout_costs: Sequence[int] | None = None,
          headroom_costs: Sequence[int] | None = None) -> _Plan:
    """Size the run. Shrink the search share to fit the cap; stop when the fixed cost alone exceeds it.

    `metric` counts metric calls: one search evaluation of one case. A metric call costs `per_evaluation` model calls.
    `_Plan.metric_calls` and `reflection_calls` keep that unit. `holdout_calls` and `sized.calls` count model calls.
    `holdout_calls` is the gate reserve. It holds the gate and `HOLDOUT_RETRIES_PER_ARM` retries per arm,
    because a failed holdout call stays claimed.
    `replay_calls` is one pass of the winner over the holdout cases, for the replay phase. It adds to `sized.calls`,
    to the holdout reserve, and to the reserved seconds. The replay has no retry.
    `sandbox_seconds` is the sandbox time of one evaluation: the limit times the sandbox runs of the costliest scored kind.
    The sandbox time of every evaluation adds to the estimate and to the gate reserve, shared across parallel calls.
    The search shrinks until both the call cap and the time cap hold, sandbox time included.
    `journey` is True when a scored case is a journey. The run then gets the journey ceiling in place of the standard cap.
    A journey run also sizes the search from `JOURNEY_SEARCH_CALLS` model calls, so the search holds as many metric calls
    as that allowance buys at `per_evaluation`. A task-only run keeps `SEARCH_CASE_FACTOR` metric calls per searched run:
    one run per train case and `VALIDATION_REPEATS` runs per validation case. The admission check, the search
    allowance, and its overshoot margin count the same `VALIDATION_REPEATS` runs per validation case.
    `holdout_costs` lists the model calls of each holdout case. The default is `per_evaluation` for each of `holdout` cases.
    A retry costs the costliest holdout case, so the reserve covers one failed attempt of any case.
    The headroom pass scores the seed once on each train case and `VALIDATION_REPEATS` times on each validation case.
    Its validation scores stand in for the seed
    validation pass that GEPA already pays inside `metric_calls`, so only the train cases add cost. `headroom_costs` lists
    the model calls of each train case; the default is `per_evaluation` for each of `train` cases. The sum is
    `headroom_calls`. It adds to `sized.calls`, so the approved total and the ceilings include it. The count of train
    cases is `headroom_outcomes`: the search ceiling adds it, so the headroom train scores do not shrink the search.
    """
    max_calls, max_seconds = ceilings(journey)
    costs = list(holdout_costs) if holdout_costs is not None else [per_evaluation] * holdout
    holdout_evaluations = 2 * holdout * repeats + 2 * HOLDOUT_RETRIES_PER_ARM + holdout
    retry_calls = 2 * HOLDOUT_RETRIES_PER_ARM * (max(costs) if costs else per_evaluation)
    holdout_pass = sum(costs)
    headroom = sum(headroom_costs) if headroom_costs is not None else per_evaluation * train
    headroom_outcomes = len(headroom_costs) if headroom_costs is not None else train
    holdout_calls = 2 * holdout_pass * repeats + retry_calls
    if PREFLIGHT_CALLS + holdout_calls + holdout_pass > max_calls:
        raise Stop("budget-unapproved", f"the preflight, the holdout gate, and the replay alone need "
                   + f"{PREFLIGHT_CALLS + holdout_calls + holdout_pass} calls; the cap is {max_calls}"
                   + f"{seconds_cap_note(journey)}; lower --repeats or the holdout size", {})

    def sandbox_total(metric: int) -> int:
        return math.ceil((metric + headroom_outcomes + holdout_evaluations) * sandbox_seconds / MAX_CONCURRENT_CALLS)

    def size(metric: int) -> Estimate:
        return estimate(preflight_calls=PREFLIGHT_CALLS, holdout=2 * holdout_pass, calls_per_evaluation=1,
                        repeats=repeats, search_calls=metric * per_evaluation + headroom, retry_calls=retry_calls,
                        replay_calls=holdout_pass,
                        reflection_calls=math.ceil(metric / CALLS_PER_REFLECTION), journey=journey)

    def fits(metric: int) -> bool:
        sized = size(metric)
        return sized.calls <= max_calls and sized.seconds + sandbox_total(metric) <= max_seconds

    if not fits(0):
        bare = size(0)
        if bare.calls > max_calls:
            raise Stop("budget-unapproved", "the preflight, the holdout gate, the replay, and the headroom pass alone need "
                       + f"{bare.calls} calls; the cap is {max_calls}{seconds_cap_note(journey)}; "
                       + "lower --repeats or the holdout size", {})
        raise Stop("budget-unapproved", "the preflight, the holdout gate, the replay, the headroom pass, and their sandbox "
                   + f"time alone need {bare.seconds + sandbox_total(0)} seconds; the cap is {max_seconds}"
                   + f"{seconds_cap_note(journey)}; lower --repeats, the holdout size, or --sandbox-seconds", {})
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
    return _Plan(metric_calls=metric, reflection_calls=math.ceil(metric / CALLS_PER_REFLECTION), holdout_calls=holdout_calls,
                 retry_calls=retry_calls, sized=Estimate(sized.calls, sized.seconds + total, journey),
                 replay_calls=holdout_pass, sandbox_gate=sandbox_gate, sandbox_total=total, sandbox_seconds=sandbox_seconds,
                 headroom_calls=headroom, headroom_outcomes=headroom_outcomes)


def size_plan(contract: Contract, cases: Sequence[Case], repeats: int, options: ClaudeOptions) -> _Plan:
    """Size the run from per-case call costs. `_prepare` and `_verify_budget` share this, so their estimates match.

    The holdout reserve sums the holdout case costs. A search metric call costs the mean over the searched runs:
    each train case once and each validation case `VALIDATION_REPEATS` times.
    The headroom pass charges the train case costs only. GEPA pays the validation pass of the seed in its own calls.
    """
    split = {name: [case_calls(contract, case) for case in cases if case.split == name]
             for name in cast(tuple[str, ...], get_args(Split))}
    searched = split["train"] + VALIDATION_REPEATS * split["validation"]
    per_evaluation = max(1, math.ceil(sum(searched) / len(searched))) if searched else 1
    return _plan(len(split["train"]), len(split["validation"]), len(split["holdout"]), per_evaluation, repeats,
                 _command_seconds(contract, cases, options), journey=any(case.journey is not None for case in cases),
                 holdout_costs=split["holdout"], headroom_costs=split["train"])


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


REFLECTION_TEXT_LIMIT = 200_000
"""The editable characters above which a reflection call risks its per-call timeout (gh-563).

A reflection prompt holds every editable file, and the answer returns every component in full.
"""


def editable_chars(files: Mapping[str, str], editable: Sequence[str]) -> int:
    """Return the characters of the editable files, which every reflection prompt holds."""
    return sum(len(files.get(name, "")) for name in editable)


def reflection_warning(chars: int, edit: Edit) -> str | None:
    """Return the budget-question warning for `chars` editable characters, or None at or below the limit."""
    if chars <= REFLECTION_TEXT_LIMIT:
        return None
    advice = ("run with --edit prose, or " if edit == "prose+cli" else "") + "name fewer files in the contract editable list"
    return (f"the editable text holds {chars} characters, more than the reflection limit of {REFLECTION_TEXT_LIMIT}; "
            + f"each reflection call reads and returns all of it, so it can reach its per-call timeout; {advice}")


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


def plan_estimate(plan: _Plan, repeats: int, holdout_cases: int) -> dict[str, object]:
    """Return the estimate that `run.json` records. `_prepare` writes it and `_verify_budget` rebuilds it."""
    journey = ({"journey": {"calls": plan.sized.calls, "seconds": plan.sized.seconds,
                            "ceiling_calls": JOURNEY_CALLS, "ceiling_seconds": JOURNEY_SECONDS}}
               if plan.sized.journey else {})
    return {"calls": plan.sized.calls, "seconds": plan.sized.seconds, "search_calls": plan.metric_calls,
            "repeats": repeats, "holdout_cases": holdout_cases, "holdout_retry_calls": plan.retry_calls,
            "replay_calls": plan.replay_calls,
            "headroom": {"calls": plan.headroom_calls, "outcomes": plan.headroom_outcomes},
            "metric_calls": plan.metric_calls, "reflection_calls": plan.reflection_calls,
            **({"sandbox_seconds": plan.sandbox_total} if plan.sandbox_total else {}), **journey}

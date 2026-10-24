from __future__ import annotations

import json
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, cast

import fromargs

from skillz_experiments._cases import CodedError
from skillz_experiments._facts import audit_facts
from skillz_experiments._scaffold import scaffold
from skillz_experiments._schemas import SchemaName, render as render_schema
from skillz_experiments._validate import validate as check_target
from skillz_experiments.gate import (
    CHEAPER_SCENARIOS, POWER_EFFECTS, simulate as simulate_gate, simulate_cheaper, simulate_power,
)
from skillz_experiments._records import read
from skillz_experiments._budget import Budget
from skillz_experiments.search import Edit
from skillz_experiments.run import Stop, export as export_run, run as run_workflow
from skillz_experiments.harness import Configuration, Effort, configured_selection, doctor, stop_kind
from skillz_experiments.wrapper import register as register_wrappers

Harness = Literal["claude", "codex"]
RunHarness = Literal["claude", "codex", "command"]
Metric = Literal[
    "false_promotions_2se", "false_promotions_one_holdout", "rate_2se", "rate_one_holdout",
    "true_promotions_effect_10", "true_promotions_effect_20", "true_promotions_effect_30",
    "power_effect_10", "power_effect_20", "power_effect_30",
    "cheaper_loss_5_margin_0", "cheaper_loss_5_margin_2", "cheaper_loss_5_margin_5", "cheaper_loss_0_margin_5"]
REMOVED_COMMANDS = frozenset({"dataset", "baseline", "search", "evaluate"})


class _Reported(BaseException):
    """A coded error that the command already wrote to stderr; `fromargs` lets it pass."""


@contextmanager
def _coded() -> Generator[None]:
    """Report a `CodedError` as JSON with its `code` on stderr. Then raise `_Reported`.

    A `Stop` also prints its question data on stdout, so the outer agent can read it.
    """
    try:
        yield
    except CodedError as error:
        if isinstance(error, Stop):
            print(json.dumps(error.data))
        report: dict[str, object] = {"error": str(error), "exit_code": 1, "code": error.code}
        if error.details is not None:
            report["data"] = dict(error.details)
        print(json.dumps(report), file=sys.stderr)
        raise _Reported from None


app = fromargs.App("skillz-experiment", help="Local, bounded skill experiments. No automatic installation.")


def _completed(out: Path) -> bool:
    try:
        return read(out / "run.json").get("phase") == "complete"
    except (OSError, ValueError):
        return False


def _run_harness(model: str, target: Path, harness: RunHarness | None, harness_config: Path | None,
                 command: list[str] | None, out: Path) -> tuple[str, Configuration | None]:
    """Resolve the harness options of `run` before any work. Return the adapter name and the configuration, if any.

    `harness` is None when the user gives no `--harness`, so a conflict with `--harness-config` shows.
    A completed run in `out` needs no harness, so a configuration that no longer loads does not stop its summary.
    """
    if harness_config is not None and (harness is not None or command is not None):
        raise CodedError("usage", "--harness-config cannot combine with --harness or --command")
    if command is not None and harness != "command":
        raise CodedError("usage", "--command requires --harness command")
    if harness == "command" and command is None:
        raise CodedError("usage", "--harness command requires --command ARGV...")
    if harness_config is None and harness != "command":
        return harness or "claude", None
    try:
        configuration = (Configuration.load(harness_config, model) if harness_config is not None
                         else Configuration.command(cast(list[str], command), model))
        configuration.check_boundary(target)
    except (CodedError, ValueError, OSError):
        if not _completed(out):
            raise
        return "claude", None
    return configured_selection(harness), configuration


@app.command(name="run")
def run_command(*, target: Path, out: Path, model: str, judge_model: str | None = None,
                harness: RunHarness | None = None,
                harness_config: Path | None = None, command: list[str] | None = None,
                live: bool = False, edit: Edit | None = None, repeats: int | None = None, seed: int | None = None,
                approve_cases: str | None = None, approve_budget: int | None = None,
                effort: Effort | None = None, sandbox_read: list[Path] | None = None,
                sandbox_seconds: int | None = None, min_gain: float | None = None,
                min_lower_bound: float | None = None, family_budget: float | None = None,
                max_token_increase_per_gain: float | None = None,
                min_token_saving: float | None = None, min_coverage: float | None = None,
                max_quality_loss: float | None = None, history: bool | None = None,
                kind: list[str] | None = None) -> dict[str, object]:
    """Run or resume one autoimprove run: cases, one search, one holdout gate. Stops return a question.

    Parameters
    ----------
    harness
        claude (default) or codex, or command for your own command (requires --command).
    harness_config
        Harness configuration file with per-role adapters. Cannot combine with --harness or --command.
    command
        Argument array of your own harness command for --harness command: --command EXECUTABLE ARG...
    judge_model
        Model of the judge calls. Task and reflection calls use --model. Defaults to --model.
    edit
        prose+cli (default) also edits helper scripts and own wedge sources. prose edits only Markdown.
        With no --edit, a skill with no editable script uses prose.
    effort
        Claude Code effort level for every role (claude harness only).
    sandbox_read
        Absolute host path that the runner's OS sandbox mounts read-only. Repeat for more paths.
    sandbox_seconds
        Time limit of one OS sandbox command, in seconds.
    min_gain
        Smallest mean holdout delta that can promote. At least 0. Overrides the contract value.
    min_lower_bound
        Margin that the delta must clear beyond 2·SE. At least 0. Overrides the contract value.
    family_budget
        Regression budget of every holdout family. At least 0. A family in the contract `family_budgets` keeps its own.
    max_token_increase_per_gain
        Largest token change per unit of score gain. At least 0. Turns the token rule on. Overrides the contract value.
    min_token_saving
        Smallest token saving, as a share above 0 and below 1. Turns the token rule on. Overrides the contract value.
    min_coverage
        Least share of planned holdout cases scored on both arms, from 0 to 1. Below it the gate is inconclusive.
        0 turns the rule off. Overrides the contract value.
    max_quality_loss
        Largest plausible quality loss, as a score per case, that `promote-cheaper` accepts. At least 0.
        `--min-token-saving` needs it. Overrides the contract value.
    history
        Keep and read the private cross-run attempt history (on by default). --no-history turns it off.
        The first run records the choice; a resume must not change it.
    kind
        Contract kind to score. Repeat for more. Default: task drives the split and journey kinds are still evaluated, else all journey kinds.
        With --kind, a drafted case of another kind stays out of the run. The first run freezes the choice.
    """
    flags = {"min_gain": min_gain, "min_lower_bound": min_lower_bound, "family_budget": family_budget,
             "max_token_increase_per_gain": max_token_increase_per_gain, "min_token_saving": min_token_saving,
             "min_coverage": min_coverage, "max_quality_loss": max_quality_loss}
    statistics = {name: value for name, value in flags.items() if value is not None}
    with _coded():
        adapter, configuration = _run_harness(model, target, harness, harness_config, command, out)
        return run_workflow(target, out, model, adapter=adapter, configuration=configuration, live=live, edit=edit,
                            repeats=repeats, seed=seed,
                            approve_cases=approve_cases, approve_budget=approve_budget, effort=effort,
                            sandbox_read=None if sandbox_read is None else [str(path) for path in sandbox_read],
                            sandbox_seconds=sandbox_seconds, statistics=statistics or None,
                            judge_model=judge_model, history=history, kinds=kind or ())


@app.command(name="doctor")
def doctor_command(*, harness: Harness = "claude") -> dict[str, object]:
    """Check the host for a run with no model call: tools, login, sandbox, and skill isolation, in one pass.

    The command exits 0 even when a check fails. Read `ok` in the report: it is false when any check fails.
    """
    with _coded():
        return doctor(harness)


@app.command(name="export")
def export_command(run: Path, *, out: Path) -> dict[str, object]:
    """Write the winner as a private local patch and a redacted report, without installation."""
    with _coded():
        return export_run(run, out)


@app.command(name="audit-facts")
def audit_facts_command(directory: Path) -> dict[str, object]:
    """Report fixed rubric checks for a skill directory as facts, without grading it."""
    return audit_facts(directory)


@app.command(name="scaffold")
def scaffold_command(*, target: Path) -> dict[str, object]:
    """Write a draft journey contract to `<target>/evals/autoimprove.json`. It makes no model call and never replaces a contract."""
    with _coded():
        return scaffold(target)


@app.command(name="validate")
def validate_command(*, target: Path, draft: Path | None = None, seed: int | None = None,
                     kind: list[str] | None = None) -> dict[str, object]:
    """Check the eval files and the split that a run would build. No model call, no host check, no write.

    The command exits 1 when any finding has severity error. Read `findings` for the code, file, and fix of each.

    Parameters
    ----------
    draft
        Also check this case draft: its fields, the helper input, and the coverage of the seed entries.
    seed
        Split seed. Default: the seed that `run` uses.
    kind
        Contract kind to score. Repeat for more. Same rule as `run --kind`.
    """
    report = check_target(target, draft, seed, kind or ())
    if not report["ok"]:
        print(json.dumps(report))
        raise _Reported
    return report


@app.command(name="schema")
def schema_command(name: SchemaName) -> None:
    """Print the JSON Schema of an author file: evals, journeys, contract, or draft. Python stays the source of truth."""
    _ = sys.stdout.write(render_schema(name))


def _simulation_report() -> dict[str, object]:
    result = simulate_gate()
    report: dict[str, object] = {
        "trials": result.trials, "cases": result.cases,
        "false_promotions_2se": result.false_promotions_2se,
        "false_promotions_one_holdout": result.false_promotions_one_holdout,
        "rate_2se": result.rate_2se, "rate_one_holdout": result.rate_one_holdout, "live_calls": 0}
    for effect in POWER_EFFECTS:
        power = simulate_power(effect)
        label = round(effect * 100)
        report[f"true_promotions_effect_{label}"] = power.promotions
        report[f"power_effect_{label}"] = power.rate
    for loss, margin in CHEAPER_SCENARIOS:
        report[_cheaper_key(loss, margin)] = simulate_cheaper(loss, margin).rate
    return report


def _cheaper_key(loss: float, margin: float) -> str:
    """Name a `promote-cheaper` rate by its true loss and margin in hundredths of a score."""
    return f"cheaper_loss_{round(loss * 100)}_margin_{round(margin * 100)}"


def _simulation_figure(metric: Metric) -> int | float:
    """Compute only the figure that `metric` names; it equals the same key of the full report."""
    for effect in POWER_EFFECTS:
        label = round(effect * 100)
        if metric == f"true_promotions_effect_{label}":
            return simulate_power(effect).promotions
        if metric == f"power_effect_{label}":
            return simulate_power(effect).rate
    for loss, margin in CHEAPER_SCENARIOS:
        if metric == _cheaper_key(loss, margin):
            return simulate_cheaper(loss, margin).rate
    return cast(int | float, getattr(simulate_gate(), metric))


@app.command(name="self-test")
def self_test(*, model: str | None = None, preflight_only: bool = False, simulate: bool = False,
              metric: Metric | None = None, harness_config: Path | None = None, max_invocations: int = 20,
              max_seconds: float = 1200) -> dict[str, object] | int | float:
    """Check the harness without a run (--preflight-only) or print the gate's false-promotion rates and power (--simulate).

    With --simulate, --metric NAME prints only that number, so `ratchet.py measure` can read it.
    With --preflight-only and no --harness-config, every role runs on Claude Code, as `run` does.
    """
    if preflight_only == simulate:
        raise ValueError("choose exactly one of --preflight-only or --simulate")
    if metric is not None and not simulate:
        raise ValueError("--metric requires --simulate")
    if simulate:
        return _simulation_report() if metric is None else _simulation_figure(metric)
    if model is None:
        raise ValueError("--preflight-only requires --model")
    with _coded():
        configuration = (Configuration.load(harness_config, model) if harness_config is not None
                         else Configuration.single("claude", model))
        adapter = configuration.create(model, Budget(max_invocations, max_seconds, reserve=0), lambda: None)
        warning: str | None = None
        try:
            result = adapter.preflight()
        finally:
            try:
                adapter.close()
            except CodedError as error:
                if stop_kind(error.code) != "notice":
                    raise
                warning = str(error)
        return result if warning is None else result | {"close_warning": warning}


register_wrappers(app.group("harness-wrapper", help="Reference wrappers for the command adapter. Each speaks JSON protocol version 1."))


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if arguments and arguments[0] in REMOVED_COMMANDS:
            with _coded():
                raise CodedError("command-removed", f"`{arguments[0]}` is removed; use `run` for the whole flow, "
                                 + "then `export` to write the patch")
        return app.run(argv)
    except _Reported:
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error), "exit_code": 1}), file=sys.stderr)
        return 1
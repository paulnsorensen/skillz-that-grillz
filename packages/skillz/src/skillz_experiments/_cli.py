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
from skillz_experiments.gate import simulate as simulate_gate
from skillz_experiments._records import read
from skillz_experiments._budget import Budget
from skillz_experiments.search import Edit
from skillz_experiments.run import Stop, export as export_run, run as run_workflow
from skillz_experiments.harness import Configuration, Effort, configured_selection, doctor, stop_kind
from skillz_experiments.wrapper import IMAGE as GEMINI_IMAGE, Runtime, respond as gemini_respond

Harness = Literal["claude", "codex"]
RunHarness = Literal["claude", "codex", "command"]
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
        print(json.dumps({"error": str(error), "exit_code": 1, "code": error.code}), file=sys.stderr)
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
                min_token_saving: float | None = None, min_coverage: float | None = None) -> dict[str, object]:
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
    """
    flags = {"min_gain": min_gain, "min_lower_bound": min_lower_bound, "family_budget": family_budget,
             "max_token_increase_per_gain": max_token_increase_per_gain, "min_token_saving": min_token_saving,
             "min_coverage": min_coverage}
    statistics = {name: value for name, value in flags.items() if value is not None}
    with _coded():
        adapter, configuration = _run_harness(model, target, harness, harness_config, command, out)
        return run_workflow(target, out, model, adapter=adapter, configuration=configuration, live=live, edit=edit,
                            repeats=repeats, seed=seed,
                            approve_cases=approve_cases, approve_budget=approve_budget, effort=effort,
                            sandbox_read=None if sandbox_read is None else [str(path) for path in sandbox_read],
                            sandbox_seconds=sandbox_seconds, statistics=statistics or None,
                            judge_model=judge_model)


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


@app.command(name="self-test")
def self_test(*, model: str | None = None, preflight_only: bool = False, simulate: bool = False,
              harness_config: Path | None = None, max_invocations: int = 20,
              max_seconds: float = 1200) -> dict[str, object]:
    """Check the harness without a run (--preflight-only) or print the gate's false-promotion rates (--simulate)."""
    if preflight_only == simulate:
        raise ValueError("choose exactly one of --preflight-only or --simulate")
    if simulate:
        result = simulate_gate()
        return {"trials": result.trials, "cases": result.cases,
                "false_promotions_2se": result.false_promotions_2se,
                "false_promotions_one_holdout": result.false_promotions_one_holdout,
                "rate_2se": result.rate_2se, "rate_one_holdout": result.rate_one_holdout, "live_calls": 0}
    if model is None:
        raise ValueError("--preflight-only requires --model")
    with _coded():
        configuration = Configuration.load(harness_config, model)
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


group = app.group("harness-wrapper", help="Reference wrappers for the command adapter. Each speaks JSON protocol version 1.")


@group.command(name="gemini")
def gemini_command(*, bin_dir: Path | None = None, runtime: Runtime | None = None, image: str = GEMINI_IMAGE) -> None:
    """Run Gemini CLI with its native sandbox behind the command adapter. Read one request on stdin, answer on stdout."""
    response = gemini_respond(sys.stdin.read(), bin_dir=bin_dir, runtime=runtime, image=image)
    _ = sys.stdout.write(json.dumps(response) + "\n")


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
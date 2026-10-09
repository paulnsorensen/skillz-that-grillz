from __future__ import annotations

import json
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import fromargs

from skillz_experiments._cases import CodedError
from skillz_experiments._claude import NOTICE_CODES, Isolation
from skillz_experiments._doctor import doctor
from skillz_experiments._facts import audit_facts
from skillz_experiments._gate import simulate as simulate_gate
from skillz_experiments._harness import Configuration
from skillz_experiments._runtime import Budget
from skillz_experiments._search import Edit
from skillz_experiments._workflow import Stop, export as export_run, run as run_workflow

Harness = Literal["claude", "codex"]
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


@app.command(name="run")
def run_command(*, target: Path, out: Path, model: str, harness: Harness = "claude", isolation: Isolation = "claude",
                live: bool = False, edit: Edit | None = None, repeats: int | None = None, seed: int | None = None,
                approve_cases: str | None = None, approve_budget: int | None = None) -> dict[str, object]:
    """Run or resume one autoimprove run: cases, one search, one holdout gate. Stops return a question.

    `--isolation nono` confines the whole Claude process with nono on Linux and needs ANTHROPIC_API_KEY on the host.
    """
    with _coded():
        return run_workflow(target, out, model, adapter=harness, live=live, edit=edit, repeats=repeats, seed=seed,
                            approve_cases=approve_cases, approve_budget=approve_budget, isolation=isolation)


@app.command(name="doctor")
def doctor_command(*, harness: Harness = "claude", isolation: Isolation = "claude") -> dict[str, object]:
    """Check the host for a run with no model call: tools, login, sandbox, and skill isolation, in one pass.

    The command exits 0 even when a check fails. Read `ok` in the report: it is false when any check fails.
    """
    with _coded():
        return doctor(harness, isolation)


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
        return {"trials": result.trials, "cases": result.cases, "rate_2se": result.rate_2se,
                "rate_one_holdout": result.rate_one_holdout, "live_calls": 0}
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
                if error.code not in NOTICE_CODES:
                    raise
                warning = str(error)
        return result if warning is None else result | {"close_warning": warning}


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
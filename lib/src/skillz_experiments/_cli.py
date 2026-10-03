from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

import fromargs

from skillz_experiments._cases import CodedError, load_cases, load_manifest
from skillz_experiments._contract import load_contract
from skillz_experiments._harness import Configuration
from skillz_experiments._records import prepare, read, write
from skillz_experiments._runtime import Budget
from skillz_experiments._search import Mode
from skillz_experiments._workflow import execute, export as export_run

Profile = Literal["inspection", "audit"]
SELF_TEST_OUT = Path("skillz-self-test")

class _Reported(BaseException):
    """A coded error that the command already wrote to stderr; `fromargs` lets it pass."""


app = fromargs.App("skillz-experiment", help="Local, bounded skill experiments. No automatic installation.")


@app.command
def dataset(manifest: Path, *, target: Path, out: Path, component: list[str] | None = None) -> dict[str, object]:
    """Validate authored cases or an approved normalized analytics export."""
    try:
        return prepare(manifest, target, out, component, load_contract(target, load_manifest(manifest)))
    except CodedError as error:
        print(json.dumps({"error": str(error), "exit_code": 1, "code": error.code}), file=sys.stderr)
        raise _Reported from None


@app.command
def baseline(run: Path, *, model: str, live: bool = False, harness_config: Path | None = None,
             max_invocations: int = 20, max_seconds: float = 1200) -> dict[str, object]:
    """Measure the frozen original on train and validation cases."""
    return execute(run, "baseline", model, live=live, maximum=max_invocations, seconds=max_seconds,
                   harness_config=harness_config)


@app.command
def search(run: Path, *, model: str, mode: Mode = "prompt", live: bool = False, harness_config: Path | None = None,
           max_invocations: int = 20, max_seconds: float = 1200) -> dict[str, object]:
    """Search prompt, prompt-cli, or cli components with pinned GEPA."""
    return execute(run, "search", model, live=live, mode=mode, maximum=max_invocations, seconds=max_seconds,
                   harness_config=harness_config)


@app.command
def evaluate(run: Path, *, model: str, live: bool = False, harness_config: Path | None = None,
             max_invocations: int = 20, max_seconds: float = 1200) -> dict[str, object]:
    """Consume the paired holdout once, without feedback to search."""
    return execute(run, "evaluate", model, live=live, maximum=max_invocations, seconds=max_seconds,
                   harness_config=harness_config)


@app.command(name="export")
def export_command(run: Path, *, out: Path, arm: Mode = "prompt") -> dict[str, object]:
    """Export a private local patch and redacted evidence, without installation."""
    return export_run(run, out, arm)


@app.command(name="self-test")
def self_test(*, model: str, out: Path = SELF_TEST_OUT, target: Path | None = None,
              preflight_only: bool = False, live: bool = False, profile: Profile = "inspection",
              prepare_only: bool = False, manifest: Path | None = None, harness_config: Path | None = None,
              max_invocations: int = 20, max_seconds: float = 1200) -> dict[str, object]:
    """Compare the original, prompt-only, and prompt-plus-helper arms."""
    if sum((preflight_only, prepare_only, live)) != 1:
        raise ValueError("choose exactly one of --preflight-only, --prepare-only, or --live")
    source = manifest or Path(__file__).parent / (
        "fixtures/audit-self-test.json" if profile == "audit" else "fixtures/self-test.json")
    if prepare_only:
        cases = load_cases(source)
        out.mkdir(mode=0o700)
        write(out / "manifest.json", read(source))
        return {"review_manifest": str(out / "manifest.json"), "cases": len(cases), "live_calls": 0}
    if preflight_only:
        configuration = Configuration.load(harness_config, model)
        adapter = configuration.create(model, Budget(max_invocations, max_seconds, reserve=0), lambda: None)
        try:
            return adapter.preflight()
        finally:
            adapter.close()
    if not live:
        raise ValueError("self-test requires --preflight-only or explicit --live")
    if profile == "audit":
        cases = load_cases(source)
        if not cases or any(case.kind != "audit" or not case.eligible for case in cases):
            raise ValueError("audit self-test requires human label review and separate provider approval in --manifest")
    if target is None:
        archive = Path(sys.argv[0]).resolve()
        if archive.name != "skillz-experiment.pyz" or not archive.is_file():
            raise ValueError("source execution requires an explicit --target")
        target = archive.parent.parent
    _ = prepare(source, target, out)
    return execute(out, "self-test", model, live=True, maximum=max_invocations, seconds=max_seconds,
                   harness_config=harness_config)

def main(argv: list[str] | None = None) -> int:
    try:
        return app.run(argv)
    except _Reported:
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        report: dict[str, object] = {"error": str(error), "exit_code": 1}
        if isinstance(error, CodedError):
            report["code"] = error.code
        print(json.dumps(report), file=sys.stderr)
        return 1
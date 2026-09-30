from __future__ import annotations

import json
import sys
from pathlib import Path

import fromargs

from skillz_experiments._cases import load_cases
from skillz_experiments._harness import Configuration
from skillz_experiments._records import prepare, read, write
from skillz_experiments._runtime import Budget
from skillz_experiments._workflow import execute, export as export_run

app = fromargs.App("skillz-experiment", help="Local, bounded skill experiments. No automatic installation.")


@app.command
def dataset(manifest: str, *, target: str, out: str, component: list[str] | None = None) -> dict[str, object]:
    """Validate authored cases or an approved normalized analytics export."""
    return prepare(Path(manifest), Path(target), Path(out), component)


@app.command
def baseline(run: str, *, model: str, live: bool = False, harness_config: str | None = None,
             max_invocations: int = 20, max_seconds: float = 1200) -> dict[str, object]:
    """Measure the frozen original on training and validation cases."""
    return execute(Path(run), "baseline", model, live=live, maximum=max_invocations, seconds=max_seconds,
                   harness_config=Path(harness_config) if harness_config else None)


@app.command
def search(run: str, *, model: str, mode: str = "prompt", live: bool = False, harness_config: str | None = None,
           max_invocations: int = 20, max_seconds: float = 1200) -> dict[str, object]:
    """Search prompt, prompt-cli, or cli components with pinned GEPA."""
    return execute(Path(run), "search", model, live=live, mode=mode, maximum=max_invocations, seconds=max_seconds,
                   harness_config=Path(harness_config) if harness_config else None)


@app.command
def evaluate(run: str, *, model: str, live: bool = False, harness_config: str | None = None,
             max_invocations: int = 20, max_seconds: float = 1200) -> dict[str, object]:
    """Consume the paired holdout once, without feedback to search."""
    return execute(Path(run), "evaluate", model, live=live, maximum=max_invocations, seconds=max_seconds,
                   harness_config=Path(harness_config) if harness_config else None)


@app.command(name="export")
def export_command(run: str, *, out: str, arm: str = "prompt") -> dict[str, object]:
    """Export a private local patch and redacted evidence, without installation."""
    return export_run(Path(run), Path(out), arm)


@app.command(name="self-test")
def self_test(*, model: str, out: str = "skillz-self-test", target: str | None = None,
              preflight_only: bool = False, live: bool = False, profile: str = "inspection",
              prepare_only: bool = False, manifest: str | None = None, harness_config: str | None = None,
              max_invocations: int = 20, max_seconds: float = 1200) -> dict[str, object]:
    """Compare the original, prompt-only, and prompt-plus-CLI arms."""
    if profile not in ("inspection", "audit"):
        raise ValueError("profile must be inspection or audit")
    if sum((preflight_only, prepare_only, live)) != 1:
        raise ValueError("choose exactly one of --preflight-only, --prepare-only, or --live")
    source = Path(manifest) if manifest else Path(__file__).parent / (
        "fixtures/audit-self-test.json" if profile == "audit" else "fixtures/self-test.json")
    if prepare_only:
        cases = load_cases(source)
        destination = Path(out)
        destination.mkdir(mode=0o700)
        write(destination / "manifest.json", read(source))
        return {"review_manifest": str(destination / "manifest.json"), "cases": len(cases), "live_calls": 0}
    if preflight_only:
        configuration = Configuration.load(Path(harness_config) if harness_config else None, model)
        adapter = configuration.create(model, Budget(max_invocations, max_seconds), lambda: None)
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
        target = str(archive.parent.parent)
    _ = prepare(source, Path(target), Path(out))
    return execute(Path(out), "self-test", model, live=True, maximum=max_invocations, seconds=max_seconds,
                   harness_config=Path(harness_config) if harness_config else None)


def main(argv: list[str] | None = None) -> int:
    try:
        return app.run(argv)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error), "exit_code": 1}), file=sys.stderr)
        return 1
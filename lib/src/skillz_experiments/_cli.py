from __future__ import annotations

import json
import sys
from pathlib import Path

import fromargs

from skillz_experiments._codex import Codex
from skillz_experiments._records import prepare
from skillz_experiments._runtime import Budget
from skillz_experiments._workflow import execute, export as export_run

app = fromargs.App("skillz-experiment", help="Local, bounded skill experiments. No automatic installation.")


@app.command
def dataset(manifest: str, *, target: str, out: str, component: list[str] | None = None) -> dict[str, object]:
    """Validate authored cases or an approved normalized analytics export."""
    return prepare(Path(manifest), Path(target), Path(out), component)


@app.command
def baseline(run: str, *, model: str, live: bool = False,
             max_invocations: int = 20, max_seconds: float = 1200) -> dict[str, object]:
    """Measure the frozen original on training and validation cases."""
    return execute(Path(run), "baseline", model, live=live, maximum=max_invocations, seconds=max_seconds)


@app.command
def search(run: str, *, model: str, mode: str = "prompt", live: bool = False,
           max_invocations: int = 20, max_seconds: float = 1200) -> dict[str, object]:
    """Search prompt or prompt-cli components with pinned GEPA."""
    return execute(Path(run), "search", model, live=live, mode=mode, maximum=max_invocations, seconds=max_seconds)


@app.command
def evaluate(run: str, *, model: str, live: bool = False,
             max_invocations: int = 20, max_seconds: float = 1200) -> dict[str, object]:
    """Consume the paired holdout once, without feedback to search."""
    return execute(Path(run), "evaluate", model, live=live, maximum=max_invocations, seconds=max_seconds)


@app.command(name="export")
def export_command(run: str, *, out: str, arm: str = "prompt") -> dict[str, object]:
    """Export a private local patch and redacted evidence, without installation."""
    return export_run(Path(run), Path(out), arm)


@app.command(name="self-test")
def self_test(*, model: str, out: str = "skillz-self-test", target: str = "skills/skillz",
              preflight_only: bool = False, live: bool = False,
              max_invocations: int = 20, max_seconds: float = 1200) -> dict[str, object]:
    """Compare the original, prompt-only, and prompt-plus-CLI arms."""
    if preflight_only:
        adapter = Codex(model, Budget(max_invocations, max_seconds), lambda: None)
        try:
            return adapter.preflight()
        finally:
            adapter.close()
    if not live:
        raise ValueError("self-test requires --preflight-only or explicit --live")
    manifest = Path(__file__).parent / "fixtures/self-test.json"
    _ = prepare(manifest, Path(target), Path(out))
    return execute(Path(out), "self-test", model, live=True, maximum=max_invocations, seconds=max_seconds)


def main(argv: list[str] | None = None) -> int:
    try:
        return app.run(argv)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error), "exit_code": 1}), file=sys.stderr)
        return 1
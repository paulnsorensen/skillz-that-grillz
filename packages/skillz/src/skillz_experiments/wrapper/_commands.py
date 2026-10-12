"""The wrapper commands. The slice registers them on the app that the CLI hands in, so the CLI names no vendor."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import fromargs

from skillz_experiments.wrapper._gemini import IMAGE, Runtime, respond


def register(app: fromargs.App) -> None:
    """Add the `harness-wrapper` group, with one command per reference wrapper, to `app`."""
    group = app.group("harness-wrapper", help="Reference wrappers for the command adapter. Each speaks JSON protocol version 1.")

    @group.command(name="gemini")
    def gemini_command(*, bin_dir: Path | None = None, runtime: Runtime | None = None, image: str = IMAGE) -> None:
        """Run Gemini CLI with its native sandbox behind the command adapter. Read one request on stdin, answer on stdout."""
        response = respond(sys.stdin.read(), bin_dir=bin_dir, runtime=runtime, image=image)
        _ = sys.stdout.write(json.dumps(response) + "\n")

"""The wrapper commands. The slice adds them to the group that the CLI hands in, so the CLI names no vendor and the slice never imports the CLI framework at runtime."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from skillz_experiments.wrapper._gemini import IMAGE, Runtime, respond

if TYPE_CHECKING:
    import fromargs


def register(group: fromargs.App) -> None:
    """Add one command per reference wrapper to `group`, the `harness-wrapper` group that the CLI creates."""
    @group.command(name="gemini")
    def gemini_command(*, bin_dir: Path | None = None, runtime: Runtime | None = None, image: str = IMAGE) -> None:
        """Run Gemini CLI with its native sandbox behind the command adapter. Read one request on stdin, answer on stdout."""
        response = respond(sys.stdin.read(), bin_dir=bin_dir, runtime=runtime, image=image)
        _ = sys.stdout.write(json.dumps(response) + "\n")

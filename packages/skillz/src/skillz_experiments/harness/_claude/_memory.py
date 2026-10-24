from __future__ import annotations

import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import final

from skillz_experiments._budget import Budget, BudgetExhausted
from skillz_experiments._candidate import make_workspace
from skillz_experiments.harness._claude._constants import AUTH_FAILED, LOGIN_HINT
from skillz_experiments.harness._claude._events import failure, final_event, parse_events
from skillz_experiments.harness._claude._login import listed_memory, login_failed, preflight_leak, user_memory


CONTEXT_REQUEST = "/context"
"""`/context` is a local command: Claude Code answers it with no model call. Its report lists each loaded memory file."""
CONTEXT_HEADER = "## Context Usage"
CONTEXT_SECONDS = 60


Start = Callable[[Path, str, float], subprocess.CompletedProcess[str]]
"""Start Claude Code in a workspace with a stdin text and a timeout, and return the finished process."""


@final
class UserMemory:
    """Prove that user memory in the real macOS config directory stays out of each Claude Code start (#131).

    `--setting-sources project` keeps user memory out. When the real config directory holds `CLAUDE.md` or `rules`,
    one free `/context` listing per role proves it: it makes no model call. A listing that names the memory, or
    that does not run, stops the run with `preflight-leak`. Linux uses a temporary config directory, so it needs no check.
    """

    def __init__(self, config_dir: Path, budget: Budget, start: Start) -> None:
        self._config_dir = config_dir
        self._budget = budget
        self._start = start
        self._checked = False

    def check(self) -> None:
        """On macOS, stop before any model call when user memory loads.

        A passing listing holds for the rest of the process. Until one runs, each call scans the config directory again,
        so memory that appears during the run still gets its listing.
        """
        if sys.platform != "darwin" or self._checked:
            return
        found = user_memory(self._config_dir)
        if not found:
            return
        loaded = listed_memory(self._listing(), self._config_dir, found)
        if loaded:
            raise preflight_leak(f"user memory {', '.join(loaded)} in the real config directory {self._config_dir} "
                                 + "loads; move it out for the run")
        self._checked = True

    def _listing(self) -> str:
        """Return the `/context` text of one free Claude Code start in a fresh workspace. No model turn runs."""
        with tempfile.TemporaryDirectory(prefix="skillz-memory-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            timeout = min(CONTEXT_SECONDS, self._budget.remaining())
            try:
                result = self._start(workspace, CONTEXT_REQUEST, timeout)
            except BudgetExhausted:
                if timeout < CONTEXT_SECONDS:
                    raise
                raise preflight_leak(f"the Claude Code memory listing timed out after {CONTEXT_SECONDS} seconds") from None
        events = parse_events(result.stdout)
        final = final_event(events)
        reported = None if final is None else final.get("result")
        text = reported if isinstance(reported, str) else ""
        if result.returncode != 0 or CONTEXT_HEADER not in text:
            if failure(result.returncode, result.stderr, events, []) == AUTH_FAILED:
                raise login_failed(LOGIN_HINT)
            raise preflight_leak(f"the free `/context` memory listing did not run (exit {result.returncode}), "
                                 + "so the runner cannot show that user memory stays out")
        return text

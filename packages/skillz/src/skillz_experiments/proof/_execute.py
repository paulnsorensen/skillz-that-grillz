"""Bounded local execution for trusted proof graders."""
from __future__ import annotations

import math
import os
import resource
import selectors
import signal
import subprocess
import time
from functools import partial
from pathlib import Path
from typing import cast

from skillz_experiments._cases import loads_untrusted

STDOUT_LIMIT = 64 * 1024
FILE_LIMIT = 4 * 1024 * 1024
TIME_LIMIT = 20


class ProofExecutionError(ValueError):
    """A grader invocation did not produce a valid score."""


def _executable(word: str, workspace: Path, path: str) -> str:
    if "/" in word:
        candidate = Path(word)
        return str(candidate if candidate.is_absolute() else workspace / candidate)
    for part in path.split(os.pathsep):
        base = Path(part) if part else workspace
        candidate = base / word if base.is_absolute() else workspace / base / word
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise ProofExecutionError(f"grader executable {word!r} was not found on PATH")

def score(workspace: Path, argv: tuple[str, ...], path: str) -> float:
    """Run resolved argv, then require a finite numeric score from valid JSON."""
    if not argv:
        raise ProofExecutionError("grader has no executable argv")
    executable = _executable(argv[0], workspace, path)
    environment = {"PATH": path, "HOME": str(workspace / "home"), "TMPDIR": str(workspace / "tmp"),
                   "LC_ALL": "C.UTF-8"}
    try:
        process = subprocess.Popen((executable, *argv[1:]), cwd=workspace, env=environment,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   start_new_session=True,
                                   preexec_fn=partial(resource.setrlimit, resource.RLIMIT_FSIZE,
                                                      (FILE_LIMIT, FILE_LIMIT)))
    except (OSError, subprocess.SubprocessError):
        raise ProofExecutionError("grader executable cannot start") from None
    output = bytearray()
    deadline = time.monotonic() + TIME_LIMIT
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            _ = selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProofExecutionError("grader timed out")
                if not selector.select(remaining):
                    raise ProofExecutionError("grader timed out")
                chunk = os.read(process.stdout.fileno(), STDOUT_LIMIT + 1 - len(output))
                if not chunk:
                    _ = selector.unregister(process.stdout)
                output.extend(chunk)
                if len(output) > STDOUT_LIMIT:
                    raise ProofExecutionError("grader stdout exceeds 64 KiB")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProofExecutionError("grader timed out")
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise ProofExecutionError("grader timed out") from None
        if code != 0:
            raise ProofExecutionError(f"grader exits with code {code}")
        try:
            result = loads_untrusted(output.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ProofExecutionError("grader stdout is not UTF-8 JSON") from None
        if not isinstance(result, dict):
            raise ProofExecutionError("grader stdout must be a JSON object")
        value = cast(dict[str, object], result).get("score")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1 or (isinstance(value, float) and not math.isfinite(value)):
            raise ProofExecutionError("grader score must be a finite number from 0 to 1")
        return float(value)
    except OSError:
        raise ProofExecutionError("grader process IO failed") from None
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _ = process.wait()
        if process.stdout is not None:
            process.stdout.close()

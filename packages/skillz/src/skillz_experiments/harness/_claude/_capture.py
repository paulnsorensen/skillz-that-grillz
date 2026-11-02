"""Read the standard error file that a sandboxed command leaves in its capture directory."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from skillz_experiments.harness._claude._constants import SANDBOX_STDERR

SANDBOX_STDERR_LIMIT = 100_000


def take_stderr(capture: Path) -> str:
    """Read the command's standard error file. A link, a special file, or a missing file reads as empty. Never raises."""
    try:
        descriptor = os.open(capture / SANDBOX_STDERR, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            return ""
    except OSError:
        os.close(descriptor)
        return ""
    try:
        handle = os.fdopen(descriptor, "rb")
    except OSError:
        os.close(descriptor)
        return ""
    try:
        with handle:
            data = handle.read(SANDBOX_STDERR_LIMIT)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")

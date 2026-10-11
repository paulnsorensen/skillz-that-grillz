"""Resume checks: frozen statistics, the run lock, the private directory, and config pins.
"""
from __future__ import annotations

import errno
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from skillz_experiments._cases import CodedError, mapping
from skillz_experiments._contract import Contract
from skillz_experiments.gate import DEFAULT_STATISTICS, Statistics
from skillz_experiments.harness import NONO_REMOVED, ClaudeOptions, stop_kind
from skillz_experiments.run._session import HarnessName, text_field
from skillz_experiments.run._stop import Stop
from skillz_experiments.search import Edit

_CONFIGURED_ADAPTERS = frozenset({"command", "config"})
_OPTION_FLAGS = (("--effort", "effort"), ("--sandbox-read", "sandbox_read"), ("--sandbox-seconds", "sandbox_seconds"))


def resolve_statistics(contract: Contract, given: Mapping[str, float]) -> Statistics:
    """Return each threshold from the CLI flag, else the contract, else the built-in default."""
    return Statistics.parse({**DEFAULT_STATISTICS.data(), **(contract.statistics or {}), **given})


def statistics_flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def frozen_statistics(record: dict[str, object]) -> Statistics:
    """Return the frozen thresholds. A map of the wrong shape is tampering.

    This checks shape only: the record does not bind the values to the contract or to the CLI flags.
    """
    try:
        return Statistics.parse(record.get("statistics"))
    except ValueError:
        raise Stop("run-record-tampered", "the recorded statistics are malformed; create a new run", {}) from None


def _config_differs(field: str) -> CodedError:
    return CodedError("run-config-differs", f"{field} differs from the frozen record; resume with the first-run value or create a new run")


def require_private(out: Path) -> None:
    """Stop unless `out` is a real directory, owned by this user, that no other user can enter."""
    status = out.lstat()
    if not stat.S_ISDIR(status.st_mode):
        raise CodedError("out-unsafe", f"{out} is not a plain directory; use a private directory from `mktemp -d`")
    if status.st_uid != os.getuid() or status.st_mode & 0o077:
        raise CodedError("out-unsafe", f"{out} must be owned by you and closed to other users (mode 0700); "
                         + "use a private directory from `mktemp -d`")


def refuse_terminated(record: dict[str, object]) -> None:
    """Stop when the run record has a terminal code."""
    stopped = record.get("failure_code")
    if stop_kind(stopped) == "terminal":
        raise Stop("run-terminated", f"the run stopped with {stopped}; start a new run directory",
                   {"next": "start a new run in a new directory", "failure_code": stopped})


def open_lock(out: Path) -> TextIO:
    """Open `run.lock` without following a symlink."""
    try:
        descriptor = os.open(out / "run.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.EMLINK):
            raise CodedError("out-unsafe", f"{out / 'run.lock'} is a symlink; remove it or use a new directory") from None
        raise
    return os.fdopen(descriptor, "w")


def refuse_nono(record: dict[str, object]) -> None:
    """Stop a resume of a run that recorded the removed nono backend. Older records of the default backend stay readable."""
    if record.get("isolation") == "nono":
        raise CodedError("nono-removed", NONO_REMOVED)


def check_resume(record: dict[str, object], target: Path, edit: Edit | None, repeats: int | None,
                  seed: int | None, model: str, adapter: HarnessName, given: Mapping[str, object],
                  configured: bool) -> None:
    """Stop when a resume names another model, adapter, target, edit, repeats, seed, or Claude option
    than the first run. A run that froze a harness configuration resumes only with a configuration.
    """
    if text_field(record, "model") != model:
        raise _config_differs("--model")
    recorded_adapter = text_field(record, "adapter")
    if recorded_adapter in _CONFIGURED_ADAPTERS and "harness" not in record:
        raise CodedError("run-record-tampered", "the run record lacks its frozen harness identity; create a new run")
    lost_configuration = recorded_adapter in _CONFIGURED_ADAPTERS and not configured
    crossed_configuration = recorded_adapter != adapter and bool({recorded_adapter, adapter} & _CONFIGURED_ADAPTERS)
    if lost_configuration or crossed_configuration:
        raise CodedError("harness-changed", "the harness selection differs from the frozen record; "
                         + "resume with the same --harness-config or --harness command --command, or create a new run")
    if recorded_adapter != adapter:
        raise _config_differs("--harness")
    for flag, value, field in ("--edit", edit, "edit"), ("--repeats", repeats, "repeats"), ("--seed", seed, "split_seed"):
        if value is not None and value != record.get(field):
            raise _config_differs(flag)
    recorded = ClaudeOptions.parse(mapping(record.get("claude_options", {})))
    for flag, field in _OPTION_FLAGS:
        if field in given and given[field] != getattr(recorded, field):
            raise _config_differs(flag)
    if str(target.resolve()) != record.get("target_root"):
        raise _config_differs("the target skill directory")


def check_statistics(statistics_given: Mapping[str, float], frozen: Statistics) -> None:
    """Stop when a resume, or a call on a completed run, names another statistics threshold than the first run."""
    for name, value in statistics_given.items():
        if value != getattr(frozen, name):
            raise _config_differs(statistics_flag(name))

"""Resume checks: frozen statistics, the run lock, the private directory, and config pins.
"""
from __future__ import annotations

import errno
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO, cast

from skillz_experiments._cases import CodedError, mapping
from skillz_experiments._contract import Contract
from skillz_experiments.gate import DEFAULT_STATISTICS, Statistics
from skillz_experiments.harness import CONFIGURED_SELECTIONS, NONO_REMOVED, ClaudeOptions, stop_kind
from skillz_experiments.run._session import HarnessName, judge_flag, text_field, too_easy_message
from skillz_experiments.run._stop import Stop
from skillz_experiments.search import Edit

_OPTION_FLAGS = (("--effort", "effort"), ("--sandbox-read", "sandbox_read"), ("--sandbox-seconds", "sandbox_seconds"))


def resolve_statistics(contract: Contract, given: Mapping[str, float]) -> Statistics:
    """Return each threshold from the CLI flag, else the contract, else the built-in default.

    `min_token_saving` without `max_quality_loss` raises `ValueError`: `promote-cheaper` needs a quality-loss bound.
    """
    resolved = Statistics.parse({**DEFAULT_STATISTICS.data(), **(contract.statistics or {}), **given})
    if resolved.min_token_saving is not None and resolved.max_quality_loss is None:
        raise ValueError("--min-token-saving needs --max-quality-loss (or the contract max_quality_loss): "
                         + "promote-cheaper needs a quality-loss bound")
    return resolved


def statistics_flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def frozen_statistics(record: dict[str, object]) -> Statistics:
    """Return the frozen thresholds.

    A well-formed map that lacks fields of a newer runner stops as `run-schema-old`. A malformed map is tampering.
    This checks shape only: the record does not bind the values to the contract or to the CLI flags.
    """
    recorded = record.get("statistics")
    try:
        declared = Statistics.declared(recorded)
    except ValueError:
        raise Stop("run-record-tampered", "the recorded statistics are malformed; create a new run", {}) from None
    if set(Statistics.names()) - set(declared):
        raise CodedError("run-schema-old", "the run record lacks statistics fields of a newer runner; "
                         + "start a new run with `skillz-experiment run`")
    return Statistics.parse(recorded)


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


def refuse_too_easy(record: dict[str, object]) -> None:
    """Raise the `cases-too-easy` stop of the headroom pass again from the record. The stop makes no model call.

    The text comes from the headroom block, not from `failure`, so a run that died before it recorded `failure` still resumes.
    """
    headroom = record.get("headroom")
    if isinstance(headroom, dict) and cast(dict[str, object], headroom).get("verdict") == "too-easy":
        raise CodedError("cases-too-easy", too_easy_message(cast(dict[str, object], headroom)))


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
                  configured: bool, judge_model: str | None, history: bool | None = None) -> None:
    """Stop when a resume names another model, judge model, adapter, target, edit, repeats, seed, Claude option, or history
    setting than the first run. A resume that omits `judge_model` or `history` keeps the recorded value.
    A run that froze a harness configuration resumes only with a configuration.
    """
    if text_field(record, "model") != model:
        raise _config_differs("--model")
    if judge_model is not None and judge_model != (judge_flag(record) or text_field(record, "model")):
        raise _config_differs("--judge-model")
    recorded_adapter = text_field(record, "adapter")
    if recorded_adapter in CONFIGURED_SELECTIONS and "harness" not in record:
        raise CodedError("run-record-tampered", "the run record lacks its frozen harness identity; create a new run")
    lost_configuration = recorded_adapter in CONFIGURED_SELECTIONS and not configured
    crossed_configuration = recorded_adapter != adapter and bool({recorded_adapter, adapter} & CONFIGURED_SELECTIONS)
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
    if history is not None and history != record.get("history"):
        raise _config_differs("--no-history")


_ABSENT = object()


def _frozen_fields(record: Mapping[str, object]) -> Mapping[str, object] | None:
    """Return the compatibility fields of `record`, or None when it has no well-formed, nonempty fingerprint."""
    found = record.get("compatibility")
    if not isinstance(found, Mapping):
        return None
    fields = cast(Mapping[str, object], found).get("fields")
    return cast(Mapping[str, object], fields) if isinstance(fields, Mapping) and fields else None


def compare(a: Mapping[str, object], b: Mapping[str, object]) -> tuple[str, ...]:
    """Name each compatibility field that differs between two run records, sorted.

    Return `("missing-fingerprint",)` when either record has none. An empty tuple means the runs are comparable.
    """
    left, right = _frozen_fields(a), _frozen_fields(b)
    if left is None or right is None:
        return ("missing-fingerprint",)
    return tuple(sorted(name for name in left.keys() | right.keys()
                        if left.get(name, _ABSENT) != right.get(name, _ABSENT)))


def check_statistics(statistics_given: Mapping[str, float], frozen: Statistics) -> None:
    """Stop when a resume, or a call on a completed run, names another statistics threshold than the first run."""
    for name, value in statistics_given.items():
        if value != getattr(frozen, name):
            raise _config_differs(statistics_flag(name))

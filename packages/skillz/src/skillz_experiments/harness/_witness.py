"""A runner-side witness for the helper of a command harness.

A command harness reports its own trace, so the trace alone does not prove that the helper ran. The runner
replaces the helper with a stub. The stub runs the real helper, then appends one line to a ledger in the workspace.
Helper credit needs a ledger line that matches the trace entry.

The witness proves that the helper ran. It does not stop a model that writes ledger lines on purpose.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import cast, final

from skillz_experiments._protocol import argv_strings

LEDGER = ".skillz-helper-ledger.jsonl"
LEDGER_BYTES_LIMIT = 1_000_000

_STUB = '''\
def _witness(space):
    import json, os, runpy, sys, zipfile
    from pathlib import Path

    nonce = {nonce}
    levels = {levels}
    ledger_name = {ledger}
    stub = Path(space["__file__"])
    real = stub.with_name({real})
    name = space["__name__"]
    for key in [n for n in space if not (n.startswith("__") and n.endswith("__"))]:
        del space[key]
    if zipfile.is_zipfile(real):
        def run():
            runpy.run_path(str(real), run_name=name)
    else:
        program = compile(real.read_bytes(), space["__file__"], "exec")
        def run():
            exec(program, space)
    if name != "__main__":
        run()
        return
    code = 0
    failure = None
    try:
        run()
    except SystemExit as stop:
        failure = stop
        if stop.code is None:
            code = 0
        elif isinstance(stop.code, int):
            code = stop.code & 255
        else:
            code = 1
    except KeyboardInterrupt as stop:
        failure = stop
        code = 130
    except BaseException as stop:
        failure = stop
        code = 1
    line = {{"nonce": nonce, "argv": list(sys.argv), "exit_code": code}}
    try:
        root = stub.resolve().parents[levels - 1]
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
        descriptor = os.open(root / ledger_name, flags, 0o600)
        with os.fdopen(descriptor, "a") as ledger:
            ledger.write(json.dumps(line) + "\\n")
    except OSError:
        pass
    if failure is not None:
        raise failure


_witness(globals())
'''


@dataclass(frozen=True)
class Witness:
    """The nonce of one call and the ledger that the stubs of that call write."""

    nonce: str
    ledger: Path


def stage_witness(workspace: Path, skill: str, helper: str) -> Witness | None:
    """Replace the staged `helper` of `skill` with a witness stub. Return None when the helper is not a plain file.

    The real helper stays in the same directory under a dot name, so `__file__`-relative reads still work.
    A file error while staging restores the helper and returns None. An error in the restore itself propagates.
    """
    path = workspace / ".agents/skills" / skill / helper
    if path.is_symlink() or not path.is_file():
        return None
    nonce = secrets.token_hex(16)
    real = path.with_name("." + path.name)
    try:
        mode = path.stat().st_mode
        _ = path.replace(real)
    except OSError:
        return None
    try:
        levels = len(path.relative_to(workspace).parts)
        _ = path.write_text(_STUB.format(nonce=json.dumps(nonce), levels=levels, real=json.dumps(real.name),
                                         ledger=json.dumps(LEDGER)), encoding="utf-8")
        path.chmod(mode)
    except OSError:
        _ = real.replace(path)
        return None
    return Witness(nonce, workspace / LEDGER)


@final
class LedgerError(Exception):
    """The ledger cannot be trusted as a full record. `cause` names why; `entries` holds the lines that still parse."""

    def __init__(self, cause: str, entries: list[dict[str, object]]) -> None:
        super().__init__(cause)
        self.cause = cause
        self.entries = entries


def ledger_entries(witness: Witness) -> list[dict[str, object]]:
    """Read the ledger lines of this call. A malformed line or a line with another nonce does not count.

    The model can write the ledger, so the open does not follow a symlink or block on a FIFO. Only a regular file counts.
    An absent ledger returns []: the helper did not run. Any other fault raises `LedgerError` with cause
    `ledger-unreadable`, or `ledger-truncated` when the ledger exceeds `LEDGER_BYTES_LIMIT`.
    """
    try:
        descriptor = os.open(witness.ledger, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    except OSError:
        raise LedgerError("ledger-unreadable", []) from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise LedgerError("ledger-unreadable", [])
        raw = os.read(descriptor, LEDGER_BYTES_LIMIT + 1)
    except OSError:
        raise LedgerError("ledger-unreadable", []) from None
    finally:
        os.close(descriptor)
    truncated = len(raw) > LEDGER_BYTES_LIMIT
    text = raw[:LEDGER_BYTES_LIMIT].decode("utf-8", errors="replace")
    found: list[dict[str, object]] = []
    for line in text.splitlines():
        try:
            value = cast(object, json.loads(line))
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        item = cast(dict[str, object], value)
        argv = argv_strings(item.get("argv"))
        code = item.get("exit_code")
        if item.get("nonce") == witness.nonce and argv is not None and type(code) is int:
            found.append({"argv": argv, "exit_code": code})
    if truncated:
        raise LedgerError("ledger-truncated", found)
    return found

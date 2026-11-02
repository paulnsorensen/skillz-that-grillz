"""Run the `fake_claude.py` table in the test process, for a test that checks only parse or verdict logic.

`in_process_claude(tmp_path, mode)` lays out `bin/claude` and its `claude.mode` sidecar as `fake_claude` in
`test_claude.py` does. Here `bin/claude` is a stub that fails when a process starts it. Its `claude.in-process`
marker tells `route` to answer the call with `fake_claude.run_captured` in this process. The events, the exit codes,
and the per-call logs come from the one table in `fake_claude.py`, so the two paths cannot drift.

A test whose contract is the argv, environment, stdin, sandbox, or process group of the child keeps `fake_claude`.
"""
from __future__ import annotations

import importlib.util
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

FAKE = Path(__file__).parent / "fixtures/fake_claude.py"
MARKER = ".in-process"
STUB = ("#!/bin/sh\necho 'fake claude: a test started the in-process stub as a process; "
        + "use fake_claude for a process contract' >&2\nexit 97\n")
PROCESS_ONLY = frozenset({"inventory-hang"})
"""Modes that need a real child: the runner kills a hung child at its deadline, and an in-process call cannot end."""

Spawn = Callable[[list[str], Path, float, dict[str, str], str | None], subprocess.CompletedProcess[str]]
Captured = Callable[[Path, list[str], Path, Mapping[str, str], str], tuple[int, str, str]]


def _captured() -> Captured:
    spec = importlib.util.spec_from_file_location("fake_claude_in_process", FAKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Captured, module.run_captured)


run_captured = _captured()


def in_process_claude(tmp_path: Path, mode: str = "ok") -> Path:
    """Write the stub `bin/claude`, its mode, and its in-process marker. Return the stub path."""
    if mode in PROCESS_ONLY:
        raise ValueError(f"mode {mode} needs a real child; use fake_claude")
    executable = tmp_path / "bin" / "claude"
    executable.parent.mkdir()
    _ = executable.write_text(STUB)
    executable.chmod(0o755)
    _ = executable.with_name("claude.mode").write_text(mode)
    executable.with_name(executable.name + MARKER).touch()
    return executable


def route(spawn: Spawn) -> Spawn:
    """Wrap `_runtime._spawn`. A child whose argv[0] is an in-process stub runs in this process; any other child starts."""
    def run(command: list[str], cwd: Path, timeout: float, environment: dict[str, str],
            input_text: str | None) -> subprocess.CompletedProcess[str]:
        script = Path(command[0])
        if not script.with_name(script.name + MARKER).is_file():
            return spawn(command, cwd, timeout, environment, input_text)
        code, stdout, stderr = run_captured(script, command[1:], cwd, environment, input_text or "")
        return subprocess.CompletedProcess(command, code, stdout, stderr)
    return run

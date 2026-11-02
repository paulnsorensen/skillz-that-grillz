"""The capture reader turns an OSError after the open into an empty result, and it closes the descriptor that it opened."""
from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn, final

import pytest

from skillz_experiments.harness._claude import _capture
from skillz_experiments.harness._claude._constants import SANDBOX_STDERR


@final
class _Os:
    """Forward each name to `os`, except the replaced functions."""

    def __init__(self, overrides: dict[str, object]) -> None:
        self.overrides = overrides

    def __getattr__(self, name: str) -> object:
        return self.overrides[name] if name in self.overrides else getattr(os, name)


def _fail(*_: object) -> NoReturn:
    raise OSError("injected")


@final
class _UnreadableHandle:
    """A file handle whose read fails. Leaving the `with` block closes the descriptor, as a real handle does."""

    def __init__(self, descriptor: int, _mode: str) -> None:
        self.descriptor = descriptor

    def __enter__(self) -> _UnreadableHandle:
        return self

    def __exit__(self, *_: object) -> None:
        os.close(self.descriptor)

    def read(self, _size: int) -> bytes:
        raise OSError("injected")


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc/self/fd")
@pytest.mark.parametrize("overrides", [{"fstat": _fail}, {"fdopen": _fail}, {"fdopen": _UnreadableHandle}],
                         ids=["fstat", "fdopen", "read"])
def test_an_os_error_after_the_open_reads_as_empty_and_closes_the_descriptor(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]) -> None:
    _ = (tmp_path / SANDBOX_STDERR).write_text("command stderr")
    assert _capture.take_stderr(tmp_path) == "command stderr"
    monkeypatch.setattr(_capture, "os", _Os(overrides))
    before = len(os.listdir("/proc/self/fd"))
    assert _capture.take_stderr(tmp_path) == ""
    assert len(os.listdir("/proc/self/fd")) == before

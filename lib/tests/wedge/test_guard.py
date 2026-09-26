"""AC-W7: the closure guard rejects a platform marker or a non-pure wheel."""

from __future__ import annotations

import pytest

from wedge._guard import ClosureEntry, GuardError, guard_closure


@pytest.mark.ac("AC-W7")
def test_platform_marker_is_rejected() -> None:
    entries = [
        ClosureEntry(
            name="colorama",
            wheel="colorama-0.4.6-py3-none-any.whl",
            marker="sys_platform == 'win32'",
        )
    ]
    with pytest.raises(GuardError, match="platform marker"):
        guard_closure(entries)


@pytest.mark.ac("AC-W7")
def test_non_pure_wheel_is_rejected() -> None:
    entries = [
        ClosureEntry(
            name="numpy",
            wheel="numpy-2.0.0-cp311-cp311-manylinux_2_28_x86_64.whl",
        )
    ]
    with pytest.raises(GuardError, match="py3-none-any"):
        guard_closure(entries)


@pytest.mark.ac("AC-W7")
def test_pure_marker_free_closure_passes() -> None:
    entries = [ClosureEntry(name="rich", wheel="rich-14.2.0-py3-none-any.whl")]
    guard_closure(entries)

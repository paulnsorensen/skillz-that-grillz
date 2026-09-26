"""AC-W7: the closure guard rejects a platform marker or a non-pure wheel."""

from __future__ import annotations

import pytest

from wedge._guard import ClosureEntry, GuardError, guard_closure
from wedge._resolve import parse_requirements


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


@pytest.mark.ac("AC-W7")
@pytest.mark.parametrize(
    "line",
    ["-e ./packages/shared", "shared @ git+https://example.com/shared@abc123", "./vendor/pkg"],
)
def test_unpinned_requirement_is_rejected(line: str) -> None:
    text = f"rich==14.2.0 \\\n    --hash=sha256:{'0' * 64}\n{line}\n"
    with pytest.raises(GuardError, match="include"):
        parse_requirements(text)


@pytest.mark.ac("AC-W7")
def test_pinned_requirements_parse_with_markers() -> None:
    text = (
        "# header comment\n"
        "rich==14.2.0 \\\n"
        f"    --hash=sha256:{'0' * 64}\n"
        "    # via cyclopts\n"
        "tomli==2.2.1 ; python_full_version < '3.11' \\\n"
        f"    --hash=sha256:{'1' * 64}\n"
    )
    assert parse_requirements(text) == [
        ("rich", "14.2.0", None),
        ("tomli", "2.2.1", "python_full_version < '3.11'"),
    ]

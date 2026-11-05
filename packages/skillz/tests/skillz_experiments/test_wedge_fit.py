"""The fit of several wedge targets: a source that one target holds fixed stays fixed for every later target."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from wedge import BuildPaths

from skillz_experiments._candidate import PACKAGE_LIMIT
from skillz_experiments.run._wedge_fit import WEDGE_EDIT_LIMIT, choose

REPO = Path("/repo")
SHARED = "@wedge/src/pkg/shared/big.py"


@dataclass(frozen=True)
class Target:
    """A staged target with the fields that the fit reads."""

    name: str
    sources: dict[str, str] | None
    fixed: dict[str, bytes] = field(default_factory=dict[str, bytes])
    repo: Path | None = REPO

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self.sources or {})

    @property
    def paths(self) -> BuildPaths:
        return cast(BuildPaths, cast(object, SimpleNamespace(source=REPO / "src/pkg")))

    @property
    def skill_root(self) -> Path:
        return REPO / "skills" / self.name


def _own(skill: str, size: int = 10) -> dict[str, str]:
    return {f"@wedge/src/pkg/{skill}/main.py": "x" * size}


def test_a_source_that_an_earlier_target_holds_fixed_is_fixed_for_a_later_target_too() -> None:
    first = Target("alpha", _own("alpha") | {SHARED: "s" * WEDGE_EDIT_LIMIT})
    second = Target("beta", _own("beta") | {SHARED: "s" * WEDGE_EDIT_LIMIT})
    fits = choose([first, second], [first, second], frozenset(), 0)
    assert [fit.target.name for fit in fits] == ["alpha", "beta"]
    for fit in fits:
        assert SHARED in fit.fixed and SHARED not in fit.sources
        assert not set(fit.sources) & set(fit.fixed)
    assert "stay fixed" in fits[0].partial and fits[1].partial == ""
    assert set(fits[1].sources) == set(_own("beta"))


def test_a_source_that_an_earlier_target_edits_costs_a_later_target_nothing() -> None:
    first = Target("alpha", _own("alpha", WEDGE_EDIT_LIMIT // 2 + 1))
    second = Target("beta", _own("alpha", WEDGE_EDIT_LIMIT // 2 + 1) | _own("beta"))
    fits = choose([first, second], [first, second], frozenset(), 0)
    assert [fit.target.name for fit in fits] == ["alpha", "beta"]
    assert set(fits[1].sources) == set(_own("alpha")) | set(_own("beta")) and fits[1].partial == ""


def test_a_partial_target_names_the_package_limit_when_the_package_text_leaves_less_room() -> None:
    target = Target("alpha", _own("alpha", 500) | {SHARED: "s" * 2000})
    fits = choose([target], [target], frozenset(), PACKAGE_LIMIT - 1000)
    assert set(fits[0].sources) == set(_own("alpha"))
    assert f"package limit of {PACKAGE_LIMIT}" in fits[0].partial


def test_a_target_with_no_part_that_fits_freezes_it_and_every_later_target() -> None:
    frozen = Target("alpha", _own("alpha", WEDGE_EDIT_LIMIT + 1))
    later = Target("beta", _own("beta"))
    assert choose([frozen, later], [frozen, later], frozenset(), 0) == []
    assert [fit.target.name for fit in choose([later, frozen], [later, frozen], frozenset(), 0)] == ["beta"]

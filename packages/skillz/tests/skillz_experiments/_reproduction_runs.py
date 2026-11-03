"""Shared helpers of the regression reproduction tests that drive a full run (gh-651, gh-663)."""
# pyright: reportAny=false
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from skillz_experiments._intake import DRAFT_NAME
from skillz_experiments._records import read
from skillz_experiments.run import run

from test_run_search import MODEL, Search

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]
Started = tuple[Path, Path, Search, str, int]


def start(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
          regression: Callable[[dict[str, object]], bool] = lambda _case: True) -> Started:
    """Draft the default 6 families of 2 cases, mark the chosen cases as session regression cases, and approve."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=6)
    path = out / DRAFT_NAME
    cases = cast(list[dict[str, object]], json.loads(path.read_text(encoding="utf-8")))
    for case in cases:
        if regression(case):
            case |= {"regression": True, "source": "session"}
    _ = path.write_text(json.dumps(cases), encoding="utf-8")
    approved, calls = approvals(target, out)
    return target, out, Search(target), approved, calls


def go(state: Search, target: Path, out: Path, approved: str, calls: int) -> dict[str, object]:
    return run(target, out, MODEL, live=True, approve_cases=approved, approve_budget=calls, factory=state.factory)


def record(out: Path) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny]
    return cast(dict[str, Any], read(out / "run.json"))  # pyright: ignore[reportExplicitAny]


def reproduction(out: Path) -> dict[str, dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
    return cast(dict[str, dict[str, Any]], record(out)["headroom"]["reproduction"])  # pyright: ignore[reportExplicitAny]


def splits(out: Path, *names: str) -> dict[str, str]:
    cases = cast(list[dict[str, str]], read(out / "cases.json")["cases"])
    return {case["id"]: case["split"] for case in cases if case["split"] in names}


def seed_zero(state: Search) -> None:
    """Score the seed 0 on every split. Improved texts keep the default score."""
    state.score_of = lambda text, _split: 0.0 if "improved" not in text else None


def seed_one_on_train(state: Search) -> None:
    state.score_of = lambda text, split: 1.0 if split == "train" and "improved" not in text else None

"""Press attacks on the autoimprove run: approvals, budget, holdout, gate, splits, credentials, CLI (AC-1 to AC-19).

Each test names its attack id (PA-n) in its docstring. Spies and tampered records stay in this file.
"""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import math
import os
import random
import stat
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple, cast, final

import pytest

from skillz_experiments import _workflow
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._claude import CONFIG_PREFIX, CREDENTIALS, ClaudeCode, Inventory, settings
from skillz_experiments._cli import main
from skillz_experiments._gate import DEFAULT_STATISTICS, case_deltas, verdict
from skillz_experiments._harness import Configuration
from skillz_experiments._intake import (DRAFT_NAME, HOLDOUT_MINIMUM, PENDING_KINDS, DraftCase, case_hash, split_cases)
from skillz_experiments._records import read, write
from skillz_experiments._runtime import MAX_CONCURRENT_CALLS, Budget
from skillz_experiments._search import Edit, Evaluate, Propose, search
from skillz_experiments._workflow import Stop, export, run

pytestmark = pytest.mark.usefixtures("host_login")

MODEL = "local-test"


@final
class Spy:
    """A fake provider factory. The seed scores 0; a proposal that says `improved` scores `improved_score`."""

    def __init__(self, *, improved_score: float = 1.0, delay: float = 0.0, refuse: bool = False) -> None:
        self.lock = threading.Lock()
        self.improved_score, self.delay, self.refuse = improved_score, delay, refuse
        self.created = 0
        self.budgets: list[Budget] = []
        self.evaluated: list[tuple[str, bool, dict[str, str]]] = []
        self.prompts: list[str] = []
        self.inflight = 0
        self.peak = 0

    def factory(self, _model: str, budget: Budget, checkpoint: Callable[[], None]) -> _Fake:
        if self.refuse:
            raise AssertionError("a provider was created before every approval")
        with self.lock:
            self.created += 1
            self.budgets.append(budget)
        return _Fake(self, budget, checkpoint)

    def enter(self) -> None:
        with self.lock:
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
        if self.delay:
            time.sleep(self.delay)

    def leave(self) -> None:
        with self.lock:
            self.inflight -= 1


@final
class _Fake:
    def __init__(self, spy: Spy, budget: Budget, checkpoint: Callable[[], None]) -> None:
        self.spy, self.budget, self.checkpoint = spy, budget, checkpoint

    def preflight(self) -> dict[str, object]:
        return {"isolation": "test-provider"}

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        self.budget.claim(holdout=holdout)
        self.checkpoint()
        self.spy.enter()
        try:
            with self.spy.lock:
                self.spy.evaluated.append((case.identifier, holdout, dict(candidate.files)))
            improved = any("improved" in text for text in candidate.files.values())
            return {"score": self.spy.improved_score if improved else 0.0, "candidate_hash": candidate.identity,
                    "case_hash": case.identifier, "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}}
        finally:
            self.spy.leave()

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del candidate, case
        assert schema is not None
        self.budget.claim(holdout=holdout)
        self.checkpoint()
        self.spy.enter()
        try:
            with self.spy.lock:
                self.spy.prompts.append(prompt)
            return {"answer": {name: "improved" for name in cast(list[str], schema["required"])}}
        finally:
            self.spy.leave()


class Prepared(NamedTuple):
    target: Path
    out: Path
    case_hash: str
    calls: int


def prepare(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
            approvals: Callable[..., tuple[str, int]], *, helper: bool = False, edit: str = "prose",
            draft: bool = True) -> Prepared:
    """Give both approvals and stop at `live-required`, so `run.json` holds the prepared record."""
    target, out = make_target(tmp_path, helper=helper), tmp_path / "run"
    if draft:
        _ = write_draft(out)
    approved_hash, calls = approvals(target, out, edit=edit)
    with pytest.raises(Stop) as stopped:
        _ = run(target, out, MODEL, edit=cast(Edit, edit), approve_cases=approved_hash, approve_budget=calls)
    assert stopped.value.code == "live-required"
    return Prepared(target, out, approved_hash, calls)


def resume(prep: Prepared, spy: Spy) -> dict[str, object] | None:
    """Resume live. Swallow every failure: the tests judge what the provider saw, not the error type."""
    try:
        return run(prep.target, prep.out, MODEL, live=True, factory=spy.factory)
    except Exception:
        return None


def tamper(out: Path, change: Callable[[dict[str, object]], None]) -> None:
    record = read(out / "run.json")
    change(record)
    write(out / "run.json", record)


def frozen(out: Path) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], json.loads((out / "cases.json").read_text())["cases"])


def snapshot(root: Path) -> dict[str, bytes | None]:
    return {path.relative_to(root).as_posix(): (path.read_bytes() if path.is_file() else None)
            for path in sorted(root.rglob("*"))}


# --- PA-1: approval bypass -------------------------------------------------------------------------------

@pytest.mark.parametrize("mutation", ["add-case", "edit-request", "move-to-train", "drop-holdout"])
def test_cases_json_edited_after_approval_stops_resume_before_any_provider(
        mutation: str, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """PA-1: an edited `cases.json` never reaches a provider."""
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    document = cast(dict[str, object], json.loads((prep.out / "cases.json").read_text()))
    cases = cast(list[dict[str, object]], document["cases"])
    if mutation == "add-case":
        cases.append(cases[0] | {"id": "smuggled", "family": "smuggled", "split": "train"})
    elif mutation == "edit-request":
        cases[0] = cases[0] | {"request": "changed after approval"}
    elif mutation == "move-to-train":
        cases[:] = [case | ({"split": "train"} if case["split"] == "holdout" else {}) for case in cases]
    else:
        cases[:] = [case for case in cases if case["split"] != "holdout"]
    _ = (prep.out / "cases.json").write_text(json.dumps(document))
    spy = Spy()
    with pytest.raises(ValueError, match="dataset differs"):
        _ = run(prep.target, prep.out, MODEL, live=True, factory=spy.factory)
    assert spy.created == 0 and spy.evaluated == []


def test_draft_edited_after_prepare_never_adds_a_case_to_the_run(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """PA-1: a draft edit after the record exists changes nothing that runs."""
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    frozen_ids = {str(case["id"]) for case in frozen(prep.out)}
    draft = cast(list[dict[str, object]], json.loads((prep.out / DRAFT_NAME).read_text()))
    draft.append(draft[0] | {"id": "late-case", "family": "late-family"})
    _ = (prep.out / DRAFT_NAME).write_text(json.dumps(draft))
    spy = Spy()
    assert run(prep.target, prep.out, MODEL, live=True, factory=spy.factory)["phase"] == "complete"
    assert {name for name, _held, _files in spy.evaluated} <= frozen_ids


def test_draft_edited_before_the_record_needs_a_fresh_hash(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """PA-1: the approved hash covers draft content, so an edit between approval and the record stops."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    approved_hash, calls = approvals(target, out)
    draft = cast(list[dict[str, object]], json.loads((out / DRAFT_NAME).read_text()))
    draft[3] = draft[3] | {"expected": {"edited": True}}
    _ = (out / DRAFT_NAME).write_text(json.dumps(draft))
    spy = Spy(refuse=True)
    with pytest.raises(Stop) as stopped:
        _ = run(target, out, MODEL, live=True, approve_cases=approved_hash, approve_budget=calls, factory=spy.factory)
    assert stopped.value.code == "cases-unapproved"
    assert not (out / "run.json").exists()
    assert all("edited" not in cast(dict[str, object], case.get("expected", {})) for case in frozen(out))


def test_case_approval_is_bound_to_the_seed(tmp_path: Path, make_target: Callable[..., Path],
                                            write_draft: Callable[..., list[str]],
                                            approvals: Callable[..., tuple[str, int]]) -> None:
    """PA-1: a hash approved for one seed never approves another split."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    approved_hash, _calls = approvals(target, out)
    with pytest.raises(Stop) as stopped:
        _ = run(target, out, MODEL, live=True, seed=7, approve_cases=approved_hash, factory=Spy(refuse=True).factory)
    assert stopped.value.code == "cases-unapproved" and not (out / "run.json").exists()


@pytest.mark.parametrize("field", ["request", "expected", "files", "family", "source", "id"])
def test_case_hash_changes_with_every_content_field(field: str) -> None:
    """PA-1: no field of an approved case can change without changing the hash."""
    def make() -> list[DraftCase]:
        return [DraftCase(f"c{family}-{index}", f"f{family}", "task", f"r{family}{index}", "skill", {"a.md": "x"}, {"k": 1})
                for family in range(4) for index in range(2)]

    base = case_hash(split_cases(make(), 5), 5)
    cases = make()
    old = cases[0]
    changes: dict[str, object] = {"request": "other", "expected": {"k": 2}, "files": {"a.md": "y"}, "family": "f1",
                                  "source": "session", "id": "renamed"}
    values = {"identifier": old.identifier, "family": old.family, "kind": old.kind, "request": old.request,
              "source": old.source, "files": old.files, "expected": old.expected}
    key = "identifier" if field == "id" else field
    values[key] = changes[field]
    cases[0] = DraftCase(**cast(dict[str, str], values))  # pyright: ignore[reportArgumentType]
    assert case_hash(split_cases(cases, 5), 5) != base


# --- PA-2 / PA-3: budget bypass --------------------------------------------------------------------------

def _budget_mutations() -> dict[str, Callable[[dict[str, object]], None]]:
    def budget(record: dict[str, object]) -> dict[str, object]:
        return cast(dict[str, object], record["budget"])

    def set_budget(name: str, value: object) -> Callable[[dict[str, object]], None]:
        def apply(record: dict[str, object]) -> None:
            budget(record)[name] = value
        return apply

    def set_estimate(record: dict[str, object]) -> None:
        cast(dict[str, object], record["estimate"])["metric_calls"] = 10**6

    return {"maximum-1000": set_budget("maximum", 1000), "maximum-200": set_budget("maximum", 200),
            "maximum-zero": set_budget("maximum", 0), "maximum-text": set_budget("maximum", "200"),
            "maximum-bool": set_budget("maximum", True), "seconds-huge": set_budget("seconds", 10**9),
            "seconds-nan": set_budget("seconds", float("nan")), "reserve-zero": set_budget("reserve", 0),
            "approved-false": set_budget("approved", False), "approved-text": set_budget("approved", "yes"),
            "reserve-seconds-zero": set_budget("reserve_seconds", 0), "reserve-seconds-bool": set_budget("reserve_seconds", False),
            "metric-calls-huge": set_estimate}


@pytest.mark.parametrize("name", list(_budget_mutations()))
def test_tampered_budget_never_exceeds_the_approved_ceiling_on_resume(
        name: str, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """PA-2: after a `run.json` edit, the provider budget stays at or under the approved calls, with the holdout reserve."""
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    original = cast(dict[str, int], read(prep.out / "run.json")["budget"])
    tamper(prep.out, _budget_mutations()[name])
    spy = Spy()
    _ = resume(prep, spy)
    for budget in spy.budgets:
        assert budget.maximum <= prep.calls and budget.maximum <= original["maximum"]
        assert budget.reserve >= original["reserve"] and budget.seconds <= 7200
    spent = cast(int, read(prep.out / "run.json")["calls"])
    assert spent <= prep.calls
    assert sum(1 for _name, _held, _files in spy.evaluated) + len(spy.prompts) <= prep.calls


@pytest.mark.parametrize("started", ["expired", "expired-wall", "expired-sleep", "future-wall", "future-wall-6"])
def test_resume_past_the_deadline_or_clock_reset_creates_no_provider(
        started: str, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """PA-2: a record older than 7200 s by either clock, or from a later wall clock, stops before any provider.

    `expired-wall` models a reboot: the monotonic clock restarts below the recorded value, and the wall clock
    still shows the deadline as past.
    """
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    clocks: dict[str, dict[str, object]] = {
        "expired": {"started_monotonic": time.monotonic() - 7300.0},
        "expired-wall": {"started_monotonic": time.monotonic() + 10**7, "started": time.time() - 7300.0},
        "expired-sleep": {"started": time.time() - 7300.0},
        "future-wall": {"started": time.time() + 10**7},
        "future-wall-6": {"started": time.time() + 6.0},
    }
    tamper(prep.out, lambda record: record.update(clocks[started]))
    spy = Spy(refuse=True)
    with pytest.raises((RuntimeError, ValueError)) as stopped:
        _ = run(prep.target, prep.out, MODEL, live=True, factory=spy.factory)
    if started.startswith("expired"):
        assert cast(CodedError, stopped.value).code == "budget-exhausted"
    else:
        assert cast(CodedError, stopped.value).code == "clock-skew"
        assert "wall clock" in str(stopped.value)


@pytest.mark.parametrize("clock", ["within-tolerance", "reboot"])
def test_resume_inside_the_budget_completes_when_the_clocks_agree_or_the_monotonic_clock_reset(
        clock: str, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """A wall clock up to 5 s early resumes. After a reboot the negative monotonic span falls back to the wall clock."""
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    clocks: dict[str, dict[str, object]] = {
        "within-tolerance": {"started": time.time() + 4.0},
        "reboot": {"started_monotonic": time.monotonic() + 10**7, "started": time.time() - 60.0},
    }
    tamper(prep.out, lambda record: record.update(clocks[clock]))
    assert run(prep.target, prep.out, MODEL, live=True, factory=Spy().factory)["phase"] == "complete"


@pytest.mark.parametrize("repeats", [1, 2, 10, 13, 16, 17, 40, 10**6, 10**18])
def test_repeats_never_silently_exceed_the_cap(
        repeats: int, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """PA-3: any repeats value stops as budget-unapproved or runs inside 200 calls and 7200 s."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    approved_hash, _calls = approvals(target, out)
    spy = Spy()
    try:
        result = run(target, out, MODEL, live=True, repeats=repeats, approve_cases=approved_hash, approve_budget=200,
                     factory=spy.factory)
    except Stop as stopped:
        assert stopped.code == "budget-unapproved" and spy.created == 0
        return
    assert result["phase"] == "complete"
    record = read(out / "run.json")
    budget = cast(dict[str, int], record["budget"])
    assert budget["maximum"] <= 200 and budget["seconds"] <= 7200 and cast(int, record["calls"]) <= budget["maximum"]
    held = [item for item in spy.evaluated if item[1]]
    holdout = {str(case["id"]) for case in frozen(out) if case["split"] == "holdout"}
    assert len(held) in (0, 2 * len(holdout) * repeats)


@pytest.mark.parametrize("approved", [None, 0, -1, -(10**9)])
def test_nonpositive_budget_approval_is_never_accepted(
        approved: int | None, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """PA-2: an approval that is missing, zero, or negative stops as budget-unapproved."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    approved_hash, _calls = approvals(target, out)
    with pytest.raises(Stop) as stopped:
        _ = run(target, out, MODEL, live=True, approve_cases=approved_hash, approve_budget=approved,
                factory=Spy(refuse=True).factory)
    assert stopped.value.code == "budget-unapproved"
    assert not (out / "run.json").exists()


@pytest.mark.parametrize("repeats", [0, -1, -(10**9)])
def test_nonpositive_repeats_are_rejected(repeats: int, tmp_path: Path, make_target: Callable[..., Path]) -> None:
    """PA-3: repeats under 1 raise before any record exists."""
    target, out = make_target(tmp_path), tmp_path / "run"
    with pytest.raises(ValueError, match="repeats"):
        _ = run(target, out, MODEL, live=True, repeats=repeats, factory=Spy(refuse=True).factory)
    assert not (out / "run.json").exists()


def test_a_tight_budget_keeps_the_holdout_reserve_and_never_overspends(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """PA-2: with repeats 15 the search cannot take holdout calls and the gate still completes inside the estimate."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    approved_hash, _calls = approvals(target, out)
    spy = Spy()
    try:
        result = run(target, out, MODEL, live=True, repeats=15, approve_cases=approved_hash, approve_budget=200,
                     factory=spy.factory)
    except Stop as stopped:
        assert stopped.code == "budget-unapproved"
        return
    record = read(out / "run.json")
    assert result["phase"] == "complete"
    estimate = cast(dict[str, int], record["estimate"])
    assert cast(int, record["calls"]) <= estimate["calls"] <= 200
    assert len([item for item in spy.evaluated if item[1]]) in (0, 2 * 15 * len([c for c in frozen(out) if c["split"] == "holdout"]))


# --- PA-4: holdout leakage -------------------------------------------------------------------------------

@pytest.mark.parametrize("edit", ["prose", "prose+cli"])
def test_holdout_ids_requests_and_expected_values_never_reach_search_or_reflection(
        edit: str, tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    """PA-4: hostile ids and texts in the holdout stay out of GEPA datasets, prompts, candidates, and search outcomes."""
    target, out = make_target(tmp_path, helper=edit == "prose+cli"), tmp_path / "run"
    cases: list[dict[str, object]] = []
    for family in range(5):
        for index in range(3):
            n = family * 3 + index
            cases.append({"id": f"case·{n:02d}·ω.*", "family": f"famé{family}", "kind": "task",
                          "request": f"REQ[[tok{n:03d}q]] holdout train validation", "files": {"fixture.md": f"file-tok{n:03d}q\n"},
                          "expected": {"answer": f"EXP[[tok{n:03d}q]]"}, "source": "skill"})
    out.mkdir(mode=0o700)
    _ = (out / DRAFT_NAME).write_text(json.dumps(cases))
    approved_hash, calls = approvals(target, out, edit=edit)
    seen: dict[str, list[object]] = {}

    def spy_search(seed: dict[str, str], edit_mode: Edit, train: list[object], validation: list[object],
                   evaluate: Evaluate, propose: Propose, *, metric_calls: int) -> object:
        seen["train"], seen["validation"] = list(train), list(validation)
        return search(seed, edit_mode, train, validation, evaluate, propose, metric_calls=metric_calls)

    monkeypatch.setattr(_workflow, "pareto_search", spy_search)
    spy = Spy()
    result = run(target, out, MODEL, live=True, edit=cast(Edit, edit), approve_cases=approved_hash,
                 approve_budget=calls, factory=spy.factory)
    assert result["phase"] == "complete"
    holdout = [case for case in frozen(out) if case["split"] == "holdout"]
    assert len(holdout) >= HOLDOUT_MINIMUM
    ids = {str(case["id"]) for case in holdout}
    secrets = [token for case in holdout for token in (str(case["request"]), json.dumps(case["expected"]),
                                                       json.dumps(case["files"]))]
    assert seen["train"] and seen["validation"] and ids.isdisjoint(seen["train"] + seen["validation"])
    assert spy.prompts
    for prompt in spy.prompts:
        assert not any(item in prompt for item in [*ids, *secrets, *(str(c["request"]).split("[[")[1][:6] for c in holdout)])
    for name, held, files in spy.evaluated:
        assert (name in ids) == held
        assert not any(token in text for text in files.values() for token in secrets)
    outcomes = cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
    searched = json.dumps([item for item in outcomes if item.get("split") != "holdout"])
    assert not any(item in searched for item in ids)
    _ = export(out, tmp_path / "export")
    exported = (tmp_path / "export/report.json").read_text() + (tmp_path / "export/candidate.patch").read_text()
    assert not any(item in exported for item in secrets)


def test_pending_cases_are_never_scored_even_in_the_holdout_family(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    """PA-5: trigger and near-miss cases stay pending and no provider call scores them."""
    target, out = make_target(tmp_path), tmp_path / "run"
    cases: list[dict[str, object]] = []
    for family in range(5):
        for index in range(2):
            cases.append({"id": f"t{family}-{index}", "family": f"f{family}", "kind": "task", "request": "r",
                          "files": {"fixture.md": "hello\n"}, "expected": {}, "source": "skill"})
    for family in range(5):
        cases.append({"id": f"p{family}", "family": f"f{family}", "kind": "trigger", "request": "pending", "source": "skill"})
    cases.append({"id": "p-only", "family": "only-pending", "kind": "near-miss", "request": "pending", "source": "skill"})
    out.mkdir(mode=0o700)
    _ = (out / DRAFT_NAME).write_text(json.dumps(cases))
    approved_hash, calls = approvals(target, out)
    spy = Spy()
    _ = run(target, out, MODEL, live=True, approve_cases=approved_hash, approve_budget=calls, factory=spy.factory)
    scored = {name for name, _held, _files in spy.evaluated}
    assert scored and not any(name.startswith("p") and name != "p" and name[1:].isdigit() or name == "p-only" for name in scored)
    document = cast(dict[str, list[dict[str, object]]], json.loads((out / "cases.json").read_text()))
    assert {str(item["id"]) for item in document["pending"]} == {f"p{n}" for n in range(5)} | {"p-only"}
    assert not any(str(item["id"]).startswith("p") for item in document["cases"])


# --- PA-5: split invariants under fuzz -------------------------------------------------------------------

def _fuzz_draft(rng: random.Random) -> list[DraftCase]:
    cases: list[DraftCase] = []
    families = rng.randint(3, 9)
    for family in range(families):
        for index in range(rng.randint(1, 5)):
            cases.append(DraftCase(f"c{family}-{index}", f"fam{family}é", "task", f"r{family}{index}", "skill", {"a": "b"}, {}))
    for index in range(rng.randint(0, 6)):
        family = f"fam{rng.randrange(families)}é" if rng.random() < 0.5 else f"pend{index}"
        cases.append(DraftCase(f"p{index}", family, rng.choice(PENDING_KINDS), "pending", "skill", {}, None))
    rng.shuffle(cases)
    return cases


@pytest.mark.parametrize("trial", range(150))
def test_split_invariants_hold_for_random_drafts(trial: int) -> None:
    """PA-5: family never crosses splits, splits are deterministic and order-free, holdout reaches 6 when possible."""
    rng = random.Random(trial)
    cases = _fuzz_draft(rng)
    seed = rng.randrange(-(2**40), 2**40)
    first = split_cases(cases, seed, "echo")
    families: dict[str, set[str]] = {}
    for item in first:
        families.setdefault(item.family, set()).add(item.split)
    assert all(len(splits) == 1 for splits in families.values())
    assigned = {item.identifier: item.split for item in first}
    shuffled = list(cases)
    rng.shuffle(shuffled)
    assert {item.identifier: item.split for item in split_cases(shuffled, seed, "echo")} == assigned
    assert split_cases(cases, seed, "echo") == first
    scored = [item for item in first if not item.pending]
    assert all(item.kind == "echo" for item in scored) and all(item.kind in PENDING_KINDS for item in first if item.pending)
    sizes: dict[str, int] = {}
    for item in scored:
        sizes[item.family] = sizes.get(item.family, 0) + 1
    for split in ("train", "validation", "holdout"):
        assert any(item.split == split for item in scored), split
    if sum(sizes.values()) - sum(sorted(sizes.values())[:2]) >= HOLDOUT_MINIMUM:
        assert sum(item.split == "holdout" for item in scored) >= HOLDOUT_MINIMUM
    assert case_hash(first, seed) == case_hash(list(reversed(first)), seed) != case_hash(first, seed + 1)


def test_the_seed_actually_changes_the_split() -> None:
    """PA-5: the recorded seed drives the assignment, so a seed is not a constant."""
    cases = [DraftCase(f"c{f}-{i}", f"f{f}", "task", "r", "skill", {"a": "b"}, {}) for f in range(8) for i in range(3)]
    layouts = {tuple(sorted((item.identifier, item.split) for item in split_cases(cases, seed))) for seed in range(30)}
    assert len(layouts) > 1


# --- PA-6: gate math -------------------------------------------------------------------------------------

def _dyadic(rng: random.Random) -> list[float]:
    pool = [rng.randint(-64, 64) / 64 for _ in range(rng.randint(0, 12))]
    return [pool[0]] * len(pool) if pool and rng.random() < 0.3 else pool


@pytest.mark.parametrize("trial", range(200))
def test_gate_is_sign_symmetric_scale_invariant_and_order_free(trial: int) -> None:
    """PA-6: negating deltas swaps promote and reject; scaling by powers of two and shuffling change nothing."""
    rng = random.Random(trial)
    values = _dyadic(rng)
    base = verdict(values)
    flipped = verdict([-value for value in values])
    swap = {"promote": "reject", "reject": "promote", "inconclusive": "inconclusive"}
    assert flipped.verdict == swap[base.verdict]
    assert (flipped.delta, flipped.se, flipped.cases) == (-base.delta if base.delta else 0.0, base.se, base.cases)
    scale = 2.0 ** rng.randint(-20, 10)
    assert verdict([value * scale for value in values]).verdict == base.verdict
    shuffled = list(values)
    rng.shuffle(shuffled)
    assert verdict(shuffled) == base
    assert verdict({str(i): value for i, value in enumerate(values)}) == base
    if len(values) < 2:
        assert base.verdict == "inconclusive"


@pytest.mark.parametrize("values", [[math.nan] * 6, [math.inf] * 6, [math.inf, 0.0, 0.0, 0.0], [-math.inf] * 4,
                                    [0.5, 0.5, math.nan, 0.5]])
def test_gate_never_promotes_non_finite_deltas(values: list[float]) -> None:
    """PA-6: non-finite deltas raise ValueError or stay inconclusive, never promote."""
    try:
        outcome = verdict(values)
    except ValueError:
        return
    assert outcome.verdict != "promote"


def test_gate_with_one_huge_outlier_does_not_promote() -> None:
    """PA-6: a single huge outlier among zeros is inconclusive."""
    assert verdict([1e9] + [0.0] * 9).verdict == "inconclusive"
    assert verdict([-1e9] + [0.0] * 9).verdict == "inconclusive"
    assert verdict([1.0] * 9 + [-1e9]).verdict != "promote"


def test_case_deltas_average_repeats_of_different_lengths_and_reject_mismatched_cases() -> None:
    """PA-6: repeats of unequal length average per case; a missing or empty case is an error."""
    deltas = case_deltas({"a": [0.0, 0.0, 1.0], "b": [1.0]}, {"a": [1.0], "b": [0.0, 1.0]})
    assert deltas["a"] == pytest.approx(1.0 - 1 / 3) and deltas["b"] == pytest.approx(-0.5)
    with pytest.raises(ValueError):
        _ = case_deltas({"a": [1.0]}, {"b": [1.0]})
    with pytest.raises(ValueError):
        _ = case_deltas({"a": []}, {"a": [1.0]})
    assert verdict(case_deltas({}, {})).verdict == "inconclusive"


@pytest.mark.parametrize("score", [math.nan, math.inf, 5.0, -3.0, 1e308])
def test_a_winner_with_an_out_of_range_holdout_score_is_not_promoted_by_the_score_alone(
        score: float, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """PA-6: a holdout score outside 0..1 is rejected or clamped, as the search side clamps it."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    approved_hash, calls = approvals(target, out)
    spy = Spy(improved_score=score)
    result: dict[str, object] | None = None
    try:
        result = run(target, out, MODEL, live=True, approve_cases=approved_hash, approve_budget=calls, factory=spy.factory)
    except (ValueError, RuntimeError):
        pass
    if result is not None:
        gate = cast(dict[str, object], result["gate"])
        assert abs(cast(float, gate["delta"])) <= 1.0, gate


# --- PA-7: concurrency -----------------------------------------------------------------------------------

def test_search_never_runs_more_than_two_calls_at_once_whatever_workers_asks() -> None:
    """PA-7: `workers=16` on the real GEPA path clamps to the two-call cap for evaluate and propose together."""
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    def enter() -> None:
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.01)
        with lock:
            state["now"] -= 1

    def evaluate(candidate: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        enter()
        return float("improved" in candidate["SKILL.md"]) / 2 + (0.0 if "1" in str(example) else 0.1), {"task_correct": 0.0}

    def propose(_candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        enter()
        return {key: "improved" for key in components}

    _ = search({"SKILL.md": "seed"}, "prose", ["t1", "t2", "t3", "t4"], ["v1", "v2", "v3"], evaluate, propose,
               metric_calls=60, workers=16)
    assert 1 <= state["peak"] <= MAX_CONCURRENT_CALLS


def test_a_full_run_keeps_at_most_two_provider_calls_in_flight(
        tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    """PA-7: search, reflection, and the holdout gate together stay at two concurrent calls."""
    spy = Spy(delay=0.005)
    assert approved_run(make_target(tmp_path), tmp_path / "run", factory=spy.factory)["phase"] == "complete"
    assert 1 <= spy.peak <= MAX_CONCURRENT_CALLS


# --- PA-8: removed commands ------------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [["search", "--mode", "cli"], ["search", "--mode=prose", "--live"], ["evaluate", "--help"],
                                  ["dataset"], ["baseline", "--target", "x", "--out", "y"], ["search", "-h"],
                                  ["dataset", "--help", "--unknown"], ["evaluate", ""], ["baseline", "\x00"]])
def test_removed_commands_with_any_arguments_exit_coded_and_name_run(
        argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    """PA-8: extra flags never turn a removed command into a parser error or a traceback."""
    assert main(argv) == 1
    captured = capsys.readouterr()
    error = cast(dict[str, object], json.loads(captured.err))
    assert error["code"] == "command-removed" and "`run`" in cast(str, error["error"]) and argv[0] in cast(str, error["error"])
    assert captured.out == "" and "Traceback" not in captured.err


# --- PA-9: old schema ------------------------------------------------------------------------------------

@pytest.mark.parametrize("version", [1, "missing", "3", 2, 4, 0, True, [3], {"v": 3}, None, -3])
def test_a_run_record_with_a_foreign_schema_stops_coded_and_creates_no_provider(
        version: object, tmp_path: Path, make_target: Callable[..., Path], capsys: pytest.CaptureFixture[str]) -> None:
    """PA-9: only schema_version 3 opens; every other value, schema 2 included, stops with run-schema-old."""
    target, out = make_target(tmp_path), tmp_path / "run"
    out.mkdir(mode=0o700)
    record: dict[str, object] = {"phase": "prepared", "model": MODEL}
    if version != "missing":
        record["schema_version"] = version
    write(out / "run.json", record)
    spy = Spy(refuse=True)
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, factory=spy.factory)
    assert caught.value.code == "run-schema-old"
    with pytest.raises(CodedError) as exported:
        _ = export(out, tmp_path / "export")
    assert exported.value.code == "run-schema-old" and not (tmp_path / "export").exists()
    assert main(["run", "--target", str(target), "--out", str(out), "--model", MODEL, "--live"]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "run-schema-old"


@pytest.mark.parametrize("content", ["{", "[]", "null", "\"text\"", "\xff\xfe", ""])
def test_a_corrupt_run_record_exits_with_an_error_not_a_traceback(
        content: str, tmp_path: Path, make_target: Callable[..., Path], capsys: pytest.CaptureFixture[str]) -> None:
    """PA-9: a malformed `run.json` exits 1 with a JSON error on stderr."""
    target, out = make_target(tmp_path), tmp_path / "run"
    out.mkdir(mode=0o700)
    _ = (out / "run.json").write_bytes(content.encode("latin-1"))
    assert main(["run", "--target", str(target), "--out", str(out), "--model", MODEL, "--live"]) == 1
    assert "error" in json.loads(capsys.readouterr().err)


# --- PA-10: export is write-only -------------------------------------------------------------------------

def _complete(tmp_path: Path, make_target: Callable[..., Path],
              approved_run: Callable[..., dict[str, object]]) -> tuple[Path, Path]:
    target, out = make_target(tmp_path), tmp_path / "run"
    assert approved_run(target, out, factory=Spy().factory)["phase"] == "complete"
    return target, out


def test_export_into_the_target_directory_is_refused(
        tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    """PA-10: `--out` inside the target must not write there, as `run --out` refuses it."""
    target, out = _complete(tmp_path, make_target, approved_run)
    before = snapshot(target)
    try:
        _ = export(out, target / "exported")
    except (OSError, ValueError):
        pass
    assert snapshot(target) == before


def test_export_through_a_symlinked_parent_into_the_target_is_refused(
        tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    """PA-10: a symlink to the target as the `--out` parent must not let the export write into the target."""
    target, out = _complete(tmp_path, make_target, approved_run)
    link = tmp_path / "elsewhere"
    link.symlink_to(target)
    before = snapshot(target)
    try:
        _ = export(out, link / "exported")
    except (OSError, ValueError):
        pass
    assert snapshot(target) == before


def test_export_to_an_existing_or_symlinked_destination_writes_nothing(
        tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    """PA-10: an existing directory or a symlink as `--out` fails and leaves the target and the old files intact."""
    target, out = _complete(tmp_path, make_target, approved_run)
    existing = tmp_path / "existing"
    existing.mkdir()
    _ = (existing / "keep").write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    before = snapshot(target)
    for destination in (existing, link):
        with pytest.raises(OSError):
            _ = export(out, destination)
    assert snapshot(target) == before and (existing / "keep").read_text() == "keep" and sorted(p.name for p in existing.iterdir()) == ["keep"]


def test_export_of_an_incomplete_run_creates_no_destination(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """PA-10: a prepared run exports nothing."""
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    with pytest.raises(ValueError, match="completed"):
        _ = export(prep.out, tmp_path / "export")
    assert not (tmp_path / "export").exists()


# --- PA-11: no provider before both approvals ------------------------------------------------------------

def _contract_for(target: Path, kinds: dict[str, object]) -> None:
    contract: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": "echo-skill",
                                   "invocation": "$echo-skill run", "kinds": kinds, "editable": []}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(contract))


@pytest.mark.parametrize("stop", ["cases-missing", "cases-unapproved", "budget-unapproved", "live-required", "contract-unreadable",
                                  "contract-unreadable-dir", "contract-kinds", "contract-audit-unsupported"])
def test_every_stop_before_the_session_creates_no_provider(
        stop: str, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    """PA-11: each stop path leaves the factory and `Configuration.create` untouched."""
    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a provider was created before every approval")

    monkeypatch.setattr(Configuration, "create", refuse)
    target, out = make_target(tmp_path), tmp_path / "run"
    contract = target / "evals/autoimprove.json"
    if stop != "cases-missing":
        _ = write_draft(out)
    if stop == "contract-unreadable":
        contract.unlink()
        contract.symlink_to(tmp_path / "nowhere.json")
    elif stop == "contract-unreadable-dir":
        contract.unlink()
        contract.mkdir()
    elif stop == "contract-kinds":
        _contract_for(target, {"a": {"grader": "exact-json"}, "b": {"grader": "exact-json"}})
    elif stop == "contract-audit-unsupported":
        _contract_for(target, {"task": {"grader": "audit"}})
    spy = Spy(refuse=True)
    live = stop != "live-required"
    for options in ({}, {"approve_cases": "0" * 64}, {"approve_cases": "0" * 64, "approve_budget": 200}):
        try:
            _ = run(target, out, MODEL, live=live, factory=spy.factory, **cast(dict[str, object], options))  # pyright: ignore[reportArgumentType]
        except CodedError as error:
            assert error.code in ("cases-missing", "cases-unapproved", "budget-unapproved", "live-required",
                                  "contract-unreadable", "contract-kinds", "contract-audit-unsupported")
            if stop.startswith("contract"):
                assert error.code == stop.removesuffix("-dir")
    assert not (out / "run.json").exists()
    if stop == "live-required":
        approved_hash, calls = _hash_and_calls(target, out)
        with pytest.raises(Stop) as stopped:
            _ = run(target, out, MODEL, live=False, approve_cases=approved_hash, approve_budget=calls, factory=spy.factory)
        assert stopped.value.code == "live-required"


def _hash_and_calls(target: Path, out: Path) -> tuple[str, int]:
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, live=True)
    approved_hash = cast(str, cases.value.data["case_hash"])
    with pytest.raises(Stop) as budget:
        _ = run(target, out, MODEL, live=True, approve_cases=approved_hash)
    return approved_hash, cast(dict[str, int], budget.value.data["estimate"])["calls"]


# --- PA-12: Claude credential link -----------------------------------------------------------------------

linux_only = pytest.mark.skipif(sys.platform == "darwin", reason="macOS uses the real config directory")


def _claude(tmp_path: Path) -> ClaudeCode:
    tool = tmp_path / "claude-bin"
    tool.mkdir(exist_ok=True)
    executable = tool / "claude"
    _ = executable.write_text("#!/usr/bin/env python3\nimport json, os\nprint(json.dumps(dict(os.environ)))\n")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return ClaudeCode(MODEL, Budget(10, 120, 0), lambda: None, executable)


@linux_only
@pytest.mark.usefixtures("umask_022")
def test_config_dir_is_private_outside_the_sandbox_and_holds_only_the_real_login(
        tmp_path: Path, host_login: Path) -> None:
    """PA-12: the temp config dir is private, unreadable by the sandbox rules, and holds one link to the real login."""
    session = _claude(tmp_path)
    directory = session.config_dir
    try:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700 and directory.name.startswith(CONFIG_PREFIX)
        assert [entry.name for entry in directory.iterdir()] == [CREDENTIALS]
        link = directory / CREDENTIALS
        assert link.is_symlink() and Path(os.readlink(link)) == host_login.resolve()
        workspace = tmp_path / "workspace"
        floor = cast(dict[str, dict[str, dict[str, list[str]]]], settings(workspace))
        for allowed in floor["sandbox"]["filesystem"]["allowRead"]:
            assert not directory.is_relative_to(allowed) and not directory.resolve().is_relative_to(allowed)
        assert not directory.is_relative_to(workspace)
        deny = cast(dict[str, dict[str, list[str]]], settings(workspace))["permissions"]["deny"]
        assert any(CONFIG_PREFIX in rule for rule in deny)
    finally:
        session.close()
    assert not directory.exists()


@linux_only
def test_a_predictable_attacker_symlink_cannot_capture_the_config_dir(tmp_path: Path) -> None:
    """PA-12: pre-planted symlinks named like the config dir are never used or deleted through."""
    scratch = Path(tempfile.gettempdir())
    victim = tmp_path / "victim"
    victim.mkdir()
    _ = (victim / "precious").write_text("data")
    for name in (CONFIG_PREFIX, CONFIG_PREFIX + "000000", CONFIG_PREFIX + "aaaaaaaa"):
        (scratch / name).symlink_to(victim, target_is_directory=True)
    session = _claude(tmp_path)
    try:
        assert session.config_dir.parent == scratch and not session.config_dir.is_symlink()
        assert not (victim / CREDENTIALS).exists()
    finally:
        session.close()
    assert (victim / "precious").read_text() == "data" and [p.name for p in victim.iterdir()] == ["precious"]


@linux_only
def test_close_after_the_login_inode_is_replaced_warns_with_credential_rotated_and_still_deletes_the_dir(
        tmp_path: Path, host_login: Path) -> None:
    """PA-12: an atomic rename over the login file is a resumable warning, and the temp dir still goes."""
    session = _claude(tmp_path)
    directory = session.config_dir
    replacement = host_login.with_name("rotated")
    login = json.dumps({"claudeAiOauth": {"accessToken": "rotated", "refreshToken": "r-rotated"}})
    _ = replacement.write_text(login)
    _ = replacement.replace(host_login)
    with pytest.raises(CodedError) as caught:
        session.close()
    assert caught.value.code == "credential-rotated" and not directory.exists()
    assert host_login.read_text() == login
    session.close()


@linux_only
def test_close_after_the_link_is_repointed_raises_credential_changed_and_still_deletes_the_dir(
        tmp_path: Path) -> None:
    """PA-12: a link swapped to another file is detected, and the temp dir still goes."""
    session = _claude(tmp_path)
    directory = session.config_dir
    other = tmp_path / "other.json"
    _ = other.write_text("{}")
    (directory / CREDENTIALS).unlink()
    (directory / CREDENTIALS).symlink_to(other)
    with pytest.raises(CodedError) as caught:
        session.close()
    assert caught.value.code == "credential-changed" and not directory.exists()


@linux_only
def test_close_when_the_config_dir_became_a_symlink_never_deletes_through_it(tmp_path: Path) -> None:
    """PA-12: replacing the temp dir by a symlink to a victim leaves the victim intact."""
    session = _claude(tmp_path)
    directory = session.config_dir
    victim = tmp_path / "victim"
    victim.mkdir()
    _ = (victim / "precious").write_text("data")
    for entry in directory.iterdir():
        entry.unlink()
    directory.rmdir()
    directory.symlink_to(victim, target_is_directory=True)
    try:
        with pytest.raises(CodedError):
            session.close()
    finally:
        directory.unlink(missing_ok=True)
    assert (victim / "precious").read_text() == "data"


@linux_only
@pytest.mark.parametrize("name", ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "claude_code_oauth_token",
                                  "Anthropic_Api_Key", "GH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN ", "CLAUDE_API_KEY"])
def test_no_token_variable_reaches_the_child_environment(
        name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PA-12: whatever token variable the host sets, the child sees only the fixed allowlist."""
    monkeypatch.setenv(name, "sk-secret-value")

    def empty(self: ClaudeCode) -> Inventory:
        del self
        return Inventory(frozenset(), frozenset())
    monkeypatch.setattr(ClaudeCode, "inventory", empty)
    session = _claude(tmp_path)
    try:
        workspace = tmp_path / "workspace"
        for directory in ("home", "tmp", ".agents/skills"):
            (workspace / directory).mkdir(parents=True, exist_ok=True)
        _code, _stderr, events = session._run(workspace, "hello", None, 30, [])
        child = cast(dict[str, str], events[0])
        assert "sk-secret-value" not in json.dumps(child)
        assert not any(key.upper().replace(" ", "") == name.upper().replace(" ", "") for key in child)
        assert set(child) <= {"PATH", "HOME", "TMPDIR", "LANG", "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",
                              "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS", "CLAUDE_CONFIG_DIR", "PWD", "SHLVL", "_", "LC_CTYPE"}
        assert child["HOME"] == str(workspace / "home") and child["CLAUDE_CONFIG_DIR"] == str(session.config_dir)
    finally:
        session.close()


@linux_only
@pytest.mark.parametrize("kind", ["missing", "dangling", "directory"])
def test_a_missing_login_stops_coded_and_leaves_no_temp_dir(
        kind: str, tmp_path: Path, host_login: Path) -> None:
    """PA-12: no usable login stops as login-missing before any temp dir exists."""
    host_login.unlink()
    if kind == "dangling":
        host_login.symlink_to(tmp_path / "gone.json")
    elif kind == "directory":
        host_login.mkdir()
    scratch = Path(tempfile.gettempdir())
    with pytest.raises(CodedError) as caught:
        _ = _claude(tmp_path)
    assert caught.value.code == "login-missing"
    assert not list(scratch.glob(CONFIG_PREFIX + "*"))


# --- Coded stops for tampered records, config changes, and unsafe exports -----------------------------------

def test_a_tampered_estimate_stops_as_run_record_tampered(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    tamper(prep.out, lambda record: cast(dict[str, object], record["estimate"]).update(metric_calls=10**6))
    with pytest.raises(Stop) as stopped:
        _ = run(prep.target, prep.out, MODEL, live=True, factory=Spy(refuse=True).factory)
    assert stopped.value.code == "run-record-tampered"


def test_an_unknown_phase_stops_as_run_record_tampered_and_runs_no_stage(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    tamper(prep.out, lambda record: record.update(phase="gating"))
    spy = Spy(refuse=True)
    with pytest.raises(Stop) as stopped:
        _ = run(prep.target, prep.out, MODEL, live=True, factory=spy.factory)
    assert stopped.value.code == "run-record-tampered"


def test_a_tampered_seed_stops_resume(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    tamper(prep.out, lambda record: cast(dict[str, str], record["seed"]).update(
        {"SKILL.md": cast(dict[str, str], record["seed"])["SKILL.md"] + "\ntampered\n"}))
    with pytest.raises(ValueError, match="seed differs from the frozen record"):
        _ = run(prep.target, prep.out, MODEL, live=True, factory=Spy(refuse=True).factory)


def test_a_changed_engine_hash_stops_resume(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    tamper(prep.out, lambda record: record.update(engine_hash="0" * 64))
    with pytest.raises(ValueError, match="evaluator differs from the frozen record"):
        _ = run(prep.target, prep.out, MODEL, live=True, factory=Spy(refuse=True).factory)


@pytest.mark.parametrize(("change", "flag"), [({"model": "other-model"}, "--model"), ({"edit": "prose+cli"}, "--edit"),
                                              ({"repeats": 7}, "--repeats")])
def test_a_resume_with_a_different_config_stops_as_run_config_differs_and_names_the_flag(
        change: dict[str, object], flag: str, tmp_path: Path, make_target: Callable[..., Path],
        write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    model = cast(str, change.get("model", MODEL))
    with pytest.raises(CodedError) as stopped:
        _ = run(prep.target, prep.out, model, live=True, factory=Spy(refuse=True).factory,
                edit=cast(Edit | None, change.get("edit")), repeats=cast(int | None, change.get("repeats")))
    assert stopped.value.code == "run-config-differs" and flag in str(stopped.value)


def test_an_export_into_the_target_stops_as_export_into_target(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    assert run(prep.target, prep.out, MODEL, live=True, factory=Spy().factory)["phase"] == "complete"
    with pytest.raises(CodedError) as stopped:
        _ = export(prep.out, prep.target / "export")
    assert stopped.value.code == "export-into-target"


def test_a_corrupt_record_creates_no_harness_resources(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    """The record validates before `Configuration.create`, which makes the credential symlink dirs."""
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    tamper(prep.out, lambda record: record.update(outcomes="corrupt"))
    created: list[object] = []

    def create(*args: object, **_kwargs: object) -> None:
        created.append(args)

    monkeypatch.setattr(Configuration, "create", create)
    with pytest.raises(ValueError, match="outcomes"):
        _ = run(prep.target, prep.out, MODEL, live=True, configuration=Configuration({}))
    assert created == []


def test_an_oversized_proposal_scores_zero_with_a_rejected_feedback_entry(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    prep = prepare(tmp_path, make_target, write_draft, approvals)
    session = _workflow._Session(prep.out, MODEL, "claude", Spy().factory, None, statistics=DEFAULT_STATISTICS)
    try:
        components = {name: "x" * 262145 for name in session.seed.editable}
        score, feedback = session._evaluate_example(components, next(iter(session._search_cases)))
    finally:
        session.provider.close()
    assert score == 0.0 and "proposal exceeds" in str(feedback["rejected"])
    assert session.outcomes == []


def test_a_missing_builtin_harness_stops_before_the_cases_approval(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(CodedError) as stopped:
        _ = run(target, out, MODEL, live=True)
    assert stopped.value.code == "harness-missing" and not (out / "run.json").exists()

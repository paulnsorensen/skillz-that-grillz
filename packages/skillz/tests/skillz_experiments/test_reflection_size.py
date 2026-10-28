"""A large editable set gets a longer per-call timeout, a prose fallback or a warning, and a recorded prompt size (gh-563)."""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments import _runtime
from skillz_experiments._budget import CALL_TIMEOUT_FACTOR, PROMPT_CHARS_PER_SECOND, Budget, CallTimeout
from skillz_experiments._records import read
from skillz_experiments.run import Stop, run
from skillz_experiments.run._plan import REFLECTION_TEXT_LIMIT
from test_default_edit import MODEL, _prepare
from test_run_search import _start

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]
LIMIT_NOTE = "more than the reflection limit"


@pytest.fixture(autouse=True)
def _restore_call_slots() -> Iterator[None]:
    yield
    _runtime.reset_call_slots()


@pytest.fixture
def timeouts(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the timeout of every process the model calls start. The real process still runs."""
    seen: list[float] = []
    original = _runtime.process

    def spy(command: list[str], *, cwd: Path, timeout: float, **options: object) -> subprocess.CompletedProcess[str]:
        seen.append(timeout)
        return original(command, cwd=cwd, timeout=timeout, **options)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(_runtime, "process", spy)
    return seen


def _large_helpers(target: Path) -> Path:
    """Add two helper scripts whose text puts the prose+cli editable set over the reflection limit."""
    line = "# " + "x" * 98 + "\n"
    for name in ("scripts/big_a.py", "scripts/big_b.py"):
        _ = (target / name).write_text(line * (REFLECTION_TEXT_LIMIT // 2 // len(line) + 10))
    return target


@pytest.mark.ac("gh-563:AC-1")
def test_a_model_call_timeout_grows_with_the_prompt_size(tmp_path: Path, timeouts: list[float]) -> None:
    command = [sys.executable, "-c", "import sys; sys.stdin.read()"]
    budget = Budget(10, 1000, 0, call_seconds=1)
    for text in (None, "p" * 2000):
        _ = _runtime.model_call(budget, command, cwd=tmp_path, environment={}, input_text=text)
    assert timeouts == [CALL_TIMEOUT_FACTOR * 1.0, CALL_TIMEOUT_FACTOR * 1.0 + 2000 / PROMPT_CHARS_PER_SECOND]


@pytest.mark.ac("gh-563:AC-1")
def test_a_large_prompt_timeout_stays_capped_by_the_remaining_search_time() -> None:
    budget = Budget(10, 5, 0, call_seconds=1)
    assert 0 < budget.call_timeout(10**9) <= 5


@pytest.mark.ac("gh-563:AC-2")
def test_timed_out_and_answered_reflection_outcomes_record_the_prompt_size(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda number: CallTimeout("the model call exceeded its per-call timeout") if number == 1 else None
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    rows = [row for row in cast(list[dict[str, object]], read(out / "run.json")["outcomes"]) if row.get("split") == "reflection"]
    assert result["phase"] == "complete"
    timed_out = [row for row in rows if row.get("status") == "timeout"]
    answered = [row for row in rows if "status" not in row]
    assert len(timed_out) == 1 and answered
    for row in [*timed_out, *answered]:
        chars = row["prompt_chars"]
        assert isinstance(chars, int) and chars > len("seed")


@pytest.mark.ac("gh-563:AC-3")
def test_an_omitted_edit_over_the_reflection_limit_plans_prose_and_names_the_limit(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, capsys: pytest.CaptureFixture[str]) -> None:
    target = _large_helpers(make_target(tmp_path, helper=True))
    _ = write_draft(tmp_path / "run")
    with pytest.raises(Stop) as cases:
        _ = run(target, tmp_path / "run", MODEL, live=True)
    with pytest.raises(Stop) as budget:
        _ = run(target, tmp_path / "run", MODEL, live=True, approve_cases=cast(str, cases.value.data["case_hash"]))
    err = capsys.readouterr().err
    assert budget.value.code == "budget-unapproved" and "reflection_warning" not in budget.value.data
    assert LIMIT_NOTE in err and "this run uses --edit prose" in err
    record, _ = _prepare(target, tmp_path / "run2", write_draft, capsys)
    assert record["edit"] == "prose"
    assert all(name.endswith(".md") for name in cast(list[str], record["editable"]))


@pytest.mark.ac("gh-563:AC-3")
def test_an_omitted_edit_under_the_reflection_limit_keeps_prose_cli(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, capsys: pytest.CaptureFixture[str]) -> None:
    record, _ = _prepare(make_target(tmp_path, helper=True), tmp_path / "run", write_draft, capsys)
    assert record["edit"] == "prose+cli"


@pytest.mark.ac("gh-563:AC-4")
def test_an_explicit_prose_cli_over_the_reflection_limit_warns_in_the_budget_question(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, capsys: pytest.CaptureFixture[str]) -> None:
    target, out = _large_helpers(make_target(tmp_path, helper=True)), tmp_path / "run"
    _ = write_draft(out)
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, live=True, edit="prose+cli")
    with pytest.raises(Stop) as budget:
        _ = run(target, out, MODEL, live=True, edit="prose+cli", approve_cases=cast(str, cases.value.data["case_hash"]))
    warning = cast(str, budget.value.data["reflection_warning"])
    assert budget.value.code == "budget-unapproved"
    assert LIMIT_NOTE in warning and str(REFLECTION_TEXT_LIMIT) in warning and "--edit prose" in warning
    assert "warning: " + warning in capsys.readouterr().err


@pytest.mark.ac("gh-563:AC-4")
def test_a_budget_question_under_the_reflection_limit_has_no_warning(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out = make_target(tmp_path, helper=True), tmp_path / "run"
    _ = write_draft(out)
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, live=True)
    with pytest.raises(Stop) as budget:
        _ = run(target, out, MODEL, live=True, approve_cases=cast(str, cases.value.data["case_hash"]))
    assert budget.value.code == "budget-unapproved" and "reflection_warning" not in budget.value.data

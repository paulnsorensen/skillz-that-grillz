"""`prose+cli` is the default edit mode, with a prose fallback for a skill that has only Markdown to edit (gh-302)."""
# pyright: reportPrivateUsage=false
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import NEW_CLI, WEDGE_PREFIX
from skillz_experiments._cases import CodedError
from skillz_experiments._records import read
from skillz_experiments._contract import load_contract
from skillz_experiments._wedge_targets import TargetPlan, plan_targets
from skillz_experiments.gate import DEFAULT_STATISTICS
from skillz_experiments.run import Stop, _run, _session, run
from test_new_cli import Recorder, layered_world

pytestmark = pytest.mark.usefixtures("host_login")

MODEL = "local-test"
NOTE = "this run uses --edit prose"
Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


def _drop_new_cli(plan: TargetPlan, directory: Path) -> tuple[TargetPlan, dict[str, object]]:
    """Stand in for `settle` when it drops an optional `@new-cli` (a failed site layer or a layer without cyclopts)."""
    del directory
    return replace(plan, capable=None, dropped="the fromargs site layer lacks cyclopts"), {}


def _prepare(target: Path, out: Path, write_draft: Drafter, capsys: pytest.CaptureFixture[str],
             **flags: object) -> tuple[dict[str, object], int]:
    """Give both approvals, then stop at `live-required`. Return the run record and the count of fallback notes of the last call."""
    _ = write_draft(out)
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, live=True, **flags)  # pyright: ignore[reportArgumentType]
    case_hash = cast(str, cases.value.data["case_hash"])
    with pytest.raises(Stop) as budget:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, **flags)  # pyright: ignore[reportArgumentType]
    calls = cast(dict[str, int], budget.value.data["estimate"])["calls"]
    _ = capsys.readouterr()
    with pytest.raises(Stop) as ready:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls, **flags)  # pyright: ignore[reportArgumentType]
    assert ready.value.code == "live-required"
    return read(out / "run.json"), capsys.readouterr().err.count(NOTE)


@pytest.mark.ac("gh-302:AC-5")
def test_an_omitted_edit_on_a_skill_with_a_helper_script_plans_prose_cli_components(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, capsys: pytest.CaptureFixture[str]) -> None:
    record, notes = _prepare(make_target(tmp_path, helper=True), tmp_path / "run", write_draft, capsys)
    assert record["edit"] == "prose+cli"
    assert "scripts/echo.py" in cast(list[str], record["editable"])
    assert notes == 0


@pytest.mark.ac("gh-302:AC-6")
def test_an_omitted_edit_on_a_markdown_only_skill_plans_prose_and_writes_the_note_once(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, capsys: pytest.CaptureFixture[str]) -> None:
    record, notes = _prepare(make_target(tmp_path), tmp_path / "run", write_draft, capsys)
    assert record["edit"] == "prose"
    assert all(name.endswith(".md") for name in cast(list[str], record["editable"]))
    assert notes == 1


@pytest.mark.ac("gh-302:AC-7")
@pytest.mark.parametrize("after_settle", [False, True])
def test_an_explicit_prose_cli_on_a_markdown_only_skill_stops_with_helper_missing(
        after_settle: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_target: Maker, write_draft: Drafter,
        approvals: Approver, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "run"
    if after_settle:
        target = layered_world(tmp_path, monkeypatch, make_target, "cyclopts").skill
        monkeypatch.setattr(_run, "settle", _drop_new_cli)
    else:
        target = make_target(tmp_path)
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    _ = capsys.readouterr()
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, edit="prose+cli", approve_cases=case_hash, approve_budget=calls)
    assert caught.value.code == "helper-missing"
    assert NOTE not in capsys.readouterr().err
    assert not (out / "run.json").exists()


@pytest.mark.ac("gh-302:AC-8")
@pytest.mark.parametrize(("helper", "mode"), [(False, "prose"), (True, "prose+cli")])
def test_a_resume_without_edit_keeps_the_recorded_mode(
        helper: bool, mode: str, tmp_path: Path, make_target: Maker, write_draft: Drafter,
        capsys: pytest.CaptureFixture[str]) -> None:
    target, out = make_target(tmp_path, helper=helper), tmp_path / "run"
    record, _ = _prepare(target, out, write_draft, capsys)
    assert record["edit"] == mode
    with pytest.raises(Stop) as resumed:
        _ = run(target, out, MODEL, approve_cases=cast(str, record["cases_hash"]))
    assert resumed.value.code == "live-required"
    assert read(out / "run.json")["edit"] == mode
    other = "prose" if mode == "prose+cli" else "prose+cli"
    with pytest.raises(CodedError, match="differs from the frozen record"):
        _ = run(target, out, MODEL, edit=other)
    session = _session.Session(out, MODEL, "claude", Recorder().factory, None, statistics=DEFAULT_STATISTICS)
    try:
        assert session.edit == mode
    finally:
        session.provider.close()


@pytest.mark.ac("gh-302:AC-9")
def test_default_edit_falls_back_when_settle_drops_new_cli(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_target: Maker, write_draft: Drafter,
        capsys: pytest.CaptureFixture[str]) -> None:
    world = layered_world(tmp_path, monkeypatch, make_target, "cyclopts")
    offered = plan_targets(world.skill, world.files(), load_contract(world.skill), "prose+cli")
    assert offered.capable is not None and offered.editable == ()
    monkeypatch.setattr(_run, "settle", _drop_new_cli)
    record, notes = _prepare(world.skill, tmp_path / "run", write_draft, capsys)
    assert record["edit"] == "prose"
    assert notes == 1
    editable = cast(list[str], record["editable"])
    assert NEW_CLI not in editable and not any(name.startswith(WEDGE_PREFIX) for name in editable)
    assert "new_cli" not in cast(dict[str, object], record["own_targets"])
    explicit, _ = _prepare(world.skill, tmp_path / "prose-run", write_draft, capsys, edit="prose")
    assert (record["seed_hash"], record["seed"]) == (explicit["seed_hash"], explicit["seed"])

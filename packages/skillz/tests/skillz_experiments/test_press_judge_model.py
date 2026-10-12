"""Adversarial attacks on `run --judge-model`: export, tampered records, an empty value, and role options."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._cli import main
from skillz_experiments._records import read, write
from skillz_experiments.harness import ClaudeOptions
from skillz_experiments.harness._harness import Configuration
from skillz_experiments.run import export

if TYPE_CHECKING:
    from .conftest import JudgedRun

TASK, JUDGE = "task-model", "judge-model"


def test_export_accepts_an_honest_split_run_and_refuses_one_whose_judge_model_changed(
        tmp_path: Path, judged: JudgedRun) -> None:
    _ = judged.start(judge_model=JUDGE)
    record = read(judged.out / "run.json")
    assert record["judge_model"] == JUDGE
    assert export(judged.out, tmp_path / "honest")["installed"] is False
    record["judge_model"] = "swapped-judge"
    write(judged.out / "run.json", record)
    with pytest.raises(ValueError, match="judge differs"):
        _ = export(judged.out, tmp_path / "tampered")


@pytest.mark.parametrize("tampered", [5, [JUDGE], True])
def test_a_tampered_judge_model_flag_stops_a_resume_that_omits_the_flag(judged: JudgedRun, tampered: object) -> None:
    with pytest.raises(CodedError) as stopped:
        _ = judged.start(live=False, judge_model=JUDGE)
    assert stopped.value.code == "live-required"
    record = read(judged.out / "run.json")
    record["judge_model_flag"] = tampered
    write(judged.out / "run.json", record)
    with pytest.raises(ValueError, match="judge_model_flag"):
        _ = judged.start()


@pytest.mark.parametrize("given", ["", "   "])
def test_an_empty_judge_model_never_reaches_a_run_record(judged: JudgedRun, given: str) -> None:
    with pytest.raises(ValueError, match="must name a model"):
        _ = judged.start(judge_model=given)
    assert not (judged.out / "run.json").exists()


def test_an_empty_judge_model_on_the_command_line_exits_nonzero(
        judged: JudgedRun, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["run", "--target", str(judged.target), "--out", str(judged.out), "--model", TASK,
            "--approve-cases", judged.case_hash, "--approve-budget", str(judged.calls), "--live", "--judge-model", ""]
    assert main(argv) != 0
    assert "must name a model" in capsys.readouterr().err
    assert not (judged.out / "run.json").exists()


def test_the_judge_role_keeps_the_claude_options_of_the_other_roles() -> None:
    options = ClaudeOptions(effort="high")
    roles = Configuration.split("claude", TASK, JUDGE, options).roles
    assert {role.options.effort for role in roles.values()} == {"high"}
    assert {role.command for role in roles.values()} == {roles["task"].command}
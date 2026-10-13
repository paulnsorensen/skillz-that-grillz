"""The run compatibility fingerprint: what a run freezes, how `compare` reads it, and which edits move `scoring_digest`."""
from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

import skillz_experiments
from skillz_experiments._cases import digest
from skillz_experiments._intake import DRAFT_NAME
from skillz_experiments._records import read
from skillz_experiments.harness._harness import Configuration
from skillz_experiments.run import compare, export
from skillz_experiments.run._session import SCORING_FILES, leakage_digest, scoring_digest

if TYPE_CHECKING:
    from .conftest import JudgedRun

PACKAGE = Path(skillz_experiments.__file__).parent
FIELDS = {"contract_hash", "skill", "adapter", "task_model", "judge_model", "claude_options", "edit",
          "repeats", "statistics", "split_digest", "scoring_digest", "leakage_digest"}


def _judged_run(base: Path, judge_model: str, make_target: Callable[..., Path],
                approved_run: Callable[..., dict[str, object]], *, task_model: str | None = None,
                expected: dict[str, object] | None = None) -> tuple[Path, dict[str, object], dict[str, object]]:
    """Run once under the command harness with a judged contract. Return the run directory, `run.json`, and the summary.

    `task_model` sets the task role's model, which overrides the `--model` flag. `expected` sets every drafted case's expected value.
    """
    base.mkdir()
    wrapper, settings, config = base / "wrapper.py", base / "settings.json", base / "harness.json"
    _ = shutil.copyfile(Path(__file__).parent / "fixtures/command_wrapper.py", wrapper)
    _ = settings.write_text(json.dumps({"mode": "normal", "log": str(base / "requests.jsonl")}))
    roles: dict[str, object] = {"judge": {"model": judge_model}}
    if task_model is not None:
        roles["task"] = {"model": task_model}
    _ = config.write_text(json.dumps({
        "schema_version": 1, "adapter": "command", "identity": "protocol-fixture",
        "command": [sys.executable, str(wrapper), str(settings)], "roles": roles}))
    target, out = make_target(base), base / "run"
    (target / "evals/autoimprove.json").unlink()
    if expected is not None:
        out.mkdir(mode=0o700)
        cases = [{"id": f"t{family}-{index}", "family": f"f{family}", "kind": "task", "request": f"request-t{family}-{index}-end",
                  "files": {"fixture.md": "hello\n"}, "expected": expected, "source": "skill"}
                 for family in range(5) for index in range(2)]
        _ = (out / DRAFT_NAME).write_text(json.dumps(cases), encoding="utf-8")
    result = approved_run(target, out, model="task-model", configuration=Configuration.load(config, "task-model"))
    return out, read(out / "run.json"), result


def _frozen(record: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], record["compatibility"])


def _fields(record: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], _frozen(record)["fields"])


@pytest.mark.ac("gh-235:AC-1")
@pytest.mark.ac("gh-235:AC-2")
def test_a_run_freezes_its_fingerprint_and_an_equal_run_compares_clean(
        tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    out, first, summary = _judged_run(tmp_path / "a", "judge-a", make_target, approved_run)
    _, second, _ = _judged_run(tmp_path / "b", "judge-a", make_target, approved_run)
    saved = cast(list[dict[str, object]], json.loads((out / "cases.json").read_text())["cases"])
    fields = _fields(first)
    assert set(fields) == FIELDS and len(fields) == 12
    assert _frozen(first)["fingerprint"] == digest(fields)
    assert fields["task_model"] == "task-model" and fields["judge_model"] == "judge-a"
    assert fields["adapter"] == "command"
    assert fields["edit"] == "prose" and fields["repeats"] == 3 and fields["statistics"] == first["statistics"]
    assert fields["scoring_digest"] == scoring_digest(PACKAGE)
    assert fields["leakage_digest"] == leakage_digest(PACKAGE)
    assert fields["split_digest"] == digest({
        split: sorted(str(case["id"]) for case in saved if case["split"] == split) for split in ("train", "validation")})
    assert fields["skill"] == cast(dict[str, object], first["contract"])["skill"]
    assert _frozen(summary)["fingerprint"] == _frozen(first)["fingerprint"]
    assert _frozen(second)["fingerprint"] == _frozen(first)["fingerprint"]
    assert compare(first, second) == ()


@pytest.mark.ac("gh-235:AC-3")
def test_runs_that_differ_only_in_the_judge_model_name_that_field(
        tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    _, first, _ = _judged_run(tmp_path / "a", "judge-a", make_target, approved_run)
    _, second, _ = _judged_run(tmp_path / "b", "judge-b", make_target, approved_run)
    assert compare(first, second) == ("judge_model",)
    assert _frozen(first)["fingerprint"] != _frozen(second)["fingerprint"]


@pytest.mark.ac("gh-235:AC-5")
def test_runs_that_differ_only_in_the_judge_model_flag_compare_on_judge_model(judged: JudgedRun, tmp_path: Path) -> None:
    twin = tmp_path / "twin"
    _ = shutil.copytree(judged.out, twin)
    _ = judged.start(judge_model="judge-a")
    _ = judged._replace(out=twin).start(judge_model="judge-b")
    first, second = read(judged.out / "run.json"), read(twin / "run.json")
    assert _fields(first)["judge_model"] == "judge-a" and _fields(second)["judge_model"] == "judge-b"
    assert compare(first, second) == ("judge_model",)


@pytest.mark.ac("gh-235:AC-3")
def test_runs_that_differ_only_in_the_task_role_model_name_that_field(
        tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    _, plain, _ = _judged_run(tmp_path / "a", "judge-a", make_target, approved_run)
    _, overridden, _ = _judged_run(tmp_path / "b", "judge-a", make_target, approved_run, task_model="task-role-model")
    assert _fields(overridden)["task_model"] == "task-role-model"
    assert compare(plain, overridden) == ("task_model",)


@pytest.mark.ac("gh-235:AC-4")
@pytest.mark.parametrize("record", [{}, {"compatibility": None}, {"compatibility": "x"}, {"compatibility": {"fields": []}},
                                    {"compatibility": {"fields": {}}}])
def test_a_record_without_a_fingerprint_is_missing_on_either_side(record: dict[str, object]) -> None:
    present: dict[str, object] = {"compatibility": {"fingerprint": "f", "fields": {"edit": "prose"}}}
    assert compare(record, present) == ("missing-fingerprint",)
    assert compare(present, record) == ("missing-fingerprint",)
    assert compare(present, present) == ()


@pytest.mark.ac("gh-235:AC-3")
def test_compare_tells_an_absent_field_from_a_null_field() -> None:
    absent: dict[str, object] = {"compatibility": {"fields": {"edit": "prose"}}}
    null: dict[str, object] = {"compatibility": {"fields": {"edit": "prose", "judge_model": None}}}
    assert compare(absent, null) == ("judge_model",)
    assert compare(null, absent) == ("judge_model",)
    assert compare(null, null) == ()


@pytest.mark.ac("gh-235:AC-3")
def test_compare_names_every_differing_field_including_one_present_on_one_side() -> None:
    first: dict[str, object] = {"compatibility": {"fields": {"edit": "prose", "repeats": 3, "skill": "a"}}}
    second: dict[str, object] = {"compatibility": {"fields": {"edit": "prose+cli", "repeats": 3, "extra": 1}}}
    assert compare(first, second) == ("edit", "extra", "skill")


@pytest.mark.ac("gh-235:AC-5")
def test_an_export_carries_the_fingerprint_and_no_case_text(
        tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    out, record, _ = _judged_run(tmp_path / "a", "judge-a", make_target, approved_run, expected={"marker": "expected-sentinel"})
    destination = Path(cast(str, export(out, tmp_path / "export")["export"]))
    text = (destination / "report.json").read_text()
    report = cast(dict[str, object], json.loads(text))
    assert _frozen(report)["fingerprint"] == _frozen(record)["fingerprint"]
    assert "expected-sentinel" in (out / "cases.json").read_text()
    assert "request-" not in text and "expected-sentinel" not in text
    assert "expected-sentinel" not in json.dumps(_frozen(record))


@pytest.mark.ac("gh-235:AC-6")
def test_the_scoring_files_are_pinned_and_exist() -> None:
    assert SCORING_FILES == ("evaluation/_evaluator.py", "_evaluation.py", "_graders.py", "_audit.py", "_contract.py")
    assert [name for name in SCORING_FILES if not (PACKAGE / name).is_file()] == []


@pytest.fixture
def copied(tmp_path: Path) -> Path:
    copy = tmp_path / "skillz_experiments"
    _ = shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    return copy


@pytest.mark.ac("gh-235:AC-6")
@pytest.mark.parametrize("module", ["gate/_rules.py", "gate/_statistics.py", "evaluation/_evaluator.py", "_evaluation.py", "_graders.py",
                                    "_audit.py", "_contract.py"])
def test_a_scoring_module_edit_changes_the_scoring_digest(copied: Path, module: str) -> None:
    before = scoring_digest(copied)
    assert before == scoring_digest(PACKAGE)
    path = copied / module
    _ = path.write_text(path.read_text() + "\n# changed\n")
    assert scoring_digest(copied) != before


@pytest.mark.ac("gh-235:AC-6")
def test_a_file_added_to_gate_changes_the_scoring_digest(copied: Path) -> None:
    before = scoring_digest(copied)
    _ = (copied / "gate" / "_added.py").write_text("X = 1\n")
    assert scoring_digest(copied) != before


@pytest.mark.ac("gh-235:AC-6")
@pytest.mark.parametrize("module", ["search/_engine.py", "run/_session.py", "harness/_claude/_events.py", "_cli.py", "__init__.py"])
def test_an_edit_outside_the_scoring_modules_keeps_the_scoring_digest(copied: Path, module: str) -> None:
    before = scoring_digest(copied)
    path = copied / module
    _ = path.write_text(path.read_text() + "\n# changed\n")
    assert scoring_digest(copied) == before


@pytest.mark.parametrize("module", ["search/_leakage.py", "search/_engine.py", "run/_session.py"])
def test_only_an_edit_to_the_leakage_check_changes_the_leakage_digest(copied: Path, module: str) -> None:
    before = leakage_digest(copied)
    assert before == leakage_digest(PACKAGE)
    path = copied / module
    _ = path.write_text(path.read_text() + "\n# changed\n")
    assert (leakage_digest(copied) != before) == (module == "search/_leakage.py")

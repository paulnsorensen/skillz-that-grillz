from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._cli import main
from skillz_experiments._intake import case_hash, load_draft, split_cases, too_few
from skillz_experiments.run import Stop, run

Maker = Callable[..., Path]
REPO = Path(__file__).resolve().parents[4]
SEED = 20261006


def _entries(target: Path, sizes: dict[str, int]) -> None:
    entries: list[dict[str, object]] = [{"name": f"{family}-{index}", "prompt": f"prompt-{family}-{index}", "expected_output": "out", "files": [],
                "family": family} for family, count in sizes.items() for index in range(count)]
    _ = (target / "evals/evals.json").write_text(json.dumps({"skill_name": "echo-skill", "evals": entries}))


def _report(capsys: pytest.CaptureFixture[str], *arguments: str) -> tuple[int, dict[str, object]]:
    code = main(["validate", *arguments])
    return code, cast(dict[str, object], json.loads(capsys.readouterr().out))


@pytest.mark.ac("gh-370:AC-1")
def test_validate_accepts_the_shipped_skillz_skill_with_a_full_holdout(capsys: pytest.CaptureFixture[str]) -> None:
    code, report = _report(capsys, "--target", str(REPO / "skills/skillz"))
    split = cast(dict[str, int], report["split"])
    assert code == 0 and report["ok"] is True and report["findings"] == []
    assert split["holdout"] >= 6 and split["seed"] == SEED


@pytest.mark.ac("gh-300:AC-4")
def test_validate_gives_the_shipped_skillz_task_split_a_full_train_and_holdout(
        capsys: pytest.CaptureFixture[str]) -> None:
    code, report = _report(capsys, "--target", str(REPO / "skills/skillz"))
    split, kinds = cast(dict[str, int], report["split"]), cast(dict[str, dict[str, int]], report["cases"])["kinds"]
    assert code == 0 and report["ok"] is True
    # The task kind drives the split: the three splits hold every task case.
    assert split["train"] + split["validation"] + split["holdout"] == kinds["task"]
    assert split["holdout"] >= 6 and split["train"] >= 4 and split["train_minimum"] == 4


@pytest.mark.ac("gh-370:AC-2")
def test_validate_reports_two_task_families_as_cases_too_few(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    _entries(target, {"a": 2, "b": 2})
    code, report = _report(capsys, "--target", str(target))
    findings = cast(list[dict[str, object]], report["findings"])
    found = [item for item in findings if item["code"] == "cases-too-few"]
    assert code == 1 and report["ok"] is False and found
    assert cast(dict[str, object], found[0]["data"])["families"] == {"a": 2, "b": 2}
    assert found[0]["severity"] == "error" and found[0]["fix"]


@pytest.mark.ac("gh-300:AC-2")
def test_validate_reports_a_split_that_misses_the_holdout_minimum_once(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    """Five families pass the family count, so the split runs and the post-split check reports the shortfall."""
    target = make_target(tmp_path)
    _entries(target, {family: 2 for family in "abcde"})
    code, report = _report(capsys, "--target", str(target))
    findings = cast(list[dict[str, object]], report["findings"])
    found = [item for item in findings if item["code"] == "cases-too-few"]
    assert code == 1 and report["ok"] is False and len(found) == 1, findings
    data = cast(dict[str, object], found[0]["data"])
    assert data["families"] == {family: 2 for family in "abcde"} and data["deficit"] == 2, data
    assert str(found[0]["message"]).startswith("the holdout can hold 4 scored cases; the minimum is 6"), found[0]


@pytest.mark.ac("gh-370:AC-3")
def test_run_with_a_two_family_draft_prints_the_cases_too_few_code(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    out.mkdir(mode=0o700)
    cases = [{"id": f"{family}-{index}", "family": family, "kind": "task", "request": f"request-{family}-{index}-end",
              "files": {"fixture.md": "hello\n"}, "expected": {}, "source": "skill"}
             for family in "ab" for index in range(3)]
    _ = (out / "cases.draft.json").write_text(json.dumps(cases))
    assert main(["run", "--target", str(target), "--out", str(out), "--model", "offline", "--edit", "prose"]) == 1
    error = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert error["code"] == "cases-too-few"
    assert cast(dict[str, object], error["data"])["families"] == {"a": 3, "b": 3}


@pytest.mark.ac("gh-370:AC-4")
def test_the_holdout_shortfall_names_the_smallest_families_the_capacity_the_deficit_and_a_fix() -> None:
    # gh-300: train now takes b:2 and c:2 before the holdout fills, so the shortfall needs a fifth family.
    error = too_few({"a": 1, "b": 2, "c": 2, "d": 2, "e": 2})
    assert isinstance(error, CodedError) and error.code == "cases-too-few"
    assert "smallest family (a:1)" in str(error) and "train takes b:2, c:2" in str(error)
    assert "rest hold 4" in str(error) and "deficit is 2" in str(error)
    assert "add 2 scored cases to families other than a and b and c, or one new family of at least 2" in str(error)
    assert error.details is not None
    details = dict(error.details)
    fix = cast(str, details.pop("fix"))
    assert details == {"families": {"a": 1, "b": 2, "c": 2, "d": 2, "e": 2}, "families_minimum": 4,
                       "holdout_minimum": 6, "train_minimum": 4, "train_families_minimum": 2,
                       "holdout_capacity": 4, "deficit": 2,
                       "shortfall": {"train": {"cases": 0, "families": 0}, "validation": {"families": 0},
                                     "holdout": {"cases": 2}}}
    assert fix in str(error)
    assert too_few({"a": 1, "b": 2, "c": 3, "d": 3, "e": 2}) is None


@pytest.mark.ac("gh-300:AC-2")
def test_an_empty_holdout_asks_only_for_a_new_family() -> None:
    # Train takes b, c, and d, so no family is left to grow for the holdout.
    error = too_few({"a": 1, "b": 1, "c": 1, "d": 2})
    assert isinstance(error, CodedError) and error.details is not None
    assert error.details["fix"] == "add one new family of at least 6 scored cases"
    assert "train takes b:1, c:1, d:2" in str(error) and "families other than" not in str(error)


def test_the_projection_leaves_the_case_hash_of_an_existing_draft_unchanged(tmp_path: Path) -> None:
    # gh-300: five families of 2 cannot give train 4 cases, so the draft has six.
    # The seeded split meets every minimum here, so the fallback does not run and the hash stays the same.
    cases = [{"id": f"t{family}-{index}", "family": f"f{family}", "kind": "task", "request": f"Do thing {family}.{index}",
              "files": {"input.txt": "hello\n"}, "expected": {"answer": f"{family}"}, "source": "skill"}
             for family in range(6) for index in range(2)]
    path = tmp_path / "cases.draft.json"
    _ = path.write_text(json.dumps(cases))
    assert case_hash(split_cases(load_draft(path), SEED), SEED) == (
        "86e91732a1dde5a2f25818953c4225416a1adca704b4fefb7629eb8b0ab11e80")


@pytest.mark.ac("gh-370:AC-9")
def test_the_cases_missing_stop_carries_a_split_projection(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _entries(target, {"a": 1, "b": 2, "c": 4})
    with pytest.raises(Stop) as caught:
        _ = run(target, tmp_path / "run", "local-test", live=True)
    summary = cast(dict[str, object], caught.value.data["seed_summary"])
    assert summary["split"] == {"families": {"a": 1, "b": 2, "c": 4}, "families_minimum": 4, "holdout_minimum": 6,
                                "train_minimum": 4, "train_families_minimum": 2, "holdout_capacity": 0, "deficit": 6,
                                "shortfall": {"train": {"cases": 0, "families": 0}, "validation": {"families": 0},
                                              "holdout": {"cases": 6}}}


@pytest.mark.ac("gh-370:AC-9")
def test_validate_and_run_project_the_same_split_for_task_entries_without_a_family(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    _entries(target, {"a": 1, "b": 2, "c": 4})
    document = cast(dict[str, list[dict[str, object]]], json.loads((target / "evals/evals.json").read_text()))
    loose: list[dict[str, object]] = [
        {"name": f"loose-{index}", "prompt": f"loose prompt {index}", "expected_output": "out", "files": []}
        for index in range(3)]
    document["evals"] += loose
    _ = (target / "evals/evals.json").write_text(json.dumps(document))
    with pytest.raises(Stop) as caught:
        _ = run(target, tmp_path / "run", "local-test", live=True)
    projected = cast(dict[str, object], cast(dict[str, object], caught.value.data["seed_summary"])["split"])
    code, report = _report(capsys, "--target", str(target))
    findings = cast(list[dict[str, object]], report["findings"])
    short = [item for item in findings if item["code"] == "cases-too-few"]
    missing = [item for item in findings if item["code"] == "case-family-missing"]
    assert code == 1 and len(missing) == 3 and len(short) == 1
    assert cast(dict[str, object], report["cases"])["families"] == projected["families"] == {"a": 1, "b": 2, "c": 4}
    data = cast(dict[str, object], short[0]["data"])
    assert {key: data[key] for key in projected} == projected


@pytest.mark.ac("gh-370:AC-10")
def test_validate_runs_offline_with_an_empty_home_and_no_agent_cli(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    home, empty = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    empty.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(empty))
    code, report = _report(capsys, "--target", str(REPO / "skills/skillz"))
    assert code == 0 and report["ok"] is True
    assert list(home.iterdir()) == []

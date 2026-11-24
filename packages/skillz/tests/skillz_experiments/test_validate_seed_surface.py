"""CLI diagnostics for serialized seed capacity."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cli import main
from skillz_experiments._seed import READ_LIMIT, SEED_LIMIT
from skillz_experiments.run import Stop, run

Maker = Callable[..., Path]


def _report(capsys: pytest.CaptureFixture[str], command: str, target: Path) -> tuple[int, dict[str, object]]:
    code = main([command, "--target", str(target), "--edit", "prose"])
    return code, cast(dict[str, object], json.loads(capsys.readouterr().out))


def _tasks(target: Path, prompt: str = "Read evals/files/input.md") -> None:
    (target / "evals/files").mkdir()
    _ = (target / "evals/files/input.md").write_text("input\n", encoding="utf-8")
    entries = [{"name": f"e{i}", "family": f"f{i // 3}", "prompt": prompt, "expected_output": "ok",
                "assertions": ["answer"], "files": ["evals/files/input.md"]} for i in range(15)]
    _ = (target / "evals/evals.json").write_text(json.dumps({"evals": entries}), encoding="utf-8")


@pytest.mark.usefixtures("host_login")
@pytest.mark.ac("gh-745:AC-1")
def test_validate_and_plan_report_serialized_seed_capacity(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    _tasks(target)
    for command in ("validate", "plan"):
        code, report = _report(capsys, command, target)
        seed = cast(dict[str, int], report["seed"] if command == "validate" else cast(dict[str, object], report["preview"])["seed"])
        assert code == 0
        assert seed["bytes"] + seed["remaining"] == seed["limit"] == SEED_LIMIT
        assert not [item for item in cast(list[dict[str, object]], report["findings"]) if item["code"] == "seed-capacity-warning"]
    with pytest.raises(Stop) as caught:
        _ = run(target, tmp_path / "run", "local-test", live=True)
    assert caught.value.code == "cases-missing"
    assert Path(cast(str, caught.value.data["seed_file"])).stat().st_size == seed["bytes"]


@pytest.mark.ac("gh-745:AC-2")
def test_seed_capacity_warns_only_below_ten_percent(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    _tasks(target)
    _, report = _report(capsys, "validate", target)
    start = cast(dict[str, int], report["seed"])["bytes"]
    fixture = target / "evals/files/input.md"
    length = len(json.dumps("input\n")) - 2 + SEED_LIMIT - SEED_LIMIT // 10 - start
    _ = fixture.write_text("x" * length, encoding="utf-8")
    for extra in (0, 1):
        if extra:
            _ = fixture.write_text("x" * (length + 1), encoding="utf-8")
        code, report = _report(capsys, "validate", target)
        assert code == 0
        assert cast(dict[str, int], report["seed"])["remaining"] == SEED_LIMIT // 10 - extra
        warnings = [item for item in cast(list[dict[str, object]], report["findings"])
                    if item["code"] == "seed-capacity-warning"]
        assert len(warnings) == extra


@pytest.mark.ac("gh-745:AC-3")
def test_serialized_overflow_reports_exact_bytes_and_largest_fixture_without_contents(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    _tasks(target)
    _, report = _report(capsys, "validate", target)
    start = cast(dict[str, int], report["seed"])["bytes"]
    length = len(json.dumps("input\n")) - 2 + SEED_LIMIT + 1 - start
    _ = (target / "evals/files/input.md").write_text("x" * length, encoding="utf-8")
    code, report = _report(capsys, "validate", target)
    errors = [item for item in cast(list[dict[str, object]], report["findings"]) if item["code"] == "evals-unreadable"]
    assert code == 1 and len(errors) == 1
    message = str(errors[0]["message"])
    assert f"{SEED_LIMIT + 1} bytes" in message and f"limit {SEED_LIMIT} bytes" in message
    assert "overage 1 bytes" in message and f"evals/files/input.md ({length} bytes)" in message
    assert "x" * 20 not in message


def test_overflow_names_largest_inline_journey_fixture(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    _tasks(target)
    task_size = 500_000
    journey_size = 600_000
    _ = (target / "evals/files/input.md").write_text("t" * task_size, encoding="utf-8")
    journey = {"id": "j1", "family": "fj", "kind": "journey", "request": "Discuss the input.",
               "brief": "Lead the conversation.", "max_turns": 3,
               "files": {"journey.txt": "j" * journey_size},
               "expected": {"must": ["a plan"]}, "source": "skill"}
    _ = (target / "evals/journeys.json").write_text(json.dumps([journey]), encoding="utf-8")
    code, report = _report(capsys, "validate", target)
    errors = [item for item in cast(list[dict[str, object]], report["findings"]) if item["code"] == "evals-unreadable"]
    assert code == 1 and len(errors) == 1
    message = str(errors[0]["message"])
    assert "serialized seed is" in message
    assert f"journey.txt ({journey_size} bytes)" in message
    assert f"evals/files/input.md ({task_size} bytes)" in message
    assert "j" * 20 not in message


@pytest.mark.ac("gh-745:AC-4")
def test_early_distinct_fixture_bound_reports_only_a_lower_bound_and_five_largest(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    _tasks(target)
    paths = [f"evals/files/f{i}.md" for i in range(6)]
    for path in paths:
        _ = (target / path).write_text("x" * 200_000, encoding="utf-8")
    document = cast(dict[str, list[dict[str, object]]], json.loads((target / "evals/evals.json").read_text(encoding="utf-8")))
    document["evals"][0]["files"] = paths
    _ = (target / "evals/evals.json").write_text(json.dumps(document), encoding="utf-8")
    code, report = _report(capsys, "validate", target)
    errors = [item for item in cast(list[dict[str, object]], report["findings"]) if item["code"] == "evals-unreadable"]
    assert code == 1 and len(errors) == 1
    message = str(errors[0]["message"])
    assert "at least" in message and "before serialized seed sizing" in message
    assert "serialized seed is" not in message
    assert sum(path in message for path in paths) == 5
    assert paths[-1] not in message


@pytest.mark.ac("gh-745:AC-3")
def test_one_fixture_over_the_read_limit_reports_its_size_limit_and_overage(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    _tasks(target)
    _ = (target / "evals/files/input.md").write_text("x" * (READ_LIMIT + 1), encoding="utf-8")
    code, report = _report(capsys, "validate", target)
    errors = [item for item in cast(list[dict[str, object]], report["findings"]) if item["code"] == "evals-unreadable"]
    assert code == 1 and len(errors) == 1
    message = str(errors[0]["message"])
    assert (f"evals/files/input.md is too large: {READ_LIMIT + 1} bytes, limit {READ_LIMIT} bytes, "
            + "overage 1 bytes") in message
    assert "x" * 20 not in message


@pytest.mark.ac("gh-745:AC-4")
def test_early_distinct_fixture_bound_lists_a_larger_inline_journey_fixture(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    _tasks(target)
    paths = [f"evals/files/f{i}.md" for i in range(6)]
    for path in paths:
        _ = (target / path).write_text("x" * 200_000, encoding="utf-8")
    document = cast(dict[str, list[dict[str, object]]], json.loads((target / "evals/evals.json").read_text(encoding="utf-8")))
    document["evals"][0]["files"] = paths
    _ = (target / "evals/evals.json").write_text(json.dumps(document), encoding="utf-8")
    journey = {"id": "j1", "family": "fj", "kind": "journey", "request": "Discuss the input.",
               "brief": "Lead the conversation.", "max_turns": 3,
               "files": {"journey.txt": "j" * 600_000},
               "expected": {"must": ["a plan"]}, "source": "skill"}
    _ = (target / "evals/journeys.json").write_text(json.dumps([journey]), encoding="utf-8")
    code, report = _report(capsys, "validate", target)
    errors = [item for item in cast(list[dict[str, object]], report["findings"]) if item["code"] == "evals-unreadable"]
    assert code == 1 and len(errors) == 1
    message = str(errors[0]["message"])
    assert "at least" in message and "serialized seed is" not in message
    assert "journey.txt (600000 bytes)" in message
    assert sum(path in message for path in paths) == 4
    assert "j" * 20 not in message

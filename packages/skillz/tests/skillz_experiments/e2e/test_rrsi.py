"""Process evidence for the implemented search and leakage controls."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import cast

import pytest

from support import BUNDLE, CLI, call_cli, carries_answer, command_adapter, make_scenario, read_cases, read_events, read_record, run_scenario


@pytest.mark.ac("skillz-experiments-end-to-end:AC-2")
@pytest.mark.parametrize("cli", [CLI, BUNDLE], ids=["source", "bundle"])
def test_search_validates_the_winner_and_reuses_a_completed_run(tmp_path: Path, cli: Path) -> None:
    scenario = make_scenario(tmp_path)
    result = run_scenario(scenario, tmp_path, cli=cli)
    record = read_record(scenario.out)
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    validation = [item for item in outcomes if item.get("arm") == "search"
                  and item.get("split") == "validation"]
    best = cast(dict[str, object], record["best_validated"])
    winner = cast(str, record["winner_hash"])
    assert winner == best["identity"] and winner != record["seed_hash"]
    validation_ids = {cast(str, item["case_id"]) for item in validation}
    assert len(validation_ids) == 2
    assert {item["case_id"] for item in validation if item["search_candidate"] == winner} == validation_ids
    assert {item["score"] for item in validation if item["search_candidate"] == winner} == {1.0}

    held = [item for item in outcomes if item.get("split") == "holdout" and item["arm"] != "replay"]
    held_ids = {cast(str, item["case_id"]) for item in held}
    assert len(held_ids) == 6
    assert {(item["arm"], item["case_id"], item["repeat"]) for item in held} == {
        (arm, case_id, repeat) for arm in ("baseline", "winner")
        for case_id in held_ids for repeat in range(3)
    }
    assert {item["score"] for item in held if item["arm"] == "baseline"} == {0.0}
    assert {item["score"] for item in held if item["arm"] == "winner"} == {1.0}
    gate = cast(dict[str, object], result["gate"])
    assert (gate["verdict"], gate["delta"], gate["se"], gate["cases"], gate["repeats"]) == (
        "promote", 1.0, 0.0, 6, 3
    )

    events = read_events(scenario.out)
    reflections = [cast(dict[str, object], item["request"]) for item in events
                   if item["role"] == "reflection"]
    assert reflections
    first_prompt = cast(str, reflections[0]["prompt"])
    feedback = cast(dict[str, object], json.loads(first_prompt.split("\n", 1)[1]))
    assert feedback["candidate"] == {"SKILL.md": scenario.original}
    shown = cast(list[dict[str, object]], cast(dict[str, object], feedback["feedback"])["SKILL.md"])
    assert set(cast(dict[str, object], feedback["feedback"])) == {"SKILL.md"}
    train = {cast(str, case["id"]) for case in read_cases(scenario.out) if case["split"] == "train"}
    assert len(shown) == 2 and len({item["case_id"] for item in shown}) == 2 and {item["case_id"] for item in shown} <= train
    assert [{key: value for key, value in item.items() if key != "case_id"} for item in shown] == [
        {"task_correct": 0.0, "request": "Transform fixture.md according to this skill.",
         "grader_scores": {"command": 0.0}}
    ] * 2
    estimate = cast(dict[str, object], record["estimate"])
    headroom = cast(dict[str, int], estimate["headroom"])
    planned = cast(int, estimate["metric_calls"]) + headroom["outcomes"] - (4 + len(validation_ids))
    first_reflection = next(index for index, item in enumerate(outcomes)
                            if item.get("split") == "reflection")
    spent = sum(1 for item in outcomes[:first_reflection]
                if item.get("arm") == "search" and item.get("split") in ("train", "validation"))
    percent = 100 * spent // planned
    edit_budget = math.floor(1 + 2.5 * (1 + math.cos(math.pi * spent / planned)))
    assert (headroom["outcomes"], spent, planned, percent, edit_budget) == (2, 4, 12, 33, 4)
    assert f"Make at most {edit_budget} edits across all components." in first_prompt
    assert f"Run status: this is proposal 1. Search calls spent: {spent} of {planned} ({percent}%)." in first_prompt
    assert edit_budget < 6
    assert cast(dict[str, object], result["search"])["proposal_over_budget"] == 0
    for case in scenario.draft:
        if case["id"] in held_ids:
            expected = cast(str, cast(dict[str, object], case["expected"])["text"])
            assert not any(carries_answer(expected, cast(str, item["prompt"])) for item in reflections)

    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out),
            "--model", "offline-test", "--harness", "command", "--live", *command_adapter(scenario)]
    code, repeated, error = call_cli(args, tmp_path, cli=cli)
    assert (code, error, repeated["gate"]) == (0, "", result["gate"])
    assert read_events(scenario.out) == events

    code, _, error = call_cli([*args, "--min-gain", "0.2"], tmp_path, cli=cli)
    assert code == 1
    failure = cast(dict[str, object], json.loads(error))
    assert failure["code"] == "run-config-differs"
    assert "min-gain differs from the frozen record" in cast(str, failure["error"])
    assert read_events(scenario.out) == events


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
def test_leak_predicate_trips_on_the_json_escaped_answer() -> None:
    escaped = json.dumps({"prompt": "context\nanswer-text\nmore"})
    assert carries_answer("answer-text\n", escaped)
    assert carries_answer("answer-text\n", "answer-text")
    assert not carries_answer("answer-text\n", json.dumps({"prompt": "clean"}))


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
def test_leaky_proposal_is_rejected_before_task_calls_and_redacted(tmp_path: Path) -> None:
    scenario = make_scenario(tmp_path)
    source = "leak_answer_private_12345\n"
    for case in scenario.draft:
        case["files"] = {"fixture.md": source}
        case["expected"] = {"text": source.upper()}
    result = run_scenario(scenario, tmp_path, mode="leak-answer")
    search = cast(dict[str, object], result["search"])
    gate = cast(dict[str, object], result["gate"])
    assert search["leakage_rejections"] == {"expected-answer": 1}
    assert search["retained_seed"] is True
    assert (gate["verdict"], gate["reasons"]) == ("inconclusive", ["winner-equals-baseline"])

    record = read_record(scenario.out)
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    assert not any(item.get("split") == "holdout" for item in outcomes)
    events = read_events(scenario.out)
    tasks = [item for item in events if item["role"] == "task"]
    assert tasks
    assert {item["candidate"] for item in tasks} == {record["seed_hash"]}
    assert any(item["role"] == "reflection" for item in events)

    code, exported, error = call_cli(
        ["export", str(scenario.out), "--out", str(scenario.export)], tmp_path
    )
    assert (code, error, exported["installed"]) == (0, "", False)
    report = cast(dict[str, object], json.loads((scenario.export / "report.json").read_text()))
    assert cast(dict[str, object], report["search"])["leakage_rejections"] == {"expected-answer": 1}
    assert not carries_answer(source.upper(), (scenario.out / "run.json").read_text())
    assert not carries_answer(source.upper(), (scenario.export / "report.json").read_text())


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
def test_invalid_helper_proposal_keeps_the_seed_without_task_inference(tmp_path: Path) -> None:
    scenario = make_scenario(tmp_path, helper=True)
    result = run_scenario(scenario, tmp_path, helper=True, mode="bad-helper")
    search = cast(dict[str, object], result["search"])
    gate = cast(dict[str, object], result["gate"])
    assert search["retained_seed"] is True
    assert (gate["verdict"], gate["reasons"]) == ("inconclusive", ["winner-equals-baseline"])

    record = read_record(scenario.out)
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    assert not any(item.get("split") == "holdout" for item in outcomes)
    tasks = [item for item in read_events(scenario.out) if item["role"] == "task"]
    assert tasks
    assert {item["candidate"] for item in tasks} == {record["seed_hash"]}


@pytest.mark.ac("skillz-experiments-end-to-end:AC-2")
def test_edit_budget_is_advisory_and_counts_oversized_proposals(tmp_path: Path) -> None:
    scenario = make_scenario(tmp_path)
    original = (scenario.original + "".join(
        f"Keep step {index} visible.\nCopy fixture.md unchanged.\n" for index in range(1, 6)
    ))
    _ = (scenario.target / "SKILL.md").write_text(original)
    result = run_scenario(scenario, tmp_path)
    search = cast(dict[str, object], result["search"])
    gate = cast(dict[str, object], result["gate"])
    assert search["proposal_over_budget"] == 1
    assert search["retained_seed"] is False
    assert (gate["verdict"], gate["delta"], gate["se"]) == ("promote", 1.0, 0.0)

    record = read_record(scenario.out)
    reflections = [item for item in cast(list[dict[str, object]], record["outcomes"])
                   if item.get("split") == "reflection"]
    assert [item.get("over_budget") for item in reflections] == [True]
    events = read_events(scenario.out)
    requests = [cast(dict[str, object], item["request"]) for item in events
                if item["role"] == "reflection"]
    assert len(requests) == 1
    assert "Make at most 4 edits across all components." in cast(str, requests[0]["prompt"])

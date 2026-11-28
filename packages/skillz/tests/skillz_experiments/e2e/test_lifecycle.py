"""Real CLI interruption and resume lifecycle evidence."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._intake import DRAFT_NAME
from support import CLI, assert_replayed, call_cli, cli_error, command_adapter, export_and_apply, make_scenario, read_events


def _record(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        return cast(dict[str, object], json.loads(path.read_text()))
    except json.JSONDecodeError:
        return None


def _held_tasks(pause_dir: Path) -> list[Path]:
    """Return the paused task calls that have no release yet."""
    return [path for path in pause_dir.glob("*.task.pending") if not path.with_suffix(".release").exists()]


def _baseline_durable(record: dict[str, object]) -> bool:
    """Return whether each baseline holdout slot has a durable outcome.

    The gate queues each baseline slot before the winner slots. After this point, no baseline call is in flight.
    A baseline call that runs at the kill has no outcome, so the resume evaluates its slot again.
    """
    done = [item for item in cast(list[dict[str, object]], record.get("outcomes", []))
            if item.get("split") == "holdout" and item.get("arm") == "baseline"
            and item.get("status") != "evaluation-failed"]
    slots = {(item["case_id"], item.get("repeat")) for item in done}
    return len(slots) == len({item["case_id"] for item in done}) * cast(int, record["repeats"])


def _advance(process: subprocess.Popen[str], pause_dir: Path, checkpoint: Path,
             *, stop_at_holdout: bool) -> tuple[dict[str, object], str, str]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        record = _record(checkpoint)
        pending = list(pause_dir.glob("*.pending"))
        if (stop_at_holdout and record is not None and record["phase"] == "searched"
                and any(item.get("split") == "holdout" and item.get("arm") == "winner"
                        for item in cast(list[dict[str, object]], record.get("outcomes", [])))
                and _baseline_durable(record) and _held_tasks(pause_dir)):
            return record, "", ""
        for path in pending:
            _ = path.with_suffix(".release").touch()
        # Drain both pipes while the CLI runs; its result can exceed the pipe capacity.
        try:
            output, error = process.communicate(timeout=0.01)
        except subprocess.TimeoutExpired:
            continue
        final_record = _record(checkpoint)
        assert final_record is not None, (process.returncode, output, error)
        return final_record, output, error
    raise AssertionError("CLI did not reach the bounded checkpoint or exit")


def _await_event(out: Path, pid: int) -> None:
    """Wait until the vendor process `pid` logs its call. It outlives a killed CLI in its own session."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if any(event.get("pid") == pid for event in read_events(out)):
                return
        except json.JSONDecodeError:
            pass
        time.sleep(0.01)
    raise AssertionError(f"vendor process {pid} did not log its call")


@pytest.mark.ac("skillz-experiments-end-to-end:AC-8")
@pytest.mark.ac("gh-756:AC-2")
@pytest.mark.ac("gh-756:AC-3")
@pytest.mark.ac("gh-756:AC-12")
@pytest.mark.ac("gh-756:AC-13")
@pytest.mark.ac("gh-756:AC-14")
@pytest.mark.ac("gh-756:AC-17")
@pytest.mark.ac("gh-756:AC-18")
@pytest.mark.parametrize("pipesize", [
    pytest.param(-1, id="default-pipes"),
    pytest.param(4096, id="small-pipes", marks=pytest.mark.skipif(
        sys.platform != "linux", reason="Explicit pipe sizing requires Linux.")),
])
def test_killed_cli_resumes_durable_holdout_without_repeating_completed_work(
    tmp_path: Path, pipesize: int,
) -> None:
    scenario = make_scenario(tmp_path)
    pause_dir = tmp_path / "pause"
    command = command_adapter(scenario, pause_dir=str(pause_dir))
    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out),
            "--model", "offline-test", "--harness", "command", "--live", "--edit", "prose", *command]
    _ = (scenario.out / DRAFT_NAME).write_text(json.dumps(scenario.draft))
    code, cases, error = call_cli(args, tmp_path)
    assert code == 1 and cli_error(error)["code"] == "cases-unapproved"
    baseline_calls = cast(dict[str, int], cases["estimate"])["calls"]
    flags = ["--approve-cases", str(cases["case_hash"]), "--approve-budget", str(baseline_calls)]
    code, baseline, error = call_cli([*args, *flags], tmp_path)
    assert code == 0 and baseline["search"] == "not-run", (baseline, error)
    checkpoint = scenario.out / "run.json"
    original = _record(checkpoint)
    assert original is not None
    aged = {key: cast(float, original[key]) - 24 * 60 * 60 for key in ("started", "started_monotonic")}
    _ = checkpoint.write_text(json.dumps(original | aged))
    measured = read_events(scenario.out)
    code, budget, error = call_cli([*args, "--search"], tmp_path)
    assert code == 1 and cli_error(error)["code"] == "budget-unapproved"
    assert read_events(scenario.out) == measured
    calls = cast(dict[str, int], budget["estimate"])["calls"]
    approved = [*args, "--search", "--approve-budget", str(calls)]
    pause_dir.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)

    process = subprocess.Popen([str(CLI), *approved], cwd=tmp_path, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True, pipesize=pipesize)
    try:
        _, output, error = _advance(process, pause_dir, checkpoint, stop_at_holdout=True)
        assert process.poll() is None, (process.returncode, output, error)
        os.killpg(process.pid, signal.SIGKILL)
        _, error = process.communicate(timeout=5)
        assert process.returncode == -signal.SIGKILL, error
        before = _record(checkpoint)
        assert before is not None
        completed = [item for item in cast(list[dict[str, object]], before["outcomes"])
                     if item.get("split") == "holdout" and item.get("status") != "evaluation-failed"]
        assert completed
        first_calls = cast(int, before["calls"])
        in_flight = _held_tasks(pause_dir)[0]
        _ = in_flight.with_suffix(".release").touch()
        _await_event(scenario.out, int(in_flight.name.split(".")[0]))
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            _ = process.communicate(timeout=5)

    resumed = subprocess.Popen([str(CLI), *args], cwd=tmp_path, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True, pipesize=pipesize)
    try:
        after, output, error = _advance(resumed, pause_dir, checkpoint, stop_at_holdout=False)
        assert resumed.returncode == 0, (resumed.returncode, output, error)
    finally:
        if resumed.poll() is None:
            os.killpg(resumed.pid, signal.SIGKILL)
            _ = resumed.communicate(timeout=5)

    if pipesize > 0:
        assert len(output.encode()) > pipesize
    result = cast(dict[str, object], json.loads(output))
    assert result["phase"] == after["phase"] == "complete"
    for key in ("cases_hash", "seed_hash", "best_validated", "started", "started_monotonic"):
        assert after[key] == before[key], key
    assert all(cast(float, after[key]) > cast(float, original[key]) for key in aged)
    assert after["plan_mode"] == "search" and after["estimate"] == budget["estimate"]
    final = cast(dict[str, int], after["budget"])
    assert "unspent" in final and "unspent" not in cast(dict[str, object], before["budget"])
    assert {key: value for key, value in final.items() if key != "unspent"} == before["budget"]
    assert final["prior_calls"] == original["calls"] and final["maximum"] == calls
    assert after["winner_hash"] == cast(dict[str, object], before["best_validated"])["identity"] != original["seed_hash"]
    assert first_calls <= cast(int, after["calls"]) <= final["prior_calls"] + calls
    held = [item for item in cast(list[dict[str, object]], after["outcomes"])
            if item.get("split") == "holdout" and item["arm"] != "replay"]
    triples = [(item["arm"], item["case_id"], item["repeat"]) for item in held]
    assert len(triples) == len(set(triples)) == 36
    assert all(item in held for item in completed)
    held_ids = {cast(str, item["case_id"]) for item in held}
    assert len(held_ids) == 6
    for case in scenario.draft:
        if case["id"] not in held_ids:
            continue
        source = cast(str, cast(dict[str, object], case["files"])["fixture.md"])
        prior_done = sum(item.get("case_id") == case["id"] for item in completed)
        resumed_tasks = sum(event.get("role") == "task" and event.get("visible_input") == source
                            and event.get("parent") == resumed.pid for event in read_events(scenario.out))
        assert resumed_tasks == 7 - prior_done
    previous = read_events(scenario.out)
    seed_tasks = Counter(event["visible_input"] for event in previous[len(measured):]
                         if event["role"] == "task" and event["candidate"] == original["seed_hash"])
    held_inputs = {cast(dict[str, str], case["files"])["fixture.md"] for case in scenario.draft if case["id"] in held_ids}
    assert seed_tasks == dict.fromkeys(held_inputs, 1)
    prompts = [cast(dict[str, str], event["request"])["prompt"] for event in previous if event["role"] == "reflection"]
    assert prompts and all(value.strip() not in prompt for prompt in prompts for value in held_inputs)
    exposed = set(cast(list[str], baseline["exposed"]))
    selected = {str(item["case_id"]) for item in cast(list[dict[str, object]], after["outcomes"])
                if item.get("split") in {"train", "validation"}}
    assert selected == exposed - held_ids
    search = cast(dict[str, object], result["search"])
    attempts = cast(list[dict[str, list[str]]], search["attempts"])
    assert attempts and {case for attempt in attempts for key in ("fixed", "broke") for case in attempt[key]} <= selected
    gate = cast(dict[str, object], result["gate"])
    moves = cast(dict[str, list[str]], gate["moves"])
    assert set(moves["fixed"]) == held_ids <= exposed
    assert not moves["broke"] and not moves["unresolved"]
    runner = cast(dict[str, object], cast(dict[str, object], result["claims"])["runner"])
    checks = cast(dict[str, str], runner["checks"])
    assert checks["holdout-isolated"] == checks["holdout-coverage"] == checks["calls-within-approval"] == "pass"
    # Killing an in-flight call can leave a charge without a scored row.
    assert {name for name, status in checks.items() if status != "pass"} == (
        {"calls-within-bound"} if result.get("usage_gap") else set())
    assert after["exposed"] == original["exposed"] == baseline["exposed"]
    assert_replayed(scenario.out)
    for field, invalid, diagnostic in (
        ("schema_version", 1, "run-schema-old"),
        ("engine_hash", "0" * 64, "evaluator differs from the frozen record"),
    ):
        _ = checkpoint.write_text(json.dumps(before | {field: invalid}))
        code, _, error = call_cli(args, tmp_path)
        assert code == 1 and diagnostic in error
        assert read_events(scenario.out) == previous
    _ = pause_dir.rename(tmp_path / "interrupted")
    outcomes = cast(list[dict[str, object]], after["outcomes"])
    missing = next(item for item in outcomes if item["arm"] == "replay")
    missing_case = next(case for case in scenario.draft if case["id"] == missing["case_id"])
    missing_input = cast(dict[str, str], missing_case["files"])["fixture.md"]
    roles = cast(dict[str, int], after["calls_by_role"])
    gated = {key: value for key, value in after.items() if key != "replay"} | {
        "phase": "gated", "outcomes": [item for item in outcomes if item is not missing],
        "calls": cast(int, after["calls"]) - 1, "calls_by_role": roles | {"task": roles["task"] - 1},
    }
    for flags in (args, [*args, "--search"]):
        _ = checkpoint.write_text(json.dumps(gated))
        code, replayed, error = call_cli(flags, tmp_path)
        assert (code, error, replayed["phase"]) == (0, "", "complete")
        assert {key: replayed[key] for key in ("gate", "replay", "calls", "claims")} == {
            key: result[key] for key in ("gate", "replay", "calls", "claims")}
        events = read_events(scenario.out)
        assert events[:len(previous)] == previous
        added = [(event["role"], event.get("candidate"), event.get("visible_input"))
                 for event in events[len(previous):] if event["role"] not in {"sandbox", "discover"}]
        assert added == [("task", after["winner_hash"], missing_input)]
        previous = events
        assert_replayed(scenario.out)
    code, repeated, error = call_cli(approved, tmp_path)
    assert (code, error, repeated["gate"]) == (0, "", result["gate"])
    assert read_events(scenario.out) == previous
    applied = export_and_apply(scenario, tmp_path)
    exported = _record(scenario.export / "report.json")
    assert exported is not None and exported["search"] == search and exported["gate"] == gate
    exported_rows = cast(list[dict[str, str]], exported["outcomes"])
    assert {row["case_id"] for row in exported_rows if "case_id" in row} == exposed
    replay = cast(dict[str, object], after["replay"])
    patch = {"candidate.patch": (scenario.export / "candidate.patch").read_text()}
    assert sha256(json.dumps(patch, sort_keys=True).encode()).hexdigest() == replay["patch_digest"]
    assert "Uppercase fixture.md." in (applied / "SKILL.md").read_text()
    assert (scenario.target / "SKILL.md").read_text() == scenario.original

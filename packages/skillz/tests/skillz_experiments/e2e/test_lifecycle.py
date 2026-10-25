"""Real CLI approval and interruption lifecycle evidence."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._intake import DRAFT_NAME
from support import CLI, call_cli, cli_error, command_adapter, export_and_apply, make_scenario, read_events


@pytest.mark.ac("skillz-experiments-end-to-end:AC-8")
def test_approval_refusals_start_no_inference(tmp_path: Path) -> None:
    scenario = make_scenario(tmp_path)
    command = command_adapter(scenario)
    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out),
            "--model", "offline-test", "--harness", "command", "--live", "--edit", "prose", *command]

    code, _, error = call_cli(args, tmp_path)
    assert code == 1 and cli_error(error)["code"] == "cases-missing"
    assert not any(event["role"] in ("task", "judge", "reflection") for event in read_events(scenario.out))

    _ = (scenario.out / DRAFT_NAME).write_text(json.dumps(scenario.draft))
    code, cases, error = call_cli(args, tmp_path)
    assert code == 1 and cli_error(error)["code"] == "cases-unapproved"
    assert not any(event["role"] in ("task", "judge", "reflection") for event in read_events(scenario.out))

    code, _, error = call_cli([*args, "--approve-cases", "0" * 64], tmp_path)
    assert code == 1 and cli_error(error)["code"] == "cases-unapproved"
    assert not any(event["role"] in ("task", "judge", "reflection") for event in read_events(scenario.out))

    case_hash = cast(str, cases["case_hash"])
    code, budget, error = call_cli([*args, "--approve-cases", case_hash], tmp_path)
    assert code == 1 and cli_error(error)["code"] == "budget-unapproved"
    assert cast(dict[str, int], budget["estimate"])["calls"] > 0
    assert not any(event["role"] in ("task", "judge", "reflection") for event in read_events(scenario.out))


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


def _advance(process: subprocess.Popen[str], pause_dir: Path, checkpoint: Path,
             *, stop_at_holdout: bool) -> tuple[dict[str, object], str, str]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        record = _record(checkpoint)
        pending = list(pause_dir.glob("*.pending"))
        if (stop_at_holdout and record is not None
                and any(item.get("split") == "holdout"
                        for item in cast(list[dict[str, object]], record.get("outcomes", [])))
                and _held_tasks(pause_dir)):
            return record, "", ""
        if process.poll() is not None:
            output, error = process.communicate(timeout=5)
            final_record = _record(checkpoint)
            assert final_record is not None, (process.returncode, output, error)
            return final_record, output, error
        for path in pending:
            _ = path.with_suffix(".release").touch()
        time.sleep(0.01)
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
def test_killed_cli_resumes_durable_holdout_without_repeating_completed_work(tmp_path: Path) -> None:
    scenario = make_scenario(tmp_path)
    pause_dir = tmp_path / "pause"
    pause_dir.mkdir()
    command = command_adapter(scenario, pause_dir=str(pause_dir))
    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out),
            "--model", "offline-test", "--harness", "command", "--live", "--edit", "prose", *command]
    _ = (scenario.out / DRAFT_NAME).write_text(json.dumps(scenario.draft))
    code, cases, error = call_cli(args, tmp_path)
    assert code == 1 and cli_error(error)["code"] == "cases-unapproved"
    case_hash = cast(str, cases["case_hash"])
    code, budget, error = call_cli([*args, "--approve-cases", case_hash], tmp_path)
    assert code == 1 and cli_error(error)["code"] == "budget-unapproved"
    calls = cast(dict[str, int], budget["estimate"])["calls"]
    approved = [*args, "--approve-cases", case_hash, "--approve-budget", str(calls)]
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    checkpoint = scenario.out / "run.json"

    process = subprocess.Popen([str(CLI), *approved], cwd=tmp_path, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
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

    resumed = subprocess.Popen([str(CLI), *approved], cwd=tmp_path, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        after, output, error = _advance(resumed, pause_dir, checkpoint, stop_at_holdout=False)
        assert resumed.returncode == 0, (resumed.returncode, output, error)
    finally:
        if resumed.poll() is None:
            os.killpg(resumed.pid, signal.SIGKILL)
            _ = resumed.communicate(timeout=5)

    result = cast(dict[str, object], json.loads(output))
    assert result["phase"] == after["phase"] == "complete"
    for key in ("cases_hash", "seed_hash", "best_validated",
                "budget", "started", "started_monotonic"):
        assert after[key] == before[key], key
    assert after["winner_hash"] == cast(dict[str, object], before["best_validated"])["identity"]
    assert first_calls <= cast(int, after["calls"]) <= calls
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
    code, repeated, error = call_cli(approved, tmp_path)
    assert (code, error, repeated["gate"]) == (0, "", result["gate"])
    assert read_events(scenario.out) == previous
    applied = export_and_apply(scenario, tmp_path)
    assert "Uppercase fixture.md." in (applied / "SKILL.md").read_text()
    assert (scenario.target / "SKILL.md").read_text() == scenario.original

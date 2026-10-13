"""Joined offline runs through the real Gemini reference wrapper."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from support import BUNDLE, CLI, FIXTURES, call_cli, export_and_apply, make_scenario, read_events, read_record, snapshot


def configured_wrapper(out: Path, home: Path, cli: Path, mode: str) -> tuple[Path, Path]:
    directory = out / "bin"
    directory.mkdir()
    for name, source in (("gemini", FIXTURES / "gemini.py"),
                         ("docker", FIXTURES.parents[1] / "fixtures/fake_docker.py")):
        target = directory / name
        _ = target.write_text("#!/usr/bin/env python3\n" + source.read_text())
        target.chmod(0o755)
    (directory / "vendor_common.py").symlink_to(FIXTURES / "vendor_common.py")
    _ = (directory / "vendor-settings.json").write_text(json.dumps({"mode": mode,
        "log": str(out / "vendor.jsonl")}))
    credential = home / ".gemini/oauth_creds.json"
    credential.parent.mkdir(parents=True)
    _ = credential.write_text('{"access_token":"offline-test"}')
    credential.chmod(0o600)
    _ = (home / ".gemini/settings.json").write_text("not exposed to Gemini")
    command = ([str(CLI)] if cli == CLI else [str(CLI.parent / "python"), "-I", "-S",
               str(home / "installed/skillz-experiment.pyz")])
    config = out / "gemini-harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "command", "identity": "offline-gemini",
        "command": ["/usr/bin/env", f"HOME={home}", *command, "harness-wrapper", "gemini",
                    "--bin-dir", str(directory)]}))
    return config, directory


def run_gemini(tmp_path: Path, cli: Path, *, helper: bool) -> tuple[dict[str, object], Path, Path, Path]:
    scenario = make_scenario(tmp_path, helper=helper)
    private = tmp_path / "private-home"
    config, directory = configured_wrapper(scenario.out, private, cli, "normal")
    before = snapshot(scenario.target)
    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out),
            "--model", "offline-test", "--live", "--edit", "prose+cli" if helper else "prose",
            "--harness-config", str(config)]
    code, first, error = call_cli(args, private, cli=cli)
    assert code == 1 and json.loads(error)["code"] == "cases-missing", (first, error)
    _ = (scenario.out / "cases.draft.json").write_text(json.dumps(scenario.draft))
    code, cases, error = call_cli(args, private, cli=cli)
    assert code == 1 and json.loads(error)["code"] == "cases-unapproved", (cases, error)
    case_hash = cast(str, cases["case_hash"])
    code, budget, error = call_cli(args + ["--approve-cases", case_hash], private, cli=cli)
    assert code == 1 and json.loads(error)["code"] == "budget-unapproved", (budget, error)
    calls = cast(dict[str, int], budget["estimate"])["calls"]
    code, result, error = call_cli(args + ["--approve-cases", case_hash, "--approve-budget", str(calls)],
                                   private, cli=cli)
    assert code == 0 and result["phase"] == "complete", (result, error)
    assert snapshot(scenario.target) == before
    applied = export_and_apply(scenario, private, cli=cli)
    return result, scenario.out, directory, applied


@pytest.mark.ac("skillz-experiments-end-to-end:AC-2")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-3")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-5")
@pytest.mark.parametrize("cli", [CLI, BUNDLE], ids=["source", "bundle"])
@pytest.mark.parametrize("helper", [False, True], ids=["P0", "H0"])
def test_gemini_wrapper_promotes_and_exports_from_actual_task_trace(tmp_path: Path, cli: Path,
                                                                    helper: bool) -> None:
    result, out, directory, applied = run_gemini(tmp_path, cli, helper=helper)
    record = read_record(out)
    gate = cast(dict[str, object], result["gate"])
    assert (gate["verdict"], gate["delta"], gate["se"], gate["cases"], gate["repeats"]) == (
        "promote", 1.0, 0.0, 6, 3)
    assert record["winner_hash"] != record["seed_hash"]
    assert cast(dict[str, object], record["best_validated"])["identity"] == record["winner_hash"]
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    held = [item for item in outcomes if item.get("split") == "holdout"]
    held_ids = {item["case_id"] for item in held}
    assert len(held_ids) == 6 and len(held) == 36
    assert {(item["arm"], item["case_id"], item["repeat"]) for item in held} == {
        (arm, case_id, repeat) for arm in ("baseline", "winner")
        for case_id in held_ids for repeat in range(3)}
    assert {item["score"] for item in held if item["arm"] == "baseline"} == {0.0}
    assert {item["score"] for item in held if item["arm"] == "winner"} == {1.0}
    log = read_events(out)
    assert {"task", "reflection"} <= {entry["role"] for entry in log}
    task_calls = [entry for entry in log if entry["role"] == "task"]
    assert any(entry["candidate"] == record["winner_hash"] for entry in task_calls)
    assert all(entry["archive_sha256"] is None for entry in task_calls)
    assert all(entry["home"] != str(out.parent / "private-home") and
               entry["home_entries"] == [".gemini", ".gemini/oauth_creds.json@"] for entry in log)
    assert all("run.json" not in cast(str, entry["prompt"]) and "reference" not in
               cast(str, entry["prompt"]) for entry in task_calls)
    assert (directory / "docker.log").is_file()
    assert "Uppercase fixture.md." in (applied / "SKILL.md").read_text()
    if helper:
        _ = (applied / "fixture.md").write_text("unseen\n")
        done = subprocess.run(["/usr/bin/python3", "-I", str(applied / "scripts/transform.py"), "fixture.md"],
                              cwd=applied, capture_output=True, text=True, check=False)
        assert done.returncode == 0 and json.loads(done.stdout) == {"text": "UNSEEN\n"}
    else:
        assert not (applied / "scripts/transform.py").exists()


@pytest.mark.ac("skillz-experiments-end-to-end:AC-5")
def test_malformed_gemini_answer_charges_calls_without_a_trace(tmp_path: Path) -> None:
    scenario = make_scenario(tmp_path)
    private = tmp_path / "private-home"
    config, _ = configured_wrapper(scenario.out, private, CLI, "malformed-json")
    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out),
            "--model", "offline-test", "--live", "--edit", "prose",
            "--harness-config", str(config)]
    code, _, error = call_cli(args, private)
    assert code == 1 and json.loads(error)["code"] == "cases-missing"
    _ = (scenario.out / "cases.draft.json").write_text(json.dumps(scenario.draft))
    code, cases, error = call_cli(args, private)
    assert code == 1 and json.loads(error)["code"] == "cases-unapproved"
    case_hash = cast(str, cases["case_hash"])
    code, budget, error = call_cli(args + ["--approve-cases", case_hash], private)
    assert code == 1 and json.loads(error)["code"] == "budget-unapproved"
    calls = cast(dict[str, int], budget["estimate"])["calls"]
    code, result, error = call_cli(args + ["--approve-cases", case_hash, "--approve-budget", str(calls)],
                                   private)
    assert code == 1 and json.loads(error)["code"] == "search-failed", (result, error)
    record = read_record(scenario.out)
    assert record["failure_code"] == "search-failed" and record["calls"] == 12
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    failed = [item for item in outcomes if item.get("case_id") is not None]
    assert failed and all(item["status"] == "evaluation-failed" for item in failed)
    path = Path(cast(str, failed[0]["failure"]).split("private stderr tail: ", 1)[1])
    assert json.loads(path.read_text())["error"] == "RuntimeError: gemini answer is not a JSON object"
    log = read_events(scenario.out)
    assert len(log) == record["calls"]

"""Shared body of the first-approval search tests: one file per row keeps each file under the test-time ceiling."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import cast

from skillz_experiments._intake import DRAFT_NAME
from support import call_cli, cli_error, command_adapter, make_scenario, read_events, read_record


def check_first_approval_search(tmp_path: Path, *, saturated: bool, token_rule: bool, expected: str,
                                objective: str | None = None) -> None:
    """Drive `run --search` on a new run directory and check where its first approval stops.

    `objective` adds `--objective` to each `--search` call, as the procedure says until the search approval.
    """
    scenario = make_scenario(tmp_path)
    if saturated:
        for case in scenario.draft:
            case["files"] = {"fixture.md": cast(dict[str, str], case["expected"])["text"]}
    if token_rule:
        path = scenario.target / "evals/autoimprove.json"
        contract = cast(dict[str, object], json.loads(path.read_text()))
        contract["statistics"] = {"min_token_saving": 0.1, "max_quality_loss": 0.05}
        _ = path.write_text(json.dumps(contract))
    _ = (scenario.out / DRAFT_NAME).write_text(json.dumps(scenario.draft))
    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out), "--search",
            "--model", "offline-test", "--harness", "command", "--live", "--edit", "prose", *command_adapter(scenario),
            *(["--objective", objective] if objective is not None else [])]
    status, cases, error = call_cli(args, tmp_path)
    assert status == 1 and cli_error(error)["code"] == "cases-unapproved"
    assert read_events(scenario.out) == []
    status, stop, error = call_cli([*args, "--approve-cases", str(cases["case_hash"]),
                                    "--approve-budget", "1000"], tmp_path)
    assert status == 1 and cli_error(error)["code"] == expected
    report = cast(dict[str, object], json.loads((scenario.out / "report.json").read_text()))
    log = read_events(scenario.out)
    counts = Counter(event["role"] for event in log)
    assert counts["task"] == 2 * len(scenario.draft) and counts["reflection"] == 0
    assert read_record(scenario.out)["plan_mode"] == "baseline"
    if expected == "budget-unapproved":
        estimate = cast(dict[str, int], stop["estimate"])
        assert estimate != cases["estimate"] and 0 < estimate["calls"] < 1000
    else:
        assert stop["baseline"] == report and "estimate" not in stop
    status, again, error = call_cli(args, tmp_path)
    assert status == 1 and cli_error(error)["code"] == expected and again == stop
    assert read_events(scenario.out) == log
    if saturated and expected == "cases-too-easy" and not token_rule:
        record = read_record(scenario.out)
        _ = (scenario.out / "run.json").write_text(json.dumps(record | {"engine_hash": "0" * 64}))
        status, _, error = call_cli(args, tmp_path)
        assert status == 1 and "evaluator differs from the frozen record" in error
        assert read_events(scenario.out) == log

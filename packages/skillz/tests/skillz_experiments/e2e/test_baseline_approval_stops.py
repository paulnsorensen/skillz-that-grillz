"""The approval questions of a first run stop before any model call (source command)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from skillz_experiments._intake import DRAFT_NAME
from support import call_cli, cli_error, command_adapter, make_scenario, read_events


@pytest.mark.ac("gh-756:AC-9")
@pytest.mark.ac("gh-756:AC-11")
def test_unapproved_cases_and_budget_stop_before_any_model_call(tmp_path: Path) -> None:
    scenario = make_scenario(tmp_path)
    _ = (scenario.out / DRAFT_NAME).write_text(json.dumps(scenario.draft))
    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out),
            "--model", "offline-test", "--harness", "command", "--live", "--edit", "prose", *command_adapter(scenario)]

    code, cases, stderr = call_cli(args, tmp_path)
    assert code == 1 and cli_error(stderr)["code"] == "cases-unapproved"
    assert cases["question"]
    case_hash = cast(str, cases["case_hash"])
    assert len(case_hash) == 64
    estimate = cast(dict[str, int], cases["estimate"])
    approved = ["--approve-cases", case_hash]
    code, hash_only, stderr = call_cli([*args, *approved], tmp_path)
    assert code == 1 and cli_error(stderr)["code"] == "budget-unapproved"
    assert hash_only["estimate"] == estimate
    code, _, stderr = call_cli([*args, "--approve-cases", "0" * 64, "--approve-budget", str(estimate["calls"])],
                               tmp_path)
    assert code == 1 and cli_error(stderr)["code"] == "cases-unapproved"
    code, _, stderr = call_cli([*args, *approved, "--approve-budget", str(estimate["calls"] - 1)], tmp_path)
    assert code == 1 and cli_error(stderr)["code"] == "budget-unapproved"
    assert read_events(scenario.out) == []

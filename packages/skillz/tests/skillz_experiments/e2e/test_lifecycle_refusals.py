"""Real CLI approval refusals that start no inference."""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._intake import DRAFT_NAME
from support import call_cli, cli_error, command_adapter, make_scenario, read_events


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

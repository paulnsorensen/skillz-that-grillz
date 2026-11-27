"""Baseline approval through the installed bundle and command subprocess."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import cast

import pytest
from skillz_experiments._intake import DRAFT_NAME
from support import BUNDLE, call_cli, cli_error, command_adapter, make_scenario, read_events, read_record

# The split needs a train of 4 cases from 2 families, 1 validation family, and a holdout of 6 cases: 11 cases at least.
# With 9 families, the seeded split of 11 cases keeps f0 in train and a failing case in the holdout.
FAMILIES = 9
CASES = 11


@pytest.mark.ac("gh-756:AC-1")
@pytest.mark.ac("gh-756:AC-3")
@pytest.mark.ac("gh-756:AC-4")
@pytest.mark.ac("gh-756:AC-5")
@pytest.mark.ac("gh-756:AC-6")
@pytest.mark.ac("gh-756:AC-7")
@pytest.mark.ac("gh-756:AC-8")
@pytest.mark.ac("gh-756:AC-10")
def test_one_approval_reports_all_seed_cases_without_search(tmp_path: Path) -> None:
    scenario = make_scenario(tmp_path)
    contract_path = scenario.target / "evals/autoimprove.json"
    contract = cast(dict[str, object], json.loads(contract_path.read_text()))
    contract["kinds"] = {"task": {"grader": "judge", "rubric": "Compare the output to the reference text."}}
    contract["statistics"] = {"min_token_saving": 0.1, "max_quality_loss": 0.05}
    _ = contract_path.write_text(json.dumps(contract))
    draft = [scenario.draft[0] | {
        "id": f"t{i}", "family": f"f{i % FAMILIES}",
        "files": {"fixture.md": f"case-{i}\n" if i % FAMILIES else f"CASE-{i}\n"},
        "expected": {"text": f"CASE-{i}\n", "criteria": [{"id": "text", "text": "THE output SHALL match the reference text."}]},
    } for i in range(CASES)]
    _ = (scenario.out / DRAFT_NAME).write_text(json.dumps(draft))
    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out),
            "--model", "offline-test", "--harness", "command", "--live", "--edit", "prose", *command_adapter(scenario)]

    def call(*flags: str) -> tuple[int, dict[str, object], str]:
        return call_cli([*args, *flags], tmp_path, cli=BUNDLE)

    code, cases, stderr = call()
    assert code == 1 and cli_error(stderr)["code"] == "cases-unapproved"
    case_hash = cast(str, cases["case_hash"])
    estimate = cast(dict[str, int], cases["estimate"])
    assert read_events(scenario.out) == []

    code, report, stderr = call("--approve-cases", case_hash, "--approve-budget", str(estimate["calls"]))
    assert code == 0, stderr
    assert json.loads((scenario.out / "report.json").read_text()) == report
    rows = cast(dict[str, dict[str, object]], report["cases"])
    assert sorted(rows) == sorted(str(case["id"]) for case in draft)
    assert all(row["scores"] == ([1.0, 1.0] if int(key[1:]) % FAMILIES == 0 else [0.0, 0.0])
               for key, row in rows.items())
    means = cast(dict[str, dict[str, float]], report["family_means"])
    assert set(means) == {"train", "validation", "holdout"}
    assert set().union(*means.values()) == {f"f{i}" for i in range(FAMILIES)}
    assert any("f0" in families for families in cast(dict[str, list[str]], report["saturated_families"]).values())
    failures = {key for key in rows if int(key[1:]) % FAMILIES != 0}
    assert any(rows[key]["split"] == "holdout" for key in failures)
    assert all(rows[key]["unmet"] == [{"text": {"level": 1, "reason": "fake judge"}}] * 2 for key in failures)
    assert report["criteria_flags"] == []
    zero = cast(list[dict[str, object]], report["zero_cases"])
    assert {row["case_id"] for row in zero} == failures
    assert all(row["repeats"] == 2 and row["arm"] == "baseline" for row in zero)
    assert {report[key] for key in ("search", "holdout_gate", "replay")} == {"not-run"}
    record = read_record(scenario.out)
    assert record["exposed"] == report["exposed"] == sorted(rows)
    assert not {"winner", "gate"} & record.keys()
    events = read_events(scenario.out)
    counts = Counter(event["role"] for event in events)
    runs = 2 * len(draft)
    assert counts["task"] == runs and counts["judge"] == runs and counts["reflection"] == 0
    for name in ("calls_by_role", "seconds_by_role"):
        roles = cast(dict[str, object], report[name])
        assert {"task", "judge"} <= roles.keys() and "reflection" not in roles, (name, roles)
    usage = cast(dict[str, dict[str, int]], report["usage_by_role"])
    assert {role: usage[role]["outcomes"] for role in ("task", "judge", "reflection")} == {
        "task": runs, "judge": runs, "reflection": 0,
    }
    assert all(usage[role]["input_tokens"] > 0 for role in ("task", "judge"))

    code, resumed, stderr = call()
    assert code == 0 and report == resumed, stderr
    assert read_events(scenario.out) == events
    code, _, stderr = call_cli(["export", str(scenario.out), "--out", str(scenario.export)], tmp_path, cli=BUNDLE)
    assert code == 1 and "report.json" in stderr
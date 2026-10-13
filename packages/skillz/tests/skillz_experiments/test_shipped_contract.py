"""The skillz skill ships a contract and seed that a run can score."""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._contract import load_contract
from skillz_experiments._intake import load_draft
from skillz_experiments._seed import seed_cases
from skillz_experiments.run._plan import supported_kind

SKILL = Path(__file__).resolve().parents[4] / "skills" / "skillz"


@pytest.mark.ac("gh-299:AC-7")
def test_shipped_contract_has_a_supported_kind_and_no_audit_grader() -> None:
    contract = load_contract(SKILL)
    assert supported_kind(contract) == "task"
    assert all(grader.type != "audit" for grader in contract.kinds.values())


@pytest.mark.ac("gh-299:AC-8")
def test_shipped_seed_yields_task_cases_that_load_as_a_draft(tmp_path: Path) -> None:
    cases = seed_cases(SKILL)
    assert cases is not None
    tasks = [case for case in cases if case["kind"] == "task"]
    assert tasks
    for case in tasks:
        assert case.get("family")
        assert case.get("files")
        assert case.get("expected")
    draft = tmp_path / "cases.draft.json"
    _ = draft.write_text(json.dumps(tasks), encoding="utf-8")
    loaded = load_draft(draft)
    assert [case.identifier for case in loaded] == [cast(str, case["id"]) for case in tasks]
    assert all(case.family and case.files and case.expected is not None for case in loaded)

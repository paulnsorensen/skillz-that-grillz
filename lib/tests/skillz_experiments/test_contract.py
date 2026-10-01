from __future__ import annotations

import json
from pathlib import Path

import pytest

from skillz_experiments._cases import load_cases
from skillz_experiments._runtime import Budget, BudgetExhausted


def test_grouped_splits_reject_leakage(tmp_path: Path) -> None:
    case = {"id": "one", "family": "same", "split": "train", "request": "Inspect.",
            "files": {"SKILL.md": "---\nname: example\n---\nBody\n"},
            "expected": {"answer": "yes"}, "provenance": "authored", "provider_approved": True}
    manifest = tmp_path / "cases.json"
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": [case, dict(case, id="two", split="holdout")]}))
    with pytest.raises(ValueError, match="family"):
        _ = load_cases(manifest)


def test_incomplete_case_stays_diagnostic(tmp_path: Path) -> None:
    manifest = tmp_path / "cases.json"
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": [{"id": "one", "family": "one", "split": "train", "provenance": "approved-analytics", "provider_approved": False}]}))
    cases = load_cases(manifest)
    assert not cases[0].eligible


def test_budget_reserves_holdout_and_counts_failures() -> None:
    budget = Budget(8, 60, reserve=6)
    budget.claim()
    budget.claim()
    with pytest.raises(BudgetExhausted):
        budget.claim()
    for _ in range(6):
        budget.claim(holdout=True)
    with pytest.raises(BudgetExhausted):
        budget.claim(holdout=True)
    assert budget.calls == 8

@pytest.mark.parametrize("path", ["a//b", "a/./b", "../x", "/x", "a/", "a\x00b", ".secret"])
def test_reject_path_aliases(path: str) -> None:
    from skillz_experiments._cases import relative

    with pytest.raises(ValueError):
        _ = relative(path)

@pytest.mark.parametrize("path", ["home/x", "tmp/x", "answer.json", "response-schema.json", "AGENTS.md", "nested/AGENTS.md"])
def test_fixture_cannot_overwrite_runtime(path: str) -> None:
    from skillz_experiments._cases import text_map

    with pytest.raises(ValueError, match="runtime-owned"):
        _ = text_map({path: "untrusted"})


def test_manifest_version_is_not_boolean(tmp_path: Path) -> None:
    manifest = tmp_path / "cases.json"
    _ = manifest.write_text('{"schema_version":true,"cases":[]}')
    with pytest.raises(ValueError, match="schema_version"):
        _ = load_cases(manifest)

"""The proposer feedback of one scored search outcome: unmet criteria, the edit hint, and their bounds (gh-501)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, load_cases
from skillz_experiments._contract import parse
from skillz_experiments.run._outcomes import FIELDS_LIMIT, TRUNCATED, feedback_for
from skillz_experiments.search import HarnessText

RULE = "THE summary SHALL list every overdue invoice by its due date."


def _case(tmp_path: Path, criteria: list[dict[str, object]]) -> Case:
    rules = parse({"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
                   "kinds": {"style": {"grader": "judge", "rubric": "rate the summary"}}}, "skill")
    item = {"id": "one", "family": "one", "split": "train", "kind": "style", "request": "Summarise ledger.csv.",
            "files": {}, "expected": {"criteria": criteria}, "provenance": "test", "provider_approved": True}
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": [item]}))
    return load_cases(path, rules.grader_types())[0]


def _feedback(case: Case, levels: dict[str, tuple[int, str]], candidate: Candidate) -> dict[str, object]:
    result: dict[str, object] = {"score": 0.5, "scores": {"judge": 0.5},
                                 "criteria": {name: {"level": level, "reason": reason} for name, (level, reason) in levels.items()}}
    return feedback_for(result, case, HarnessText(), candidate)[1]


@pytest.mark.ac("gh-501:AC-3")
@pytest.mark.parametrize(("files", "hint"), [
    ({"SKILL.md": "Always list every overdue invoice first.", "reference.md": "Invoice fields."}, ["SKILL.md"]),
    ({"SKILL.md": "Always list every overdue invoice first.", "reference.md": "We list every overdue invoice here."},
     ["SKILL.md", "reference.md"]),
    ({"SKILL.md": "Write a summary.", "reference.md": "Invoice fields."}, ["unknown"]),
], ids=["one-file", "two-files", "no-file"])
def test_the_edit_hint_names_each_editable_file_that_shares_four_words_with_an_unmet_rule(
        tmp_path: Path, files: dict[str, str], hint: list[str]) -> None:
    case = _case(tmp_path, [{"id": "overdue", "text": RULE}])
    # A file that the proposer may not edit is never a hint, even when it holds the words.
    candidate = Candidate(files | {"notes.txt": "list every overdue invoice"}, ("SKILL.md", "reference.md"))
    feedback = _feedback(case, {"overdue": (2, "misses two invoices")}, candidate)
    assert feedback["edit_hint"] == hint
    assert candidate.editable == ("SKILL.md", "reference.md")


def test_a_fully_met_outcome_has_no_unmet_criteria_and_no_hint(tmp_path: Path) -> None:
    case = _case(tmp_path, [{"id": "overdue", "text": RULE}])
    feedback = _feedback(case, {"overdue": (4, "complete")}, Candidate({"SKILL.md": "x"}, ("SKILL.md",)))
    assert "unmet" not in feedback and "edit_hint" not in feedback


@pytest.mark.ac("gh-501:AC-5")
def test_thirty_long_unmet_criteria_keep_every_id_and_cap_the_text_and_reason_fields(tmp_path: Path) -> None:
    criteria: list[dict[str, object]] = [{"id": f"c{index:02}", "text": "THE summary SHALL " + "x" * 982, "must": index % 3 == 0}
                                         for index in range(30)]
    assert {len(cast(str, item["text"])) for item in criteria} == {1000}
    case = _case(tmp_path, criteria)
    levels = {cast(str, item["id"]): (1 + index % 3, "y" * 1000) for index, item in enumerate(criteria)}
    unmet = cast(list[dict[str, object]], _feedback(case, levels, Candidate({"SKILL.md": "x"}, ("SKILL.md",)))["unmet"])
    assert sorted(cast(str, entry["id"]) for entry in unmet) == [f"c{index:02}" for index in range(30)]
    assert all(entry["must"] == (int(cast(str, entry["id"])[1:]) % 3 == 0) for entry in unmet)
    assert all(entry["level"] == levels[cast(str, entry["id"])][0] for entry in unmet)
    # `must` entries come first, then the lowest level.
    assert [(not entry["must"], entry["level"]) for entry in unmet] == sorted((not entry["must"], entry["level"]) for entry in unmet)
    fields = [cast(str, entry[key]) for entry in unmet for key in ("text", "reason")]
    assert sum(map(len, fields)) <= FIELDS_LIMIT
    cut = [field for field in fields if len(field) < 300]
    assert cut and set(cut) == {TRUNCATED}
    kept = [entry for entry in unmet if entry["text"] != TRUNCATED]
    assert kept and all(len(cast(str, entry["text"])) == len(cast(str, entry["reason"])) == 300 for entry in kept)
    # Only later entries are cut.
    assert unmet[:len(kept)] == kept

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


@pytest.mark.ac("gh-501:AC-5")
def test_more_unmet_criteria_than_the_markers_fit_keep_every_id_within_the_limit(tmp_path: Path) -> None:
    criteria: list[dict[str, object]] = [{"id": f"c{index:03}", "text": "THE summary SHALL list it."} for index in range(200)]
    case = _case(tmp_path, criteria)
    levels = {cast(str, item["id"]): (2, "missing") for item in criteria}
    unmet = cast(list[dict[str, object]], _feedback(case, levels, Candidate({"SKILL.md": "x"}, ("SKILL.md",)))["unmet"])
    assert len(unmet) == 200 and {cast(str, entry["id"]) for entry in unmet} == {f"c{index:03}" for index in range(200)}
    fields = [cast(str, entry[key]) for entry in unmet for key in ("text", "reason")]
    assert sum(map(len, fields)) <= FIELDS_LIMIT
    # 200 markers pass the limit alone, so a cut field is empty; the first entries keep their whole text.
    assert unmet[0]["text"] == "THE summary SHALL list it." and unmet[-1]["text"] == unmet[-1]["reason"] == ""


@pytest.mark.ac("gh-501:AC-4")
def test_the_gate_feedback_is_masked_before_its_cut_so_it_stays_within_1000_characters(tmp_path: Path) -> None:
    case = _case(tmp_path, [{"id": "overdue", "text": RULE}])
    result: dict[str, object] = {"score": 0.0, "gate_diagnostic": "missing output/x " * 58 + "exit 1"}
    harness = HarnessText.build([], phrases=["output/x"])
    gate = cast(str, feedback_for(result, case, harness, Candidate({"SKILL.md": "x"}, ("SKILL.md",)))[1]["gate_feedback"])
    diagnostic = cast(str, result["gate_diagnostic"])
    # The mask makes the text longer than the limit, so the cut must come after it.
    assert len(diagnostic) <= 1000 < len(harness.mask(diagnostic))
    assert len(gate) == 1000 and gate.endswith("[harness] exit 1") and "output/x" not in gate


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
    assert kept and all(len(cast(str, entry[key])) == 300 for entry in kept for key in ("text", "reason"))
    # A shortened kept field ends with the marker and keeps the head of the original.
    assert all(entry["text"] == "THE summary SHALL " + "x" * (300 - len(TRUNCATED) - 18) + TRUNCATED for entry in kept)
    assert all(entry["reason"] == "y" * (300 - len(TRUNCATED)) + TRUNCATED for entry in kept)
    # Only later entries are cut.
    assert unmet[:len(kept)] == kept


@pytest.mark.ac("gh-501:AC-5")
def test_a_kept_field_at_or_under_300_characters_is_unchanged_and_one_over_gets_the_marker(tmp_path: Path) -> None:
    exact = "THE summary SHALL " + "e" * 282
    over = "THE summary SHALL " + "o" * 283
    assert (len(exact), len(over)) == (300, 301)
    case = _case(tmp_path, [{"id": "exact", "text": exact}, {"id": "over", "text": over}])
    levels = {"exact": (1, "r" * 300), "over": (2, "short reason")}
    unmet = {cast(str, entry["id"]): entry for entry in cast(
        list[dict[str, object]], _feedback(case, levels, Candidate({"SKILL.md": "x"}, ("SKILL.md",)))["unmet"])}
    assert unmet["exact"]["text"] == exact and unmet["exact"]["reason"] == "r" * 300
    assert unmet["over"]["text"] == over[:300 - len(TRUNCATED)] + TRUNCATED and len(cast(str, unmet["over"]["text"])) == 300
    assert unmet["over"]["reason"] == "short reason"


@pytest.mark.ac("gh-501:AC-5")
def test_a_credential_whose_closing_quote_lies_past_the_field_limit_is_redacted_before_the_cut(tmp_path: Path) -> None:
    # A criterion text holds at most 1,000 characters, so only the reason can pass the old 1,200-character window.
    short = 'password="ZZCANARYT ' + "word " * 150 + '" tail'
    long = 'password="ZZCANARYR ' + "word " * 400 + '" tail'
    assert 300 < len(short) < 1000 < 1200 < len(long)
    case = _case(tmp_path, [{"id": "quoted", "text": "THE summary SHALL hide " + short}])
    feedback = _feedback(case, {"quoted": (1, "reason " + long)}, Candidate({"SKILL.md": "x"}, ("SKILL.md",)))
    unmet = cast(list[dict[str, object]], feedback["unmet"])
    assert "ZZCANARY" not in json.dumps(feedback)
    assert unmet[0]["text"] == "THE summary SHALL hide [redacted] tail"
    assert unmet[0]["reason"] == "reason [redacted] tail"


@pytest.mark.ac("gh-501:AC-4")
def test_an_escaped_quote_credential_in_a_criterion_text_and_reason_is_redacted(tmp_path: Path) -> None:
    secret = r'password=\"one\\\" two zzcanary\" kept'
    case = _case(tmp_path, [{"id": "escaped", "text": "THE summary SHALL hide " + secret}])
    feedback = _feedback(case, {"escaped": (1, "reason " + secret)}, Candidate({"SKILL.md": "x"}, ("SKILL.md",)))
    assert "zzcanary" not in json.dumps(feedback) and "two" not in json.dumps(feedback)

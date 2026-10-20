"""Content tests for the criteria drafting reference and its link from the experiments reference."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REFERENCES = Path(__file__).resolve().parents[4] / "skills/skillz/references"
DRAFTING = REFERENCES / "criteria-drafting.md"
EXPERIMENTS = REFERENCES / "experiments.md"
SKILL = REFERENCES.parent / "SKILL.md"

STEP_MARKERS = (
    "Convert the source fields",
    "Read the skill files",
    "Mine past sessions for friction",
    "Run an optional research pass",
    "List the forks",
    "Record deferred picks as agent-decided",
    "Re-offer later forks",
    "Read the last run's `criteria_flags`",
    "Mark `must` only for dealbreakers",
)
OPTIONAL_MARKERS = ("if ", "optional", "when ")
OPTIONAL_SKILLS = ("/mold", "/briesearch", "/session-analytics")


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}.*?(?=^## |\Z)", text, re.M | re.S)
    assert match is not None, heading
    return match[0]


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text)
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z*`\d])", flat) if s]


@pytest.mark.ac("judge-criteria-verdicts:AC-12")
def test_ac12_reference_exists_and_draft_section_links_it() -> None:
    assert DRAFTING.is_file()
    section = _section(EXPERIMENTS.read_text(), "Draft the cases")
    assert "criteria drafting reference that `SKILL.md` lists" in section
    assert "`references/criteria-drafting.md`" in SKILL.read_text()


@pytest.mark.ac("judge-criteria-verdicts:AC-12")
@pytest.mark.parametrize("marker", STEP_MARKERS)
def test_ac12_step_is_present(marker: str) -> None:
    steps: list[str] = re.findall(r"^\d+\. \*\*(.+?)\.\*\*", _section(DRAFTING.read_text() + "\n## end\n", "Steps"), re.M)
    assert any(marker in step for step in steps), marker


@pytest.mark.ac("judge-criteria-verdicts:AC-12")
def test_ac12_reference_links_session_mining_and_shows_ears_example() -> None:
    text = DRAFTING.read_text()
    assert "`Check the files offline` section" in text
    assert "## Check the files offline" in EXPERIMENTS.read_text()
    assert re.search(r"THE \w+ SHALL", text)


@pytest.mark.ac("judge-criteria-verdicts:AC-13")
def test_ac13_optional_skills_sit_in_optional_sentences() -> None:
    mentions = [s for s in _sentences(DRAFTING.read_text()) if any(name in s for name in OPTIONAL_SKILLS)]
    assert len(mentions) >= len(OPTIONAL_SKILLS)
    for sentence in mentions:
        assert any(marker in sentence.lower() for marker in OPTIONAL_MARKERS), sentence
    found = {name for name in OPTIONAL_SKILLS if any(name in s for s in mentions)}
    assert found == set(OPTIONAL_SKILLS)

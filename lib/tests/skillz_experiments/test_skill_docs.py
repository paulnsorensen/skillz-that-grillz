"""Guard tests over the skillz documentation: mode table, audit lens, mode steps, and sentence length."""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

SKILL_DIR = Path(__file__).resolve().parents[3] / "skills/skillz"
SKILL = SKILL_DIR / "SKILL.md"
EXPERIMENTS = SKILL_DIR / "references/experiments.md"


def _table(text: str, header: str) -> list[list[str]]:
    """Return the body rows of the markdown table whose header row starts with `header`."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"| {header}"))
    rows: list[list[str]] = []
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}.*?(?=^## |\Z)", text, re.M | re.S)
    assert match is not None, heading
    return match[0]


def test_mode_table_has_autoimprove_with_experiment_alias() -> None:
    text = SKILL.read_text()
    rows = {row[0].strip("`").split(" ")[0]: row for row in _table(text, "Mode")}
    aliases = {mode: re.findall(r"`([^`]+)`", row[1]) for mode, row in rows.items()}
    assert "experiment" not in rows
    assert aliases["autoimprove"] == ["experiment"]
    assert aliases["improve"] == ["optimize", "tighten"]
    assert "references/experiments.md" in _routing(text)


def _routing(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("For `autoimprove`"))


def test_audit_lens_has_prose_row_citing_long_sentences(limits: tuple[int, int]) -> None:
    advisory, maximum = limits
    row = next(r for r in _table(SKILL.read_text(), "Lens") if r[0].startswith("**Prose (ASD-STE100)"))
    assert "inspect_skill.py" in row[2]
    assert "long_sentences" in row[2]
    assert f"over {maximum} words" in row[2] and f"({advisory + 1} to {maximum} words)" in row[2]
    assert "Report passive voice and multi-instruction sentences as findings." in row[2]
    assert "long_sentences" not in row[0] + row[1]


def _steps(section: str) -> list[str]:
    return cast(list[str], re.findall(r"^\d+\. .*?(?=^\d+\. |^Done means|\Z)", section, re.M | re.S))


@pytest.mark.parametrize(("mode", "subject"), [("add", "`SKILL.md`"), ("improve", "the target file")])
def test_add_improve_steps_run_prose_check_on_their_subject(mode: str, subject: str) -> None:
    section = _section(SKILL.read_text(), f"Mode: {mode}")
    step = next(text for text in _steps(section) if "prose check" in text)
    assert subject in step
    assert "each changed reference" in step
    assert "before you report" in step


def test_shared_prose_check_names_inspector_limit_and_references(limits: tuple[int, int]) -> None:
    advisory, maximum = limits
    match = re.search(r"^## Prose check.*?(?=^## |\Z)", SKILL.read_text(), re.M | re.S)
    assert match is not None
    items = _steps(match[0])
    assert len(items) == 3
    assert "inspect_skill.py" in items[0] and "SKILL.md" in items[0]
    assert f"over {maximum} words" in items[1]
    assert f"`advisory_sentences` entry ({advisory + 1} to {maximum} words)" in items[1]
    assert "procedural step" in items[1]
    assert f"{maximum} words" in items[2] and f"{advisory} words for a procedural step" in items[2]
    assert "by hand" in items[2]


def test_no_contract_step_offers_judge_only_or_draft_contract() -> None:
    section = _section(EXPERIMENTS.read_text(), "No contract")
    choices = cast(list[str], re.findall(r"^\d+\. .*?(?=^\d+\. |^\S|\Z)", section, re.M | re.S))
    assert len(choices) == 2
    assert "judge-only" in choices[0] and "`judge`" in choices[0] and "powerful model" in choices[0]
    assert "approved" not in choices[0]
    assert "find" in choices[1].lower() and "draft" in choices[1].lower() and "contract" in choices[1].lower()
    ask = section.splitlines().index(next(line for line in section.splitlines() if line.startswith("1. ")))
    assert any("ask the user" in line for line in section.splitlines()[:ask])
    assert "Draft a contract" in choices[0]
    tail = section.split(choices[1])[1]
    assert 'Save every drafted contract with `"status": "draft"`, for both choices.' in tail
    assert "until the user approves" in tail and '`"status": "approved"` only after the user approves' in tail


def _long(path: Path, inspector: ModuleType, maximum: int) -> list[dict[str, int]]:
    lines = path.read_text().splitlines()
    start = 0
    if lines and lines[0] == "---":
        start = next(i for i, line in enumerate(lines[1:], 1) if line == "---") + 1
    sentences = cast(Callable[[list[str], int], list[dict[str, int]]], getattr(inspector, "prose_sentences"))
    return [hit for hit in sentences(lines, start) if hit["words"] > maximum]


# Table cells are exempt: the inspector flushes sentences on `|`.
PROSE_FILES = [SKILL, *sorted((SKILL_DIR / "references").glob("*.md")), *sorted((SKILL_DIR / "engine/references").glob("*.md"))]


@pytest.mark.parametrize("path", PROSE_FILES, ids=[str(path.relative_to(SKILL_DIR)) for path in PROSE_FILES])
def test_skill_prose_has_no_sentence_over_limit(path: Path, inspector: ModuleType, limits: tuple[int, int]) -> None:
    assert _long(path, inspector, limits[1]) == []

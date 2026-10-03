"""Guard tests over the skillz documentation: mode table, audit lens, mode steps, and sentence length."""
from __future__ import annotations

import importlib.util
import re
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

SKILL_DIR = Path(__file__).resolve().parents[3] / "skills/skillz"
SKILL = SKILL_DIR / "SKILL.md"
EXPERIMENTS = SKILL_DIR / "references/experiments.md"
MAX_WORDS = 25


def _inspector() -> ModuleType:
    spec = importlib.util.spec_from_file_location("inspect_skill", SKILL_DIR / "scripts/inspect_skill.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    modes = [row[0] for row in _table(text, "Mode")]
    assert any(mode.startswith("`autoimprove") for mode in modes)
    assert not any(mode.startswith("`experiment") for mode in modes)
    aliases = [line for line in text.splitlines() if "alias" in line]
    assert any("`experiment`" in line and "`autoimprove`" in line for line in aliases)
    assert any("`optimize`" in line and "`tighten`" in line and "`improve`" in line for line in aliases)
    routing = next(line for line in text.splitlines() if line.startswith("For `autoimprove`"))
    assert "references/experiments.md" in routing


def test_audit_lens_has_prose_row_citing_long_sentences() -> None:
    row = next(r for r in _table(SKILL.read_text(), "Lens") if r[0].startswith("**Prose (ASD-STE100)"))
    assert "inspect_skill.py" in row[1] + row[2]
    assert "long_sentences" in row[2]
    assert "passive" in row[2]
    assert "multi-instruction" in row[2]


@pytest.mark.parametrize("mode", ["add", "improve"])
def test_add_improve_steps_run_inspector_and_rewrite_long_sentences(mode: str) -> None:
    section = _section(SKILL.read_text(), f"Mode: {mode}")
    steps = cast(list[str], re.findall(r"^\d+\. .*?(?=^\d+\. |^Done means|\Z)", section, re.M | re.S))
    step = next(text for text in steps if "inspect_skill.py" in text)
    assert "changed prose" in step
    assert f"over {MAX_WORDS} words" in step
    assert "before you report" in step


def test_no_contract_step_offers_judge_only_or_draft_contract() -> None:
    section = _section(EXPERIMENTS.read_text(), "No contract")
    assert "judge-only" in section
    assert "powerful model" in section
    assert "draft" in section and '"status": "draft"' in section
    assert "approves" in section


def _long(path: Path) -> list[dict[str, int]]:
    lines = path.read_text().splitlines()
    start = 0
    if lines and lines[0] == "---":
        start = next(i for i, line in enumerate(lines[1:], 1) if line == "---") + 1
    long_sentences = cast(Callable[[list[str], int], list[dict[str, int]]], getattr(_inspector(), "long_sentences"))
    return [hit for hit in long_sentences(lines, start) if hit["words"] > MAX_WORDS]


PROSE_FILES = [SKILL, *sorted((SKILL_DIR / "references").glob("*.md"))]


@pytest.mark.parametrize("path", PROSE_FILES, ids=[path.name for path in PROSE_FILES])
def test_skill_prose_has_no_sentence_over_limit(path: Path) -> None:
    assert _long(path) == []

"""Guard tests over the review-bot instructions: the architecture-drift item and the crust path rule."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
INSTRUCTIONS = ROOT / ".github/copilot-instructions.md"
CODERABBIT = ROOT / ".coderabbit.yaml"


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}.*?(?=^## |\Z)", text, re.M | re.S)
    assert match, heading
    return match.group(0)


@pytest.mark.ac("gh-498:AC-1")
def test_what_to_find_names_the_architecture_drift_that_the_layout_tests_cannot_see() -> None:
    section = _section(INSTRUCTIONS.read_text(encoding="utf-8"), "What to find, in priority order")
    item = re.search(r"^\d+\. \*\*Architecture drift\.\*\*.*?(?=^\d+\. |\Z)", section, re.M | re.S)
    assert item, "no Architecture drift item"
    text = " ".join(item.group(0).split())
    for needle in ("Architecture (Sliced Bread)", "AGENTS.md", "mixes lifetimes", "one implementation", "`__init__.py`",
                   "kernel", "`Session`", "`ClaudeCode`", "collaborator"):
        assert needle in text, needle


@pytest.mark.ac("gh-498:AC-1")
def test_what_not_to_flag_carries_the_sliced_bread_growth_guards() -> None:
    section = " ".join(_section(INSTRUCTIONS.read_text(encoding="utf-8"), "What not to flag").split())
    for needle in ("one file", "one consumer", "real pressure", "size alone"):
        assert needle in section, needle


@pytest.mark.ac("gh-498:AC-1")
def test_coderabbit_flags_logic_in_a_crust_init() -> None:
    text = CODERABBIT.read_text(encoding="utf-8")
    entry = re.search(r'- path: "packages/\*/src/\*\*/__init__\.py"\n\s+instructions: >-\n(?P<body>(?:\s{8,}.*\n)+)', text)
    assert entry, "no path_instructions entry for crust __init__.py files"
    body = " ".join(entry.group("body").split())
    assert "re-exports" in body and "Flag logic here" in body, body

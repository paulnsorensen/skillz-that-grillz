from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._contract import Surface
from skillz_experiments._frontmatter import frontmatter_end, top_fields
from skillz_experiments._intake import skill_facts, skill_frontmatter
from skillz_experiments.harness._claude._events import declares
from skillz_experiments.harness._claude._settings import declared_names
from skillz_experiments._cases import CodedError


def test_frontmatter_end_finds_the_closing_delimiter() -> None:
    assert frontmatter_end(["---", "name: a", "---", "body"]) == 2
    assert frontmatter_end(["---", "---"]) == 1


@pytest.mark.parametrize("lines", [[], ["name: a", "---"], ["---", "name: a"], ["---"]])
def test_frontmatter_end_rejects_a_missing_or_unclosed_block(lines: list[str]) -> None:
    assert frontmatter_end(lines) is None


def test_top_fields_reads_crlf_text() -> None:
    assert top_fields("---\r\nname: demo\r\ndescription: Does it.\r\n---\r\nbody\r\n") == {
        "name": "demo", "description": "Does it."}


def test_top_fields_unquotes_values_and_keys_and_keeps_key_case() -> None:
    text = "---\n\"Name\": 'demo'\n? description: \"Does it.\"\n---\n"
    assert top_fields(text) == {"Name": "demo", "description": "Does it."}


def test_top_fields_skips_blank_and_indented_lines_but_keeps_empty_values() -> None:
    text = "---\nname: demo\n\nhooks:\n  name: nested\n  Stop: x\n---\n"
    assert top_fields(text) == {"name": "demo", "hooks": ""}


def test_top_fields_lets_the_last_duplicate_win() -> None:
    assert top_fields("---\nname: first\nname: last\n---\n") == {"name": "last"}


@pytest.mark.parametrize("text", ["", "name: a\n", "---\nname: a\n", "\n---\nname: a\n---\n"])
def test_top_fields_returns_none_without_a_closed_block(text: str) -> None:
    assert top_fields(text) is None


@pytest.mark.skipif(importlib.util.find_spec("yaml") is None, reason="pyyaml is not installed")
def test_top_fields_agrees_with_yaml_on_simple_scalars() -> None:
    import yaml
    body = "name: demo\ndescription: Does a demo.\nname: final\n"
    loaded = cast(dict[str, object], yaml.safe_load(body))
    assert top_fields(f"---\n{body}---\n") == {key: str(value) for key, value in loaded.items()}


def test_declares_accepts_a_matching_skill_and_lowercases_keys() -> None:
    assert declares("---\nName: demo\ndescription: x\n---\n", "demo")
    assert not declares("---\nname: demo\ndescription: x\nhooks:\n  Stop: y\n---\n", "demo")
    assert not declares("name: demo\ndescription: x\n", "demo")


def test_skill_facts_reads_a_crlf_skill_md(tmp_path: Path) -> None:
    _ = (tmp_path / "SKILL.md").write_bytes(b"---\r\nname: crlf-name\r\ndescription: Does it.\r\n---\r\nbody\r\n")
    facts = skill_facts(tmp_path)
    assert facts["name"] == "crlf-name" and facts["description"] == "Does it."


def test_skill_facts_reads_a_quoted_key(tmp_path: Path) -> None:
    _ = (tmp_path / "SKILL.md").write_text('---\n"name": quoted-name\n---\nbody\n', encoding="utf-8")
    assert skill_facts(tmp_path)["name"] == "quoted-name"


def test_skill_frontmatter_drops_empty_values(tmp_path: Path) -> None:
    _ = (tmp_path / "SKILL.md").write_text("---\nname: demo\nhooks:\n  Stop: x\n---\n", encoding="utf-8")
    assert skill_frontmatter(tmp_path) == {"name": "demo"}


def agent_names(text: str) -> frozenset[str]:
    return declared_names(Surface(agents=("agents/reviewer.md",)), {"agents/reviewer.md": text}).agents


def test_declared_names_takes_the_last_duplicate_agent_name() -> None:
    assert agent_names("---\nname: other\nname: reviewer\n---\n") == {"reviewer"}
    with pytest.raises(CodedError) as error:
        _ = agent_names("---\nname: reviewer\nname: other\n---\n")
    assert error.value.code == "contract-surface"

from __future__ import annotations

import pytest

from skillz_experiments.harness._claude._events import declares
from skillz_experiments.harness._claude._settings import _frontmatter_name  # pyright: ignore[reportPrivateUsage]


def _skill(*fields: str) -> str:
    return "---\n" + "".join(f"{field}\n" for field in fields) + "---\nBody.\n"


def test_declares_accepts_valid_frontmatter() -> None:
    assert declares(_skill("name: echo", "description: Say it"), "echo")


@pytest.mark.parametrize("name", ['"echo"', "'echo'", "echo  # the skill", '"echo"  # the skill'])
def test_declares_reads_quoted_and_commented_names(name: str) -> None:
    assert declares(_skill(f"name: {name}", "description: Say it"), "echo")


def test_declares_ignores_comment_lines() -> None:
    assert declares(_skill("# hooks: none", "name: echo", "description: Say it"), "echo")


@pytest.mark.parametrize("text", [
    "name: echo\ndescription: x\n",
    "---\nname: echo\ndescription: x\n",
    _skill("name: echo", "description: x", "bad: [unclosed"),
    _skill("- echo"),
])
def test_declares_rejects_unreadable_frontmatter(text: str) -> None:
    assert not declares(text, "echo")


def test_declares_rejects_a_description_that_is_only_a_comment() -> None:
    assert not declares(_skill("name: echo", "description: # todo"), "echo")


def test_declares_rejects_an_empty_block_scalar_description() -> None:
    assert not declares(_skill("name: echo", "description: >-"), "echo")


def test_declares_rejects_hooks_and_user_invocable() -> None:
    assert not declares(_skill("name: echo", "description: x", "hooks: {}"), "echo")
    assert not declares(_skill("name: echo", "description: x", "user-invocable: false"), "echo")


def test_frontmatter_name_reads_valid_and_quoted_names() -> None:
    assert _frontmatter_name(_skill("name: echo")) == "echo"
    assert _frontmatter_name(_skill('name: "echo"')) == "echo"


def test_frontmatter_name_drops_a_trailing_comment() -> None:
    assert _frontmatter_name(_skill('name: "echo"  # the skill')) == "echo"
    assert _frontmatter_name(_skill("# name: other", "name: echo")) == "echo"


@pytest.mark.parametrize("text", ["name: echo\n", _skill("description: x"), _skill("name: [unclosed"), _skill('name: "echo')])
def test_frontmatter_name_is_none_without_a_readable_name(text: str) -> None:
    assert _frontmatter_name(text) is None


def test_declares_reads_a_block_scalar_body_as_the_description() -> None:
    assert declares("---\nname: echo\ndescription: |-\n  Say it\n  twice\nother: x\n---\n", "echo")
    assert not declares("---\nname: echo\ndescription: >\n\nother: x\n---\n", "echo")


def test_declares_keeps_a_stray_quote_inside_an_unquoted_value() -> None:
    assert declares(_skill("name: echo", "description: it's fine"), "echo")
    assert not declares(_skill("name: \"echo'", "description: x"), "echo")

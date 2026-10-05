from __future__ import annotations

import json
from typing import cast

import pytest

from skillz_experiments._search import Mode
from skillz_experiments._workflow import _reflection_request  # pyright: ignore[reportPrivateUsage]

BRIEF = "Offload the link check.\nUse only the standard library."


@pytest.mark.parametrize("mode, components", [
    ("prompt", ["SKILL.md"]), ("prompt-cli", ["SKILL.md", "scripts/inspect_skill.py"]),
    ("cli", ["scripts/inspect_skill.py"]), ("wedge", ["SKILL.md", "wedge-files"])])
def test_every_reflection_request_requires_ste_prose(mode: Mode, components: list[str], limits: tuple[int, int]) -> None:
    advisory, maximum = limits
    prompt, _ = _reflection_request(mode, {name: "text" for name in components}, {}, components, "echo-skill")
    assert "ASD-STE100" in prompt
    assert f"at most {advisory} words per procedural sentence" in prompt
    assert f"at most {maximum} words per descriptive sentence" in prompt
    payload = cast(dict[str, object], json.loads(prompt.split("\n", 1)[1]))
    assert payload["candidate"] == {name: "text" for name in components}


def test_cli_request_names_the_component_it_edits() -> None:
    prompt, _ = _reflection_request("cli", {"scripts/other.py": "x"}, {}, ["scripts/other.py"], "echo-skill")
    assert "Improve only scripts/other.py" in prompt


def test_wedge_brief_text_is_in_the_request_before_the_json_line() -> None:
    components = ["SKILL.md", "wedge-files"]
    prompt, schema = _reflection_request("wedge", {"SKILL.md": "s", "wedge-files": "{}"}, {}, components, "echo-skill", BRIEF)
    assert BRIEF in prompt
    assert schema["required"] == components
    assert ".agents/skills/echo-skill/" in prompt
    assert "<skill>" not in prompt
    head, last = prompt.rsplit("\n", 1)
    assert BRIEF in head
    assert cast(dict[str, object], json.loads(last))["candidate"] == {"SKILL.md": "s", "wedge-files": "{}"}

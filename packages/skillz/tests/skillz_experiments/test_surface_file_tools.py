"""gh-702: a surface file tool such as `Write` or `Edit` may write in the workspace, and nowhere else.

Claude Code 2.1.319 checked live (owner-approved): with these rules a Write inside the workspace succeeds, a Write into
`.claude/` or `.agents/` is denied by the settings, and a Write outside the workspace is not granted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._contract import Surface
from test_claude import surface_call

pytestmark = pytest.mark.usefixtures("host_login", "claude_in_process")


def _permissions(call: dict[str, object]) -> dict[str, list[str]]:
    return cast(dict[str, dict[str, list[str]]], json.loads(cast(str, call["settings"])))["permissions"]


@pytest.mark.ac("gh-702:AC-1")
@pytest.mark.parametrize("tools", [("Write", "Edit"), ("Edit",), ("NotebookEdit",), ("MultiEdit",)])
def test_a_surface_file_tool_may_write_only_in_the_workspace(tmp_path: Path, tools: tuple[str, ...]) -> None:
    call = surface_call(tmp_path, Surface(tools))
    argv, workspace = cast(list[str], call["argv"]), Path(cast(str, call["cwd"]))
    assert all(tool in argv[argv.index("--tools") + 1].split(",") for tool in tools)
    permissions = _permissions(call)
    forms = sorted({str(workspace), str(workspace.resolve())})
    assert [rule for rule in permissions["allow"] if rule.startswith("Edit(")] == [f"Edit(/{form}/**)" for form in forms]
    for form in forms:
        assert {f"Edit(/{form}/.agents/**)", f"Edit(/{form}/.claude/**)"} <= set(permissions["deny"])
    assert not any(rule in ("Edit", "Write") or rule.startswith("Write") for rule in permissions["allow"])


@pytest.mark.ac("gh-702:AC-2")
@pytest.mark.parametrize("surface", [None, Surface(("WebFetch",))])
def test_without_a_surface_file_tool_no_edit_rule_is_added(tmp_path: Path, surface: Surface | None) -> None:
    permissions = _permissions(surface_call(tmp_path, surface))
    assert not any(rule.startswith(("Edit", "Write")) for rule in [*permissions["allow"], *permissions["deny"]])

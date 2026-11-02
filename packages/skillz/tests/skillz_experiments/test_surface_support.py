"""Each adapter runs only the contract surface parts that it supports; the harness refuses the rest by name."""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments._cases import CodedError
from skillz_experiments._contract import Surface
from skillz_experiments.harness import Configuration, Harness
from skillz_experiments.harness import _roles
from skillz_experiments.harness._codex import Codex

FULL = Surface(("WebFetch",), "mcp/docs.json", ("agents/reviewer.md",), ("skills/helper-skill",))


@pytest.fixture
def codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point CODEX_HOME at a private directory for a test that starts a Codex role. The autouse stub `codex` never runs."""
    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))


def _harness(tmp_path: Path, document: dict[str, object], surface: Surface) -> Harness:
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1} | document))
    return Configuration.load(config, "m").create("m", Budget(10, 120, 0), lambda: None, None, surface, {})


def _refusal(tmp_path: Path, document: dict[str, object], surface: Surface) -> str:
    with pytest.raises(CodedError) as caught:
        _ = _harness(tmp_path, document, surface)
    assert caught.value.code == "contract-surface-unsupported"
    assert str(caught.value).endswith("; the claude harness runs every part, so run with --harness claude")
    return str(caught.value)


@pytest.mark.usefixtures("codex_home")
def test_a_codex_task_role_starts_with_a_surface_of_native_edit_tools(tmp_path: Path) -> None:
    session = _harness(tmp_path, {"adapter": "codex"}, Surface(("Write", "Edit", "MultiEdit")))
    try:
        assert {name: transport.role for name, transport in session.transports.items()} == {
            "task": "task", "reflection": "reflection", "judge": "judge"}
    finally:
        session.close()


def test_a_codex_task_role_refuses_a_tool_that_it_does_not_grant_and_names_only_that_tool(tmp_path: Path) -> None:
    message = _refusal(tmp_path, {"adapter": "codex"}, Surface(("Write", "WebFetch")))
    assert "tool WebFetch" in message and "tool Write" not in message and "codex task role" in message


@pytest.mark.parametrize(("surface", "part"), [
    (Surface(("Write",), mcp_config="mcp/docs.json"), "mcp_config mcp/docs.json"),
    (Surface(("Edit",), agents=("agents/reviewer.md",)), "agent agents/reviewer.md"),
    (Surface(companions=("skills/helper-skill",)), "companion skills/helper-skill"),
])
def test_a_codex_task_role_refuses_an_mcp_config_an_agent_or_a_companion_by_name(
        tmp_path: Path, surface: Surface, part: str) -> None:
    message = _refusal(tmp_path, {"adapter": "codex"}, surface)
    assert part in message and "tool " not in message


def test_a_command_task_role_refuses_every_surface_part_even_a_native_edit_tool(tmp_path: Path) -> None:
    wrapper = tmp_path / "wrapper.py"
    _ = wrapper.write_text("")
    document: dict[str, object] = {"adapter": "command", "identity": "fixture", "command": [sys.executable, str(wrapper)]}
    message = _refusal(tmp_path, document, Surface(("Write",), "mcp/docs.json"))
    assert "tool Write, mcp_config mcp/docs.json" in message and "command task role" in message


def test_each_adapter_names_the_surface_parts_that_it_cannot_run_in_contract_order() -> None:
    unsupported = _roles.unsupported_surface
    assert unsupported("claude", FULL) == []
    assert unsupported("codex", Surface(("Write", "Edit", "MultiEdit"))) == []
    assert unsupported("codex", FULL) == [
        "tool WebFetch", "mcp_config mcp/docs.json", "agent agents/reviewer.md", "companion skills/helper-skill"]
    assert unsupported("command", Surface(("Edit",))) == ["tool Edit"]


def test_the_check_names_every_part_of_the_contract_surface() -> None:
    """A new `Surface` part must join `unsupported_surface`, or a Codex or command task role starts without it."""
    assert [field.name for field in dataclasses.fields(Surface)] == ["tools", "mcp_config", "agents", "companions"]


def test_the_check_follows_the_task_role_when_a_codex_task_overrides_a_claude_harness(tmp_path: Path) -> None:
    message = _refusal(tmp_path, {"adapter": "claude", "roles": {"task": {"adapter": "codex"}}}, FULL)
    assert "codex task role" in message and "tool WebFetch" in message


@pytest.mark.usefixtures("codex_home", "host_login")
def test_a_claude_task_role_runs_the_full_surface_beside_codex_judge_and_reflection_roles(tmp_path: Path) -> None:
    document: dict[str, object] = {"adapter": "codex", "roles": {"task": {"adapter": "claude"}}}
    session = _harness(tmp_path, document, Surface(("WebFetch",)))
    try:
        assert {name: type(transport).__name__ for name, transport in session.transports.items()} == {
            "task": "ClaudeCode", "reflection": "Codex", "judge": "Codex"}
    finally:
        session.close()


@pytest.mark.parametrize(("surface", "refused"), [
    (Surface(("write",)), "tool write"),
    (Surface(("Bash",)), "tool Bash"),
])
def test_a_codex_task_role_matches_tool_names_exactly(tmp_path: Path, surface: Surface, refused: str) -> None:
    message = _refusal(tmp_path, {"adapter": "codex"}, surface)
    assert refused in message and "tool Write" not in message


@pytest.mark.usefixtures("codex_home")
def test_a_failed_later_role_closes_the_codex_roles_that_already_started(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[object] = []
    closed: list[object] = []
    original_init = Codex.__init__
    original_close = Codex.close

    def init(self: Codex, *args: object, **kwargs: object) -> None:
        if len(started) == 2:
            raise OSError("no third role")
        original_init(self, *args, **kwargs)  # pyright: ignore[reportArgumentType]
        started.append(self)

    def close(self: Codex) -> None:
        closed.append(self)
        original_close(self)

    monkeypatch.setattr(Codex, "__init__", init)
    monkeypatch.setattr(Codex, "close", close)
    with pytest.raises(OSError, match="no third role"):
        _ = _harness(tmp_path, {"adapter": "codex"}, Surface(("Edit",)))
    assert len(started) == 2 and {id(item) for item in closed} == {id(item) for item in started}

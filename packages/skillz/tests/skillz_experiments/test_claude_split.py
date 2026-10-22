from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments.harness._claude import _preflight, _transport
from skillz_experiments.harness._claude._preflight import Preflight
from skillz_experiments.harness._claude._transport import ClaudeCode


def _function(module: object, owner: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(Path(cast(str, getattr(module, "__file__"))).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == owner:
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name == name:
                    return member
    raise AssertionError(f"{owner}.{name} not found")


def _names(function: ast.FunctionDef) -> set[str]:
    names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
    return names | {argument.arg for argument in ast.walk(function) if isinstance(argument, ast.arg)}


@pytest.mark.ac("gh-396:AC-1")
def test_claude_code_preflight_delegates_to_the_preflight_collaborator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    function = _function(_transport, "ClaudeCode", "preflight")
    returns = [node for node in function.body if isinstance(node, ast.Return)]
    assert len(function.body) == 1 and len(returns) == 1
    assert ast.unparse(cast(ast.expr, returns[0].value)) == "Preflight(self).run()"
    seen: list[ClaudeCode] = []

    def run(self: Preflight) -> dict[str, object]:
        seen.append(self.claude)
        return {"delegated": True}
    monkeypatch.setattr(Preflight, "run", run)
    adapter = ClaudeCode("m", Budget(10, 120, 0), lambda: None, Path("/usr/bin/true"), out=tmp_path)
    try:
        assert adapter.preflight() == {"delegated": True}
    finally:
        adapter.close()
    assert seen == [adapter]


@pytest.mark.ac("gh-396:AC-1")
def test_the_preflight_module_does_not_import_transport_at_runtime() -> None:
    tree = ast.parse(Path(cast(str, getattr(_preflight, "__file__"))).read_text())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom | ast.Import):
            assert "_transport" not in ast.unparse(node)


@pytest.mark.ac("gh-396:AC-5")
def test_preflight_has_no_reached_or_socket_open_verdict_chain() -> None:
    assert not _names(_function(_transport, "ClaudeCode", "preflight")) & {"reached", "socket_open"}
    assert not _names(_function(_preflight, "Preflight", "run")) & {"reached", "socket_open"}

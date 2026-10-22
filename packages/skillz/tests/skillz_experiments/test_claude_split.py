from __future__ import annotations

import ast
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast, final

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments._cases import CodedError
from skillz_experiments._runtime import Live
from skillz_experiments.evaluation import Reply, Transcript
from skillz_experiments.harness._claude import _journey, _preflight, _transport
from skillz_experiments.harness._claude._journey import Exit, TurnRunner
from skillz_experiments.harness._claude._preflight import Preflight
from skillz_experiments.harness._claude._settings import Inventory
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


@pytest.mark.ac("gh-396:AC-5")
def test_converse_has_no_closed_or_ended_flag_and_preflight_has_no_verdict_flags() -> None:
    converse = _names(_function(_transport, "ClaudeCode", "converse"))
    preflight = _names(_function(_transport, "ClaudeCode", "preflight"))
    assert not converse & {"closed", "ended"}
    assert not preflight & {"reached", "socket_open"}
    run = _names(_function(_journey, "TurnRunner", "run"))
    assert not run & {"closed", "ended"}
    assert not _names(_function(_preflight, "Preflight", "run")) & {"reached", "socket_open"}


@pytest.mark.ac("gh-396:AC-1")
@pytest.mark.ac("gh-396:AC-4")
@pytest.mark.parametrize("module", [_preflight, _journey])
def test_the_split_modules_do_not_import_transport_at_runtime(module: object) -> None:
    tree = ast.parse(Path(cast(str, getattr(module, "__file__"))).read_text())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom | ast.Import):
            assert "_transport" not in ast.unparse(node)


@final
class _Child:
    def __init__(self, turns: list[tuple[list[str], bool]]) -> None:
        self.turns = turns
        self.sent: list[str] = []

    def turn(self, line: str, remaining: float, is_result: Callable[[str], bool]) -> tuple[list[str], bool]:
        del remaining, is_result
        self.sent.append(line)
        return self.turns.pop(0)


def _init(*skills: str) -> str:
    return json.dumps({"type": "system", "subtype": "init", "skills": list(skills), "tools": ["Bash"], "agents": [],
                       "mcp_servers": [], "plugins": []}) + "\n"


def _result(*, error: bool = False) -> str:
    return json.dumps({"type": "result", "is_error": error, "result": "done"}) + "\n"


def _runner(turns: list[tuple[list[str], bool]], replies: list[Reply], max_turns: int) -> tuple[TurnRunner, _Child]:
    inventory = Inventory(frozenset(), frozenset())

    def adopt(events: list[dict[str, object]], expected: list[str], current: Inventory, declared: object) -> Inventory:
        del events, expected, declared
        return current
    claude = SimpleNamespace(
        budget=Budget(10, 120, 0), checkpoint=lambda: None, role="task", declared=SimpleNamespace(skills=frozenset()),
        _late=SimpleNamespace(adopt=adopt), inventory=lambda: inventory)
    child = _Child(turns)

    def simulate(transcript: Transcript) -> Reply:
        del transcript
        return replies.pop(0)
    runner = TurnRunner(cast(ClaudeCode, cast(object, claude)), cast(Live, cast(object, child)), expected=["cand"],
                        builtin=frozenset(), request="hi", shown="hi", max_turns=max_turns, holdout=False, simulate=simulate)
    return runner, child


@pytest.mark.ac("gh-396:AC-4")
@pytest.mark.parametrize(("turns", "replies", "max_turns", "expected", "sent"), cast(list[tuple[list[tuple[list[str], bool]], list[Reply], int, Exit, int]], [
    ([([_init("cand"), _result()], True), ([_result()], True)], [Reply("more", False), Reply("", True)], 5, Exit.DONE, 2),
    ([([_init("cand"), _result()], True), ([_result()], True)], [Reply("more", False)], 2, Exit.MAX_TURNS, 2),
    ([([_init(), _result()], True)], [], 5, Exit.NOT_LOADED, 1),
    ([([_init("cand"), _result()], True), ([], False)], [Reply("more", False)], 5, Exit.PROCESS_DIED, 2),
]))
def test_the_turn_runner_ends_with_exactly_one_exit(turns: list[tuple[list[str], bool]], replies: list[Reply],
                                                   max_turns: int, expected: Exit, sent: int) -> None:
    runner, child = _runner(turns, replies, max_turns)
    assert runner.run() is expected
    assert len(child.sent) == sent and not replies
    assert len(runner.transcript) == (2 * sent if expected is not Exit.PROCESS_DIED else 2)


@pytest.mark.ac("gh-396:AC-4")
def test_the_turn_runner_keeps_raises_as_exceptions() -> None:
    runner, _ = _runner([([_init("cand"), _result(error=True)], True)], [], 3)
    with pytest.raises(RuntimeError, match="error-result \\(turn 1"):
        _ = runner.run()
    runner, _ = _runner([([_result()], True)], [], 3)
    with pytest.raises(CodedError, match="turn 1 has no init event"):
        _ = runner.run()
    runner, _ = _runner([([_init("cand"), _result()], True), ([_init("cand", "extra"), _result()], True)],
                        [Reply("more", False)], 3)
    with pytest.raises(CodedError, match="inventory changed in turn 2"):
        _ = runner.run()

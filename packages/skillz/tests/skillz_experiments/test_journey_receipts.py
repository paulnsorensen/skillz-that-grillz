"""The scaffold journey gate checks ordered receipts over one step shape for Claude and Codex traces."""
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from skillz_experiments._journey_gate import Matcher, Step, missing_receipt, steps

ROOT = Path(__file__).resolve().parents[4]
SOURCE = (ROOT / "packages/skillz/src/skillz_experiments/_journey_gate.py").read_text(encoding="utf-8")
RECEIPT_LINE = "RECEIPTS: list[Matcher] = []"
TERMINAL_LINE = "TERMINAL: Matcher | None = None"
RECEIPT_SRC = ('RECEIPTS: list[Matcher] = [Matcher("edited", tool="Edit", input="SKILL[.]md$"), '
               'Matcher("validated", tool="Bash", input="validate"), Matcher("summary", text="done")]')
TERMINAL_SRC = 'TERMINAL: Matcher | None = Matcher("publish", tool="Bash", input="git push")'

RECEIPTS = [Matcher("edited", tool="Edit", input="SKILL[.]md$"), Matcher("validated", tool="Bash", input="validate"),
            Matcher("summary", text="done")]
TERMINAL = Matcher("publish", tool="Bash", input="git push")

Flow = list[tuple[str, str]]
GOOD: Flow = [("text", "start"), ("edit", "/s/SKILL.md"), ("cmd", "ls"), ("cmd", "just validate"), ("text", "all done"),
              ("cmd", "git push")]


def _claude(kind: str, value: str) -> str:
    if kind == "text":
        block: dict[str, object] = {"type": "text", "text": value}
    elif kind == "edit":
        block = {"type": "tool_use", "name": "Edit", "input": {"file_path": value}}
    else:
        block = {"type": "tool_use", "name": "Bash", "input": {"command": value}}
    return json.dumps({"type": "assistant", "message": {"content": [block]}})


def _codex(kind: str, value: str) -> str:
    if kind == "text":
        item: dict[str, object] = {"type": "agentMessage", "text": value}
    elif kind == "edit":
        item = {"type": "fileChange", "changes": [{"path": value}]}
    else:
        item = {"type": "commandExecution", "id": "c", "exitCode": 0, "command": value}
    return json.dumps({"method": "item/completed", "params": {"item": item}})


RENDER: dict[str, Callable[[str, str], str]] = {"claude": _claude, "codex": _codex}


def _lines(harness: str, flow: Flow) -> list[str]:
    return [RENDER[harness](kind, value) for kind, value in flow]


def _flat(harness: str, flow: Flow) -> list[Step]:
    return [step for line in _lines(harness, flow) for step in steps(cast(object, json.loads(line)))]


def _run(directory: Path, lines: list[str], source: str = SOURCE) -> dict[str, object]:
    (directory / "trace").mkdir()
    _ = (directory / "trace/events.jsonl").write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    done = subprocess.run([sys.executable, "-I", "-c", source], cwd=directory, capture_output=True, text=True, check=True)
    return cast(dict[str, object], json.loads(done.stdout))


def _authored() -> str:
    return SOURCE.replace(RECEIPT_LINE, RECEIPT_SRC).replace(TERMINAL_LINE, TERMINAL_SRC)


@pytest.mark.ac("gh-651:AC-13")
def test_both_harnesses_normalize_to_the_same_steps() -> None:
    assert _flat("claude", GOOD) == _flat("codex", GOOD)
    assert steps({"type": "user"}) == [] and steps({"method": "item/completed", "params": {"item": {"type": "x"}}}) == []
    other = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"b": 1, "a": 2}}]}}
    assert steps(other) == [Step("Read", '{"a": 2, "b": 1}', None)]


@pytest.mark.ac("gh-651:AC-13")
@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_the_same_receipts_pass_on_both_harnesses(harness: str) -> None:
    assert missing_receipt(_flat(harness, GOOD), RECEIPTS, TERMINAL) is None


@pytest.mark.ac("gh-651:AC-14")
@pytest.mark.parametrize("harness", ["claude", "codex"])
@pytest.mark.parametrize(("skip", "name"), [("edit", "edited"), ("just validate", "validated"), ("all done", "summary")])
def test_a_skipped_receipt_is_reported_by_name(harness: str, skip: str, name: str) -> None:
    flow = [step for step in GOOD if skip not in step]
    assert missing_receipt(_flat(harness, flow), RECEIPTS, TERMINAL) == name


@pytest.mark.ac("gh-651:AC-14")
def test_only_the_first_missing_receipt_is_reported() -> None:
    flow: Flow = [("cmd", "ls")]
    assert missing_receipt(_flat("claude", flow), RECEIPTS, None) == "edited"


@pytest.mark.ac("gh-651:AC-15")
@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_receipt_after_the_terminal_step_does_not_count(harness: str) -> None:
    flow: Flow = [("edit", "/s/SKILL.md"), ("cmd", "git push"), ("cmd", "just validate"), ("text", "done")]
    assert missing_receipt(_flat(harness, flow), RECEIPTS, TERMINAL) == "validated"
    assert missing_receipt(_flat(harness, flow), RECEIPTS, None) is None
    assert missing_receipt(_flat(harness, GOOD[:-1]), RECEIPTS, TERMINAL) is None


@pytest.mark.ac("gh-651:AC-15")
def test_receipts_out_of_order_fail_and_unrelated_steps_between_are_allowed() -> None:
    swapped: Flow = [("cmd", "just validate"), ("edit", "/s/SKILL.md"), ("text", "done")]
    assert missing_receipt(_flat("claude", swapped), RECEIPTS, TERMINAL) == "validated"
    spaced: Flow = [("edit", "/s/SKILL.md"), ("cmd", "a"), ("text", "b"), ("cmd", "just validate"), ("cmd", "c"),
                    ("text", "done")]
    assert missing_receipt(_flat("codex", spaced), RECEIPTS, TERMINAL) is None


@pytest.mark.ac("gh-651:AC-16")
def test_a_matcher_needs_every_given_field_on_one_step() -> None:
    step = Step("Bash", "just validate", None)
    assert missing_receipt([step], [Matcher("m", tool="Bash", input="validate")], None) is None
    assert missing_receipt([step], [Matcher("m", tool="Edit", input="validate")], None) == "m"
    assert missing_receipt([step], [Matcher("m", tool="Bash", input="nope")], None) == "m"
    assert missing_receipt([step], [Matcher("m", text="validate")], None) == "m"


@pytest.mark.ac("gh-651:AC-16")
@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_the_real_gate_enforces_an_author_edited_receipt_list(tmp_path: Path, harness: str) -> None:
    assert _run(tmp_path, _lines(harness, GOOD), _authored()) == {"score": 1, "failed": []}
    broken = tmp_path / "b"
    broken.mkdir()
    flow = [step for step in GOOD if "validate" not in step[1]]
    assert _run(broken, _lines(harness, flow), _authored()) == {"score": 0, "failed": ["receipt-missing:validated"]}


@pytest.mark.ac("gh-651:AC-17")
def test_the_default_gate_ignores_receipts(tmp_path: Path) -> None:
    flow: Flow = [("text", "hello")]
    assert _run(tmp_path, _lines("claude", flow)) == {"score": 1, "failed": []}
    other = tmp_path / "n"
    other.mkdir()
    assert _run(other, ["[1]"]) == {"score": 0, "failed": ["no-agent-turn"]}

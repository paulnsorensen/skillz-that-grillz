from __future__ import annotations

from skillz_experiments.evaluation import CheckMemo

import json
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from typing_extensions import override

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError, load_cases
from skillz_experiments._contract import Contract, parse
from skillz_experiments.evaluation._evaluator import SIMULATOR_SCHEMA, Conversant, Reply, Transcript, task_tokens
from skillz_experiments.harness import Configuration, Harness
from skillz_experiments.harness._claude import ClaudeCode
from skillz_experiments.harness._roles import Role
from skillz_experiments._budget import Budget
from fake_provider import FakeProvider

FAKE = Path(__file__).parent / "fixtures/fake_claude.py"
BRIEF = "BRIEF_SENTINEL: wants a short plan."
RUBRIC = "RUBRIC_SENTINEL: did the agent ask about scope?"
EXPECTED = "EXPECTED_SENTINEL"
GATE = 'import json;print(json.dumps({"score": 1}))'
REQUEST = "Help me plan the work."
TOKENS = {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 2}
pytestmark = pytest.mark.usefixtures("host_login")


def contract() -> Contract:
    return parse({"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
                  "kinds": {"journey": {"grader": "journey", "argv": ["python3", "-I", "-c", GATE], "rubric": RUBRIC}}},
                 "skill")


def journey_case(tmp_path: Path, rules: Contract, max_turns: int = 8) -> tuple[Case, Candidate]:
    item = {"id": "j1", "family": "j", "split": "train", "kind": "journey", "request": REQUEST, "brief": BRIEF,
            "max_turns": max_turns, "files": {"input.txt": "hello\n"}, "expected": {"secret": EXPECTED},
            "provenance": "test", "provider_approved": True}
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": [item]}))
    return load_cases(path, rules.grader_types())[0], Candidate({"SKILL.md": "seed"}, ("SKILL.md",), contract=rules)


def fake_claude(tmp_path: Path, mode: str = "ok") -> Path:
    executable = tmp_path / "bin" / "claude"
    executable.parent.mkdir()
    _ = shutil.copyfile(FAKE, executable)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    _ = executable.with_name("claude.mode").write_text(mode)
    return executable


def harness(tmp_path: Path, executable: Path) -> Harness:
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(executable)]}))
    return Configuration.load(config, "claude-test-model").create("claude-test-model", Budget(20, 120, 0), lambda: None)


def received(executable: Path) -> list[str]:
    log = executable.with_name("claude.journey.log")
    rows = [cast(dict[str, object], json.loads(line)) for line in log.read_text().splitlines()] if log.is_file() else []
    return [cast(str, row["message"]) for row in rows]


def run(tmp_path: Path, mode: str, max_turns: int, replies: list[Reply]) -> tuple[Path, dict[str, object], list[Transcript], Budget]:
    executable = fake_claude(tmp_path, mode)
    rules = contract()
    case, candidate = journey_case(tmp_path, rules, max_turns)
    session = harness(tmp_path, executable)
    seen: list[Transcript] = []

    def simulate(transcript: Transcript) -> Reply:
        seen.append([dict(row) for row in transcript])
        return replies[len(seen) - 1]

    try:
        task = session.transports["task"]
        assert isinstance(task, ClaudeCode) and isinstance(task, Conversant)
        result = task.converse("PREFIX " + REQUEST, candidate, case, False, simulate)
        return executable, result, seen, task.budget
    finally:
        session.close()


@pytest.mark.parametrize(("mode", "max_turns", "replies", "messages", "asked", "turns", "claims"), [
    ("ok", 8, [Reply("r1", False), Reply("", True)], ["PREFIX " + REQUEST, "r1"], 2, 2, 2),
    ("ok", 2, [Reply("r1", False)], ["PREFIX " + REQUEST, "r1"], 1, 2, 2),
    ("ok", 1, [], ["PREFIX " + REQUEST], 0, 1, 1),
], ids=["done", "max-turns", "one-turn"])
def test_converse_stream_loop_stops_on_done_and_max_turns(
        tmp_path: Path, mode: str, max_turns: int, replies: list[Reply], messages: list[str], asked: int, turns: int,
        claims: int) -> None:
    executable, result, seen, budget = run(tmp_path, mode, max_turns, replies)
    assert received(executable) == messages
    assert len(seen) == asked
    transcript = cast(Transcript, result["transcript"])
    assert [row["role"] for row in transcript] == ["user", "assistant"] * turns
    assert transcript[0] == {"role": "user", "text": REQUEST}
    assert transcript[1] == {"role": "assistant", "text": "agent-1"}
    assert budget.calls == claims
    events = cast(list[dict[str, object]], result["raw_events"])
    assert sum(event.get("type") == "result" for event in events) == turns
    assert sum(event.get("type") == "system" for event in events) == turns


def test_converse_stream_loop_sends_the_stream_json_argv_and_user_message_lines(tmp_path: Path) -> None:
    executable, result, _, _ = run(tmp_path, "ok", 2, [Reply("second", False)])
    call = [cast(dict[str, object], json.loads(line)) for line in executable.with_name("claude.log").read_text().splitlines()][-1]
    argv = cast(list[str], call["argv"])
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--no-session-persistence" in argv and "--json-schema" not in argv
    log = [cast(dict[str, object], json.loads(line)) for line in executable.with_name("claude.journey.log").read_text().splitlines()]
    assert [row["line"] for row in log] == [{"type": "user", "message": {"role": "user", "content": text}}
                                            for text in ("PREFIX " + REQUEST, "second")]
    assert cast(dict[str, object], result["answer"])["result_json"] == "agent-2"
    assert result["usage"] == {"input_tokens": 70, "cached_input_tokens": 40, "output_tokens": 14}


def test_converse_stream_loop_keeps_one_workspace_and_removes_it_after_the_journey(tmp_path: Path) -> None:
    executable = fake_claude(tmp_path, "journey-writes")
    rules = contract()
    case, candidate = journey_case(tmp_path, rules, 3)
    session = harness(tmp_path, executable)
    try:
        task = cast(ClaudeCode, session.transports["task"])
        result = task.converse("go", candidate, case, False, lambda _t: Reply("more", False))
    finally:
        session.close()
    assert cast(dict[str, str], result["output_files"])["notes.txt"] == "turn 3\n"
    assert not Path(cast(str, result["workspace"])).exists()
    assert BRIEF not in json.dumps(result["events"])


def test_converse_stream_loop_fails_when_the_process_ends_before_any_result(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Claude Code fails"):
        _ = run(tmp_path, "journey-exit-before-result", 3, [])


def test_turn_inventory_changed_in_a_later_init_event_stops_with_isolation_failed(tmp_path: Path) -> None:
    with pytest.raises(CodedError) as caught:
        _ = run(tmp_path, "journey-init-changes", 5, [Reply("again", False), Reply("again", False)])
    assert caught.value.code == "isolation-failed" and "turn 2" in str(caught.value)


class Judge(FakeProvider):
    def __init__(self, answers: list[dict[str, object]]) -> None:
        self.budget: Budget = Budget(40, 2400, reserve=0)
        self.answers: list[dict[str, object]] = answers
        self.prompts: list[str] = []
        self.schemas: list[dict[str, object] | None] = []

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        assert candidate is None and case is None
        self.budget.claim(holdout=holdout)
        self.prompts.append(prompt)
        self.schemas.append(schema)
        return {"answer": self.answers.pop(0), "usage": TOKENS.copy(), "latency_seconds": 0.1}


class Talker(FakeProvider):
    """A task transport that asks its simulator once and has a sandbox."""

    def __init__(self) -> None:
        self.budget: Budget = Budget(40, 2400, reserve=0)

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str, str]:
        del workspace, argv
        return 0, '{"score": 1}', ""

    def converse(self, request: str, candidate: Candidate, case: Case, holdout: bool,
                 simulate: Callable[[Transcript], Reply]) -> dict[str, object]:
        del request, case, holdout
        transcript: Transcript = [{"role": "user", "text": REQUEST}, {"role": "assistant", "text": "ASSISTANT_SENTINEL one"}]
        _ = simulate(transcript)
        events = [{"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0,
                                                      "command": "cat .agents/skills/echo-skill/SKILL.md"}}]
        return {"answer": {"load_marker": candidate.identity, "result_json": "final"}, "events": events,
                "usage": TOKENS.copy(), "workspace": "/TASK", "latency_seconds": 0.1, "output_files": {"a.txt": "a"},
                "transcript": transcript, "raw_events": []}


def test_simulator_prompt_holds_the_brief_the_request_and_replies_but_never_expected_or_the_rubric(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    judge = Judge([{"reply": "yes", "done": True}, {"score_percent": 60}])
    task = Talker()
    transports = {"task": task, "judge": judge, "reflection": judge}

    def create(self: Role, budget: Budget, checkpoint: object, out: Path | None = None, *rest: object) -> Talker | Judge:
        del checkpoint, out, rest
        transports[self.adapter].budget = budget
        return transports[self.adapter]

    monkeypatch.setattr(Role, "create", create)
    roles = {name: Role(name, "model", ("fake",), "fake-1") for name in ("task", "judge", "reflection")}
    session = Harness(Configuration(roles), Budget(40, 2400, reserve=0), lambda: None)
    case, candidate = journey_case(tmp_path, contract())
    result = session.evaluate(candidate, case, checks=CheckMemo())
    prompt = judge.prompts[0]
    assert judge.schemas[0] == SIMULATOR_SCHEMA
    assert BRIEF in prompt and REQUEST in prompt and "ASSISTANT_SENTINEL one" in prompt
    assert EXPECTED not in prompt and "RUBRIC_SENTINEL" not in prompt and "secret" not in prompt
    assert "untrusted data" in prompt
    assert judge.prompts[1].count(RUBRIC) == 1 and BRIEF in judge.prompts[1]
    assert result["score"] == 0.6


@pytest.mark.parametrize("sim_input", [10, None])
def test_simulator_tokens_are_recorded_apart_and_never_charged_as_task_tokens(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sim_input: int | None) -> None:
    class Simulating(Judge):
        @override
        def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
                   *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
            result = super().invoke(prompt, candidate, case, holdout=holdout, schema=schema)
            if schema == SIMULATOR_SCHEMA:
                result["usage"] = TOKENS | {"input_tokens": sim_input}
            return result

    judge = Simulating([{"reply": "yes", "done": True}, {"score_percent": 60}])
    task = Talker()
    transports = {"task": task, "judge": judge, "reflection": judge}

    def create(self: Role, budget: Budget, checkpoint: object, out: Path | None = None, *rest: object) -> Talker | Judge:
        del checkpoint, out, rest
        transports[self.adapter].budget = budget
        return transports[self.adapter]

    monkeypatch.setattr(Role, "create", create)
    roles = {name: Role(name, "model", ("fake",), "fake-1") for name in ("task", "judge", "reflection")}
    session = Harness(Configuration(roles), Budget(40, 2400, reserve=0), lambda: None)
    case, candidate = journey_case(tmp_path, contract())
    result = session.evaluate(candidate, case, checks=CheckMemo())
    assert result["task_usage"] == TOKENS and task_tokens(result) == 12
    assert result["simulator_usage"] == TOKENS | {"input_tokens": sim_input}
    total = cast(dict[str, int | None], result["usage"])
    assert total["input_tokens"] == (None if sim_input is None else 30) and total["output_tokens"] == 6
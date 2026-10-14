"""Adversarial (press) tests for journey evals: brief leakage, the converse loop, init checks, the gate, and intake.

A wrapper `claude` runs scripted turns from a scenario file and delegates every other call to `fake_claude.py`.
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError, load_cases
from skillz_experiments._contract import Contract, Surface, parse
from skillz_experiments.evaluation._evaluator import Reply, Transcript, evaluate
from skillz_experiments.evaluation import CheckMemo
from skillz_experiments.harness import Configuration, Harness
from skillz_experiments.harness._claude import ClaudeCode
from skillz_experiments._intake import DRAFT_NAME, case_hash, freeze, load_draft, split_cases
from skillz_experiments._budget import Budget, BudgetExhausted
from fake_provider import FakeProvider

FAKE = Path(__file__).parent / "fixtures/fake_claude.py"
BRIEF = "BRIEF_SENTINEL_9f2c: wants a short plan."
EXPECTED = "EXPECTED_SENTINEL_77aa wants a short plan"
RUBRIC = "RUBRIC_SENTINEL_31d0: wants a short plan?"
REQUEST = "Help me plan the work."
TOKENS = {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 2}
PASS_GATE = '{"score": 1}'
pytestmark = pytest.mark.usefixtures("host_login")


REAL_TMP = Path(tempfile.gettempdir())


@pytest.fixture
def foreign_journey_dir() -> Iterator[Path]:
    """A skillz-journey-* directory in the real system temp dir, as another process would leave it."""
    foreign = Path(tempfile.mkdtemp(prefix="skillz-journey-", dir=REAL_TMP))
    try:
        yield foreign
    finally:
        shutil.rmtree(foreign, ignore_errors=True)


def leftovers() -> list[Path]:
    """Journey directories in the per-test temp dir that host_login installs."""
    return list(Path(tempfile.gettempdir()).glob("skillz-journey-*"))

WRAPPER = '''#!/usr/bin/env python3
import importlib.util, json, os, subprocess, sys, time
from pathlib import Path

here = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("base_claude", here.with_name("base_claude.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
USAGE = base.USAGE


def emit(event):
    print(json.dumps(event), flush=True)


def note(row):
    with here.with_name("claude.attack.log").open("a") as handle:
        handle.write(json.dumps(row) + "\\n")


def attempt(action):
    try:
        action()
        return "ok"
    except OSError as error:
        return type(error).__name__


def stream(argv):
    scenario = json.loads(here.with_name("claude.scenario").read_text())
    cwd = Path.cwd()
    roots = [cwd.parent] + ([Path(os.environ["CLAUDE_CONFIG_DIR"])] if os.environ.get("CLAUDE_CONFIG_DIR") else [])
    hits, scanned = [], 0
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                scanned += 1
                text = path.read_text(errors="ignore")
                hits += [[str(path), s] for s in scenario.get("sentinels", []) if s in text]
    blob = json.dumps({"argv": argv, "env": dict(os.environ)})
    hits += [["argv-or-env", s] for s in scenario.get("sentinels", []) if s in blob]
    row = {"start": True, "pid": os.getpid(), "hits": hits, "scanned": scanned, "argv": argv,
           "listing": sorted(str(p.relative_to(cwd.parent)) for p in cwd.parent.rglob("*"))}
    if scenario.get("tamper"):
        skill = Path(".claude/skills/helper-skill")
        row["tamper"] = {
            "write_existing": attempt(lambda: (skill / "SKILL.md").write_text("pwn")),
            "create_in_root": attempt(lambda: (skill / "new.md").write_text("x")),
            "create_nested": attempt(lambda: (skill / "references/new.md").write_text("x")),
            "delete": attempt(lambda: (skill / "SKILL.md").unlink()),
        }
    note(row)
    turns = scenario.get("turns", [])
    markers = sorted(cwd.glob(".agents/skills/*/EXPERIMENT_MARKER"))
    for turn, line in enumerate(sys.stdin, 1):
        note({"turn": turn, "message": json.loads(line)["message"]["content"]})
        step = turns[min(turn, len(turns)) - 1] if turns else {}
        if step.get("exit"):
            return 3
        if "exit_code" in step:
            return step["exit_code"]
        skills = sorted(p.name for p in (cwd / ".claude/skills").iterdir()) + list(base.BUILTIN_SKILLS)
        skills = [s for s in skills if s not in step.get("drop_skills", [])] + step.get("extra_skills", [])
        init = {"skills": skills, "tools": ["Bash", "Read", "Skill"], "plugins": list(base.BUILTIN_PLUGINS),
                "mcp_servers": [], "agents": ["general-purpose", "Explore"]} | step.get("init", {})
        if not step.get("no_init"):
            emit({"type": "system", "subtype": "init", **init})
        if step.get("spawn"):
            note({"grandchild": subprocess.Popen(["sleep", "300"]).pid})
        if step.get("hang"):
            time.sleep(120)
        for event in step.get("events", []):
            emit(event)
        for name, text in step.get("write", {}).items():
            Path(name).write_text(text)
        if turn == 1 and markers:
            skill = markers[0].parent.name
            emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                  "input": {"command": f"cat .agents/skills/{skill}/SKILL.md"}}]}})
            emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                  "is_error": False, "content": markers[0].read_text()}]}})
        answer = f"agent-{turn}"
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}})
        emit({"type": "result", "subtype": "success", "is_error": bool(step.get("error")), "result": answer, "usage": USAGE})
    return scenario.get("close_exit", 0)


if "--input-format" in sys.argv and "--model" in sys.argv:
    sys.exit(stream(sys.argv[1:]))
sys.exit(base.main())
'''

SKILL_TEXT = "---\nname: echo-skill\ndescription: x\n---\nbody\n"
SURFACE_FILES = {
    "SKILL.md": SKILL_TEXT,
    "mcp/docs.json": json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
    "agents/reviewer.md": "---\nname: reviewer\ndescription: r\n---\n",
    "skills/helper-skill/SKILL.md": "---\nname: helper-skill\ndescription: h\n---\n",
    "skills/helper-skill/references/notes.md": "notes\n",
}
SURFACE: dict[str, object] = {"mcp_config": "mcp/docs.json", "agents": ["agents/reviewer.md"],
                              "companions": ["skills/helper-skill"]}


def gate_argv(dump: Path | None = None, stdout: str = PASS_GATE, code: int = 0) -> list[str]:
    """A gate that dumps every file of its workspace to `dump`, then prints `stdout` and exits with `code`."""
    script = ("import json, os, sys\nfiles = {}\nfor root, _, names in os.walk('.'):\n    for name in names:\n"
              + "        path = os.path.join(root, name)\n        files[path] = open(path, errors='ignore').read()\n"
              + f"if {str(dump or '')!r}: json.dump(files, open({str(dump or '')!r}, 'w'))\n"
              + f"print({stdout!r})\nsys.exit({code})\n")
    return ["python3", "-I", "-c", script]


def contract(surface: dict[str, object] | None = None, argv: list[str] | None = None) -> Contract:
    document: dict[str, object] = {
        "schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
        "kinds": {"journey": {"grader": "journey", "argv": argv or gate_argv(), "rubric": RUBRIC}}}
    if surface is not None:
        document["surface"] = surface
    return parse(document, "skill")


def journey_case(tmp_path: Path, rules: Contract, files: dict[str, str], max_turns: int = 8,
                 case_files: dict[str, str] | None = None) -> tuple[Case, Candidate]:
    item = {"id": "j1", "family": "j", "split": "train", "kind": "journey", "request": REQUEST, "brief": BRIEF,
            "max_turns": max_turns, "files": case_files or {"input.txt": "hello\n"}, "expected": {"secret": EXPECTED},
            "provenance": "test", "provider_approved": True}
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": [item]}))
    return load_cases(path, rules.grader_types())[0], Candidate(files, ("SKILL.md",), contract=rules)


def install(tmp_path: Path, scenario: dict[str, object]) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir(parents=True)
    _ = shutil.copyfile(FAKE, directory / "base_claude.py")
    executable = directory / "claude"
    _ = executable.write_text(WRAPPER)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    _ = executable.with_name("claude.scenario").write_text(json.dumps(scenario))
    return executable


def open_session(tmp_path: Path, executable: Path, surface: Surface | None = None, files: dict[str, str] | None = None,
                 budget: Budget | None = None) -> Harness:
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(executable)]}))
    return Configuration.load(config, "m").create("m", budget or Budget(40, 120, 0), lambda: None, None, surface, files)


def rows(executable: Path) -> list[dict[str, object]]:
    log = executable.with_name("claude.attack.log")
    return [cast(dict[str, object], json.loads(line)) for line in log.read_text().splitlines()] if log.is_file() else []


def messages(executable: Path) -> list[str]:
    return [cast(str, row["message"]) for row in rows(executable) if "message" in row]


def alive(pid: int) -> bool:
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False).stdout.strip()
    return bool(state) and not state.startswith("Z")


def all_gone(pids: list[int]) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not any(alive(pid) for pid in pids):
            return True
        time.sleep(0.1)
    return False


def pids(executable: Path) -> list[int]:
    return [cast(int, row[key]) for row in rows(executable) for key in ("pid", "grandchild") if key in row]


def drive(tmp_path: Path, scenario: dict[str, object], max_turns: int, replies: list[Reply] | None = None, *,
          surface: dict[str, object] | None = None, case_files: dict[str, str] | None = None,
          budget: Budget | None = None) -> tuple[Path, dict[str, object], list[Transcript], ClaudeCode]:
    """Run `converse` on the wrapper. Replies come from `replies`, then the simulator continues forever."""
    executable = install(tmp_path, scenario)
    rules = contract(surface)
    files = SURFACE_FILES if surface is not None else {"SKILL.md": SKILL_TEXT}
    case, candidate = journey_case(tmp_path, rules, files, max_turns, case_files)
    session = open_session(tmp_path, executable, rules.surface, dict(files), budget)
    seen: list[Transcript] = []

    def simulate(transcript: Transcript) -> Reply:
        seen.append([dict(row) for row in transcript])
        return replies[len(seen) - 1] if replies and len(seen) <= len(replies) else Reply(f"reply-{len(seen)}", False)

    task = cast(ClaudeCode, session.transports["task"])
    try:
        return executable, task.converse("PREFIX " + REQUEST, candidate, case, False, simulate), seen, task
    finally:
        session.close()


# AC-2: the brief reaches nothing the agent can read.

def test_converse_brief_and_expected_never_reach_the_workspace_config_argv_env_or_user_messages(tmp_path: Path) -> None:
    scenario: dict[str, object] = {"sentinels": [BRIEF, EXPECTED]}
    executable, result, _, _ = drive(tmp_path / "real", scenario, 3, surface=SURFACE)
    start = next(row for row in rows(executable) if "start" in row)
    assert start["hits"] == []
    assert cast(int, start["scanned"]) >= 6 and "workspace/.claude/agents/reviewer.md" in cast(list[str], start["listing"])
    assert all(BRIEF not in text and EXPECTED not in text for text in messages(executable))
    assert BRIEF not in json.dumps(result["events"]) and BRIEF not in json.dumps(result["raw_events"])
    # Control: a fixture that holds the brief must show up in the scan, so the scan above is not vacuous.
    control, _, _, _ = drive(tmp_path / "control", scenario, 1, case_files={"input.txt": BRIEF})
    hits = cast(list[list[str]], next(row for row in rows(control) if "start" in row)["hits"])
    assert any(sentinel == BRIEF for _, sentinel in hits)


# AC-3: loop edges.

@pytest.mark.parametrize("max_turns", [1, 2, 3, 4])
def test_converse_sends_exactly_max_turns_messages_and_asks_the_simulator_one_fewer_time(tmp_path: Path, max_turns: int) -> None:
    executable, result, seen, task = drive(tmp_path, {}, max_turns)
    assert messages(executable) == ["PREFIX " + REQUEST] + [f"reply-{n}" for n in range(1, max_turns)]
    assert len(seen) == max_turns - 1
    assert [len(transcript) for transcript in seen] == [2 * n for n in range(1, max_turns)]
    assert task.budget.calls == max_turns
    transcript = cast(Transcript, result["transcript"])
    assert [row["text"] for row in transcript if row["role"] == "assistant"] == [f"agent-{n}" for n in range(1, max_turns + 1)]
    assert [row["text"] for row in transcript if row["role"] == "user"] == [REQUEST] + [f"reply-{n}" for n in range(1, max_turns)]


def test_converse_simulator_done_on_its_first_call_sends_no_further_message(tmp_path: Path) -> None:
    executable, result, seen, task = drive(tmp_path, {}, 5, [Reply("", True)])
    assert messages(executable) == ["PREFIX " + REQUEST] and len(seen) == 1 and task.budget.calls == 1
    assert len(cast(Transcript, result["transcript"])) == 2


def test_converse_process_that_exits_mid_journey_after_two_results_fails_the_case(tmp_path: Path) -> None:
    scenario: dict[str, object] = {"turns": [{}, {}, {"exit": True}]}
    with pytest.raises(RuntimeError, match=r"Claude Code fails.*exit 3"):
        _ = drive(tmp_path, scenario, 6)


def test_converse_process_that_exits_zero_before_a_later_result_fails_the_case(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=r"Claude Code fails.*exit 0"):
        _ = drive(tmp_path, {"turns": [{}, {"exit_code": 0}]}, 4)


@pytest.mark.parametrize(("replies", "max_turns"), [([Reply("", True)], 5), (None, 2)], ids=["done", "max-turns"])
def test_converse_nonzero_exit_after_the_runner_closes_stdin_passes(tmp_path: Path, replies: list[Reply] | None,
                                                                    max_turns: int) -> None:
    _, result, _, _ = drive(tmp_path, {"close_exit": 5}, max_turns, replies)
    assert result["answer"] != {"result_json": "", "load_marker": ""}


def test_converse_init_without_the_candidate_returns_the_not_loaded_result(tmp_path: Path) -> None:
    executable, result, seen, task = drive(tmp_path, {"turns": [{"drop_skills": ["echo-skill"]}]}, 3)
    assert result["answer"] == {"result_json": "", "load_marker": ""} and result["events"] == []
    assert messages(executable) == ["PREFIX " + REQUEST] and len(seen) == 0 and task.budget.calls == 1


def test_converse_errored_result_in_an_earlier_turn_fails_the_case(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="turn 1"):
        _ = drive(tmp_path, {"turns": [{"error": True}, {}]}, 4)


def test_converse_turn_one_without_an_init_event_stops_with_isolation_failed(tmp_path: Path) -> None:
    with pytest.raises(CodedError) as caught:
        _ = drive(tmp_path, {"turns": [{"no_init": True}, {}]}, 4)
    assert caught.value.code == "isolation-failed"


def test_converse_second_init_event_inside_turn_one_is_compared(tmp_path: Path) -> None:
    change = {"type": "system", "subtype": "init", "skills": ["echo-skill"], "tools": ["Bash"]}
    with pytest.raises(CodedError) as caught:
        _ = drive(tmp_path, {"turns": [{"events": [change]}]}, 4)
    assert caught.value.code == "isolation-failed"


@pytest.mark.parametrize("bad", [
    {"reply": "x"}, {"done": True}, {"reply": "x", "done": "true"}, {"reply": "x", "done": 1}, {"reply": None, "done": False},
    {"reply": 5, "done": False}, {"reply": "x", "done": False, "extra": 1}, {}, ["reply", "done"],
], ids=["no-done", "no-reply", "string-done", "int-done", "null-reply", "int-reply", "extra-field", "empty", "list"])
def test_malformed_simulator_reply_is_rejected_without_a_hang_and_the_process_group_is_reaped(tmp_path: Path, bad: object) -> None:
    class Simulator:
        def __init__(self) -> None:
            self.budget: Budget = Budget(40, 120, 0)

        def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
                   *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
            del prompt, candidate, case, holdout, schema
            return {"answer": bad, "usage": TOKENS.copy()}

    executable = install(tmp_path, {"turns": [{"spawn": True}]})
    rules = contract()
    case, candidate = journey_case(tmp_path, rules, {"SKILL.md": SKILL_TEXT}, 4)
    session = open_session(tmp_path, executable)
    started = time.monotonic()
    try:
        with pytest.raises(ValueError, match="invalid simulator response"):
            _ = evaluate(session.transports["task"], session.transports["judge"], candidate, case,
                         simulator=Simulator(), checks=CheckMemo())  # pyright: ignore[reportArgumentType]
    finally:
        session.close()
    assert time.monotonic() - started < 30 and all_gone(pids(executable))


def test_converse_deadline_kills_the_whole_process_group_and_stops_with_budget_exhausted(tmp_path: Path) -> None:
    started = time.monotonic()
    executable = install(tmp_path, {"turns": [{"spawn": True, "hang": True}]})
    rules = contract()
    case, candidate = journey_case(tmp_path, rules, {"SKILL.md": SKILL_TEXT}, 3)
    session = open_session(tmp_path, executable, budget=Budget(40, 4, 0))
    try:
        with pytest.raises(BudgetExhausted) as caught:
            _ = cast(ClaudeCode, session.transports["task"]).converse(REQUEST, candidate, case, False, lambda _t: Reply("x", False))
    finally:
        session.close()
    assert caught.value.code == "budget-exhausted" and time.monotonic() - started < 30
    assert len(pids(executable)) == 2 and all_gone(pids(executable))


# AC-5 and AC-8: per-turn init checks.

@pytest.mark.parametrize("turns", [
    [{}, {"init": {"tools": ["Bash", "Read", "Skill", "Write"]}}],
    [{}, {"init": {"tools": ["Bash", "Read"]}}],
    [{}, {"drop_skills": ["echo-skill"]}],
    [{}, {"extra_skills": ["intruder-skill"]}],
    [{}, {}, {"extra_skills": ["intruder-skill"]}],
    [{}, {"events": [{"type": "system", "subtype": "init", "skills": ["echo-skill"], "tools": ["Bash"]}]}],
], ids=["extra-tool", "removed-tool", "removed-skill", "extra-skill", "extra-skill-turn-3", "second-init-in-turn-2"])
def test_a_later_init_event_with_another_skill_or_tool_inventory_stops_with_isolation_failed(
        tmp_path: Path, turns: list[dict[str, object]]) -> None:
    with pytest.raises(CodedError) as caught:
        _ = drive(tmp_path, {"turns": turns}, 5)
    assert caught.value.code == "isolation-failed"


def test_an_undeclared_skill_in_every_init_event_stops_with_isolation_failed(tmp_path: Path) -> None:
    with pytest.raises(CodedError) as caught:
        _ = drive(tmp_path, {"turns": [{"extra_skills": ["intruder-skill"]}]}, 3)
    assert caught.value.code == "isolation-failed"


@pytest.mark.parametrize("turns", [
    [{}, {"init": {"tools": ["Skill", "Read", "Bash"]}}],
    [{}, {}, {}],
], ids=["tools-reordered", "identical"])
def test_an_identical_later_init_event_passes_the_inventory_check(tmp_path: Path, turns: list[dict[str, object]]) -> None:
    _, result, _, _ = drive(tmp_path, {"turns": turns}, 3)
    assert len(cast(Transcript, result["transcript"])) == 6


@pytest.mark.parametrize("change", [
    {"agents": ["general-purpose", "Explore", "intruder-agent"]},
    {"mcp_servers": [{"name": "evil", "status": "connected"}]},
    {"plugins": [{"name": "user-plugin", "path": "/x"}]},
], ids=["agent", "mcp-server", "plugin"])
def test_an_undeclared_agent_server_or_plugin_that_appears_only_in_a_later_init_event_stops_the_journey(
        tmp_path: Path, change: dict[str, object]) -> None:
    with pytest.raises(CodedError) as caught:
        _ = drive(tmp_path, {"turns": [{}, {"init": change}]}, 4)
    assert caught.value.code == "isolation-failed"


# AC-6, AC-4, AC-9: the gate, the judge, the simulator prompt, and the budget around one journey.

class Judge(FakeProvider):
    """A stub simulator and judge that pops one answer per call and claims the budget that it shares with the task."""

    def __init__(self, answers: list[dict[str, object]]) -> None:
        self.budget: Budget = Budget(40, 2400, reserve=0)
        self.answers: list[dict[str, object]] = answers
        self.prompts: list[str] = []

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del schema
        assert candidate is None and case is None
        self.budget.claim(holdout=holdout)
        self.prompts.append(prompt)
        return {"answer": self.answers.pop(0), "usage": TOKENS.copy(), "latency_seconds": 0.1}


@pytest.fixture
def local_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str], seconds: int | None = None) -> tuple[int, str, str]:
        del self, seconds
        done = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=30, check=False)
        return done.returncode, done.stdout, done.stderr

    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)


def evaluate_journey(tmp_path: Path, scenario: dict[str, object], argv: list[str], answers: list[dict[str, object]],
                     max_turns: int = 4, budget: Budget | None = None) -> tuple[Path, dict[str, object], Judge, ClaudeCode]:
    executable = install(tmp_path, scenario)
    rules = contract(argv=argv)
    case, candidate = journey_case(tmp_path, rules, {"SKILL.md": SKILL_TEXT}, max_turns)
    session = open_session(tmp_path, executable, budget=budget)
    task = cast(ClaudeCode, session.transports["task"])
    judge = Judge([dict(answer) for answer in answers])
    judge.budget = task.budget
    try:
        return executable, evaluate(task, judge, candidate, case, checks=CheckMemo()), judge, task
    finally:
        session.close()


SIMULATE_THEN_DONE: list[dict[str, object]] = [{"reply": "r1 text", "done": False}, {"reply": "", "done": True}]


def test_journey_gate_workspace_holds_the_exact_event_stream_and_neither_brief_nor_expected(
        tmp_path: Path, local_sandbox: None) -> None:
    del local_sandbox
    dump = tmp_path / "dump.json"
    scenario: dict[str, object] = {"turns": [{"write": {"notes.txt": "NOTES_SENTINEL\n"}}, {}]}
    _, result, judge, task = evaluate_journey(tmp_path, scenario, gate_argv(dump), [*SIMULATE_THEN_DONE, {"score_percent": 70}])
    files = cast(dict[str, str], json.loads(dump.read_text()))
    trace = [cast(dict[str, object], json.loads(line)) for line in files["./trace/events.jsonl"].splitlines()]
    assert [event["type"] for event in trace] == ["system", "assistant", "user", "assistant", "result",
                                                 "system", "assistant", "result"]
    assert trace[-1]["result"] == "agent-2" and trace[4]["result"] == "agent-1"
    assert files["./input.txt"] == "hello\n" and files["./output/notes.txt"] == "NOTES_SENTINEL\n"
    assert not [name for name, text in files.items() if BRIEF in text or EXPECTED in text or "SENTINEL_9f2c" in text]
    assert result["score"] == 0.7 and task.budget.calls == 5
    sim_one, sim_two, final = judge.prompts
    for prompt in (sim_one, sim_two):
        assert BRIEF in prompt and REQUEST in prompt
        assert "EXPECTED_SENTINEL" not in prompt and "RUBRIC_SENTINEL" not in prompt and "secret" not in prompt
    assert "agent-1" in sim_one and "agent-2" not in sim_one
    assert all(text in sim_two for text in ("agent-1", "agent-2", "r1 text"))
    assert all(text in final for text in (BRIEF, REQUEST, "agent-1", "agent-2", "NOTES_SENTINEL", EXPECTED, "r1 text"))
    assert final.count(RUBRIC) == 1


@pytest.mark.parametrize(("stdout", "code"), [
    ('{"score": 0}', 0), ('{"score": 0.0}', 0), ('{"score": -0.5}', 0), ('{"score": -1}', 0), ('{"score": 1.5}', 0),
    ('{"score": true}', 0), ('not json', 0), ('{}', 0), ('{"score": 1}', 1),
], ids=["zero", "float-zero", "negative-half", "minus-one", "above-one", "bool", "garbage", "no-score", "exit-1"])
def test_journey_gate_score_of_zero_or_less_or_an_invalid_score_skips_the_judge(
        tmp_path: Path, local_sandbox: None, stdout: str, code: int) -> None:
    del local_sandbox
    _, result, judge, _ = evaluate_journey(tmp_path, {}, gate_argv(stdout=stdout, code=code), SIMULATE_THEN_DONE)
    assert result["score"] == 0.0 and result["status"] == "gate-failed"
    assert cast(dict[str, object], result["scores"])["judge"] is None
    assert [prompt for prompt in judge.prompts if "RUBRIC:" in prompt] == [] and len(judge.prompts) == 2


def test_journey_gate_score_just_above_zero_reaches_the_judge(tmp_path: Path, local_sandbox: None) -> None:
    del local_sandbox
    _, result, judge, _ = evaluate_journey(tmp_path, {}, gate_argv(stdout='{"score": 0.0001}'),
                                           [*SIMULATE_THEN_DONE, {"score_percent": 40}])
    assert result["score"] == 0.4 and len([prompt for prompt in judge.prompts if "RUBRIC:" in prompt]) == 1


def test_journey_that_needs_more_calls_than_the_search_share_is_refused_before_any_process_starts(
        tmp_path: Path, local_sandbox: None) -> None:
    del local_sandbox
    # max_turns 3 costs 7 calls; 10 - 5 reserved leaves 5, so the check must fire before the first agent turn.
    with pytest.raises(BudgetExhausted):
        _ = evaluate_journey(tmp_path / "short", {}, gate_argv(), SIMULATE_THEN_DONE, 3, Budget(10, 120, 5))
    assert not (tmp_path / "short/bin/claude.attack.log").exists()
    # 10 - 3 reserved leaves exactly 7: the journey completes inside the share, with at most 7 claims.
    answers: list[dict[str, object]] = [{"reply": "a", "done": False}, {"reply": "b", "done": False}, {"score_percent": 50}]
    _, result, _, task = evaluate_journey(tmp_path / "fits", {}, gate_argv(), answers, 3, Budget(10, 120, 3))
    assert result["score"] == 0.5 and task.budget.calls == 6 <= contract().calls("journey", 3) == 7


# AC-7: companions stay read-only for the task, and the journey leaves nothing behind.

@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
@pytest.mark.ac("gh-311:AC-1")
@pytest.mark.ac("gh-311:AC-2")
def test_a_task_cannot_write_create_or_delete_inside_a_companion_and_the_workspace_is_still_removed(
        tmp_path: Path, foreign_journey_dir: Path) -> None:
    executable, result, _, _ = drive(tmp_path, {"tamper": True}, 2, surface=SURFACE)
    tamper = cast(dict[str, str], next(row for row in rows(executable) if "start" in row)["tamper"])
    assert tamper == {"write_existing": "PermissionError", "create_in_root": "PermissionError",
                      "create_nested": "PermissionError", "delete": "PermissionError"}
    workspace = Path(cast(str, result["workspace"]))
    assert Path(tempfile.gettempdir()) != REAL_TMP
    assert workspace.parents[1] == Path(tempfile.gettempdir())
    assert not workspace.exists()
    assert foreign_journey_dir.exists()
    assert not leftovers()


@pytest.mark.ac("gh-285:AC-1")
def test_a_failing_simulator_still_removes_a_read_only_workspace_and_the_process(
        tmp_path: Path, foreign_journey_dir: Path) -> None:
    executable = install(tmp_path, {"turns": [{"spawn": True}]})
    rules = contract(SURFACE)
    case, candidate = journey_case(tmp_path, rules, SURFACE_FILES, 3)
    session = open_session(tmp_path, executable, rules.surface, dict(SURFACE_FILES))

    def boom(_transcript: Transcript) -> Reply:
        raise RuntimeError("simulator down")

    try:
        with pytest.raises(RuntimeError, match="simulator down"):
            _ = cast(ClaudeCode, session.transports["task"]).converse(REQUEST, candidate, case, False, boom)
    finally:
        session.close()
    assert len(pids(executable)) == 2 and all_gone(pids(executable))
    assert foreign_journey_dir.exists()
    assert not leftovers()


# AC-7 and AC-8: no surface means the exact pre-change argv.

def test_a_contract_without_a_surface_keeps_the_pre_change_claude_argv(tmp_path: Path) -> None:
    executable = install(tmp_path, {})
    rules = contract()
    case, candidate = journey_case(tmp_path, rules, {"SKILL.md": SKILL_TEXT}, 1)
    session = open_session(tmp_path, executable)
    try:
        _ = cast(ClaudeCode, session.transports["task"]).converse(REQUEST, candidate, case, False, lambda _t: Reply("", True))
    finally:
        session.close()
    argv = cast(list[str], next(row for row in rows(executable) if "start" in row)["argv"])
    settings = argv[argv.index("--settings") + 1]
    assert argv == ["-p", "--setting-sources", "project", "--no-session-persistence", "--tools", "Bash,Read,Skill",
                    "--strict-mcp-config", "--settings", settings, "--output-format", "stream-json", "--verbose",
                    "--model", "m", "--input-format", "stream-json"]


# AC-1: intake edges.

def draft_items(**changes: object) -> list[dict[str, object]]:
    base: dict[str, object] = {"id": "a", "family": "fa", "kind": "journey", "request": "r", "source": "skill",
                               "files": {"f.txt": "x"}, "expected": "ref", "brief": "wants plan"}
    others = [base | {"id": name, "family": "f" + name} for name in ("b", "c")]
    return [base | changes, *others]


def hash_of(tmp_path: Path, **changes: object) -> str:
    path = tmp_path / DRAFT_NAME
    _ = path.write_text(json.dumps(draft_items(**changes)), encoding="utf-8")
    cases = split_cases(load_draft(path), 7, "journey")
    return case_hash(cases, 7)


def test_case_hash_changes_with_the_brief_the_turn_limit_and_the_order_of_requirements(tmp_path: Path) -> None:
    base = hash_of(tmp_path, requirements=["R1", "R2"])
    assert hash_of(tmp_path, requirements=["R1", "R2"]) == base
    variants = [hash_of(tmp_path, requirements=["R2", "R1"]), hash_of(tmp_path, requirements=["R1"]),
                hash_of(tmp_path, requirements=["R1", "R2", "R2"]), hash_of(tmp_path, requirements=["R1", "R2"], brief="wants plan."),
                hash_of(tmp_path, requirements=["R1", "R2"], max_turns=7)]
    assert len({base, *variants}) == 6
    assert hash_of(tmp_path, max_turns=8) == hash_of(tmp_path)


@pytest.mark.parametrize("changes", [
    {"max_turns": 0}, {"max_turns": -1}, {"max_turns": True}, {"max_turns": 1.5}, {"max_turns": "3"}, {"max_turns": None},
    {"requirements": "R1"}, {"requirements": [1]}, {"requirements": [None]},
], ids=["zero", "negative", "bool", "float", "string", "null", "requirements-text", "requirements-int", "requirements-null"])
def test_a_journey_with_an_invalid_turn_limit_or_requirements_is_rejected_by_intake_and_by_load_cases(
        tmp_path: Path, changes: dict[str, object]) -> None:
    path = tmp_path / DRAFT_NAME
    _ = path.write_text(json.dumps(draft_items(**changes)), encoding="utf-8")
    with pytest.raises(ValueError):
        _ = load_draft(path)
    item = draft_items(**changes)[0] | {"split": "train", "provenance": "t", "provider_approved": True}
    manifest = tmp_path / "cases.json"
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": [item]}))
    with pytest.raises(ValueError):
        _ = load_cases(manifest, {"journey": "journey"})


@pytest.mark.parametrize("brief", ["", "   \n\t", None, 5, ["x"]], ids=["empty", "blank", "null", "int", "list"])
def test_a_journey_without_a_usable_brief_is_rejected_with_brief_missing(tmp_path: Path, brief: object) -> None:
    path = tmp_path / DRAFT_NAME
    _ = path.write_text(json.dumps(draft_items(brief=brief)), encoding="utf-8")
    with pytest.raises(CodedError) as caught:
        _ = load_draft(path)
    assert caught.value.code == "brief-missing"


def test_frozen_journey_keeps_the_brief_the_turn_limit_and_the_requirement_order_through_load_cases(tmp_path: Path) -> None:
    path = tmp_path / DRAFT_NAME
    _ = path.write_text(json.dumps(draft_items(requirements=["R2", "R1", "R2"])), encoding="utf-8")
    cases = split_cases(load_draft(path), 7, "journey")
    out = tmp_path / "cases.json"
    freeze(out, cases, 7, case_hash(cases, 7))
    loaded = {case.identifier: case for case in load_cases(out, {"journey": "journey"})}
    journey = loaded["a"].journey
    assert journey is not None and (journey.brief, journey.max_turns, journey.requirements) == ("wants plan", 8, ("R2", "R1", "R2"))
    assert loaded["a"].kind == "journey" and all(case.journey is not None for case in loaded.values())


def test_a_journey_case_needs_the_journey_grader_and_a_conversing_task_transport(tmp_path: Path) -> None:
    journey, candidate = journey_case(tmp_path, contract(), {"SKILL.md": SKILL_TEXT}, 2)
    stub = Judge([])
    with pytest.raises(CodedError) as caught:
        _ = evaluate(stub, stub, candidate, journey, checks=CheckMemo())
    assert caught.value.code == "journey-unsupported" and stub.prompts == []
    journey_grader = contract().kinds["journey"]
    plain = dataclasses.replace(contract(), kinds={"journey": dataclasses.replace(journey_grader, type="exact-json")})
    with pytest.raises(ValueError, match="journey grader"):
        _ = evaluate(stub, stub, Candidate({"SKILL.md": SKILL_TEXT}, ("SKILL.md",), contract=plain), journey, checks=CheckMemo())


def test_contract_calls_scale_with_max_turns_only_for_a_journey_kind() -> None:
    rules = contract()
    assert [rules.calls("journey", turns) for turns in (1, 2, 8, 50)] == [3, 5, 17, 101]
    mixed = parse({"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
                   "kinds": {"e": {"grader": "exact-json"}, "h": {"grader": "hybrid", "argv": ["true"], "rubric": "r"}}}, "skill")
    assert (mixed.calls("e", 50), mixed.calls("h", 50)) == (1, 2)


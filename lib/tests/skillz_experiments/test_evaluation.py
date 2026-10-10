from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

import pytest

from skillz_experiments._evaluation import fixture_failure, grade, usage


def test_evaluator_rejects_parroting_and_missing_activation() -> None:
    expected = {"keys": ["name"]}
    assert grade('{"keys":["name"]}', expected, loaded=True, helper_executed=True) == 1.0
    assert grade('{"keys":["description"]}', expected, loaded=True, helper_executed=True) == 0.0
    assert grade('{"keys":["name"]}', expected, loaded=False, helper_executed=True) == 0.0
    assert grade('{"keys":["name"]}', expected, loaded=True, helper_executed=False) == 0.0


def test_usage_keeps_unknown_and_does_not_double_count_cache() -> None:
    assert usage([]) == {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None}
    events: list[dict[str, object]] = [{"type": "turn.completed", "usage": {"input_tokens": 12, "cached_input_tokens": 5, "output_tokens": 3}}]
    assert usage(events) == {"input_tokens": 12, "cached_input_tokens": 5, "output_tokens": 3}

def test_command_trace_rejects_spoofed_helper_names() -> None:
    from skillz_experiments._evaluation import executed

    def events(command: str) -> list[dict[str, object]]:
        return [{"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0, "command": command}}]

    script = "scripts/inspect-skill.pyz"
    assert not executed(events("echo inspect-skill.pyz"), script, skill="skillz")
    assert not executed(events("echo SKILL.md"), "SKILL.md", skill="skillz")
    assert executed(events("cat .agents/skills/skillz/SKILL.md"), "SKILL.md", skill="skillz")
    assert executed(events("python3 -I .agents/skills/skillz/scripts/inspect-skill.pyz fixture.md"), script,
                    skill="skillz", input_path="fixture.md")
    assert not executed(events("echo yes; python3 .agents/skills/skillz/scripts/inspect-skill.pyz fixture.md"),
                        script, skill="skillz")
    assert executed(events("cat .agents/skills/echo/SKILL.md"), "SKILL.md", skill="echo")
    assert not executed(events("cat .agents/skills/skillz/SKILL.md"), "SKILL.md", skill="echo")
    assert executed(events("python3 -I .agents/skills/echo/scripts/run.py in.md"), "scripts/run.py", skill="echo",
                    isolated=True, input_path="in.md")
    assert not executed(events("python3 .agents/skills/echo/scripts/run.py in.md"), "scripts/run.py", skill="echo",
                        isolated=True, input_path="in.md")
    assert executed(events("python3 .agents/skills/echo/run.py input.md"), "run.py", skill="echo", input_path="input.md")
    assert not executed(events("python3 .agents/skills/echo/run.py wrong.md"), "run.py", skill="echo", input_path="input.md")
    assert not executed(events("python3 .agents/skills/echo/run.py"), "run.py", skill="echo", input_path="input.md")
    assert not executed(events("python3 .agents/skills/echo/run.py input.md extra"), "run.py", skill="echo",
                        input_path="input.md")
    assert executed(events("python3 /work/.agents/skills/echo/run.py /work/input.md"), "run.py", "/work",
                    skill="echo", input_path="input.md")
    assert executed(events("python3 -I .agents/skills/echo/scripts/offload.py"), "scripts/offload.py",
                    skill="echo", isolated=True)


def test_activation_requires_the_declared_root_helper_and_input() -> None:
    from skillz_experiments._candidate import Candidate
    from skillz_experiments._contract import Contract, Grader, Helper
    from skillz_experiments._evaluator import _activation  # pyright: ignore[reportPrivateUsage]

    rules = Contract("echo", "$echo run", {"echo": Grader("exact-json")}, Helper("run.py", "input.md"))
    candidate = Candidate({"SKILL.md": "skill", "run.py": "pass"}, ("SKILL.md",), rules)

    def activated(command: str) -> tuple[bool, bool]:
        events = [{"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0,
                                                     "command": text}} for text in
                  ("cat .agents/skills/echo/SKILL.md", command)]
        result: dict[str, object] = {"answer": {"load_marker": candidate.identity},
                                     "events": events, "workspace": "/work"}
        _, loaded, helper = _activation(result, candidate, rules)
        return loaded, helper

    assert activated("python3 .agents/skills/echo/run.py input.md") == (True, True)
    assert activated("python3 /work/.agents/skills/echo/run.py /work/input.md") == (True, True)
    assert activated("python3 .agents/skills/echo/run.py wrong.md") == (True, False)
    assert activated("python3 .agents/skills/echo/run.py") == (True, False)


ERROR = '{"error": "inspect: link escapes package", "exit_code": 3}'


def fixture_result(fixture: Mapping[str, object], returncode: int, stdout: str, stderr: str) -> bool:
    return fixture_failure(fixture, returncode, stdout, stderr) is None


def test_frozen_helper_contract_rejects_constant_answers() -> None:
    from skillz_experiments._contract import HELPER_FIXTURES
    failure, success = HELPER_FIXTURES[1], HELPER_FIXTURES[0]
    assert fixture_result(failure, 3, "", ERROR)
    assert not fixture_result(failure, 3, "", '{"error": "inspect: other", "exit_code": 3}')
    assert not fixture_result(failure, 3, "", "")
    assert not fixture_result(failure, 0, "", ERROR)
    assert not fixture_result(success, 3, "", ERROR)


def test_a_null_output_requires_an_empty_stdout() -> None:
    from skillz_experiments._contract import HELPER_FIXTURES
    failure = HELPER_FIXTURES[1]
    assert not fixture_result(failure, 3, "\n", ERROR)
    assert fixture_result(failure, 3, "", ERROR)
    assert not fixture_result(failure, 3, ERROR, ERROR)
    assert not fixture_result(failure, 3, "{}", ERROR)


def test_a_stdout_mismatch_fails_even_with_clean_stderr() -> None:
    from skillz_experiments._contract import HELPER_FIXTURES
    good = HELPER_FIXTURES[0]
    wrong = cast(dict[str, object], good["output"]) | {"body_line_count": 99}
    assert fixture_result(good, 0, json.dumps(good["output"]), "")
    assert not fixture_result(good, 0, json.dumps(wrong), "")
    assert not fixture_result(good, 0, "", "")


def test_stderr_is_ignored_when_the_fixture_has_no_error_key() -> None:
    fixture: dict[str, object] = {"returncode": 0, "output": {"ok": 1}}
    assert fixture_result(fixture, 0, '{"ok": 1}', "warning: noise")


def test_deeply_nested_helper_output_fails_the_fixture_instead_of_raising() -> None:
    from skillz_experiments._contract import HELPER_FIXTURES
    assert not fixture_result(HELPER_FIXTURES[1], 3, "", "[" * 100_000)
    assert not fixture_result(HELPER_FIXTURES[0], 0, "[" * 100_000, "")


def test_exact_json_distinguishes_boolean_from_number() -> None:
    from skillz_experiments._contract import HELPER_FIXTURES
    assert grade("true", 1, loaded=True, helper_executed=True) == 0.0
    assert not fixture_result(HELPER_FIXTURES[1], 3, "", '{"error": "inspect: link escapes package", "exit_code": true}')
    assert fixture_result({"returncode": 0, "output": {"ok": 1}}, 0, '{"ok": 1}', "")
    assert not fixture_result({"returncode": 0, "output": {"ok": 1}}, 0, '{"ok": true}', "")


_FAILURE: dict[str, object] = {"input": "x", "returncode": 3, "output": None, "error": {"error": "e", "exit_code": 3}}


def _parse_fixture(fixture: dict[str, object]) -> object:
    from skillz_experiments._contract import parse

    contract: dict[str, object] = {
        "schema_version": 1, "status": "approved", "skill": "demo", "invocation": "$demo",
        "kinds": {"k": {"grader": "exact-json"}}, "helper": {"path": "scripts/h.py", "fixtures": [fixture]}}
    helper = parse(contract, "skill").helper
    assert helper is not None
    return helper.fixtures[0]


def test_a_fixture_with_output_and_an_error_object_parses() -> None:
    assert _parse_fixture(_FAILURE) == _FAILURE
    assert _parse_fixture({"input": "x", "returncode": 0, "output": {"ok": 1}})


@pytest.mark.parametrize("fixture", [
    _FAILURE | {"stderr": "x"},
    {"input": "x", "returncode": 0},
    {"input": "x", "returncode": "3", "output": None, "error": {"error": "e"}},
    {"input": "x", "returncode": True, "output": {"ok": 1}},
    {"input": "x", "returncode": 3, "output": None},
    {"input": "x", "returncode": 3, "output": None, "error": "text"},
    {"input": "x", "returncode": 0, "output": {"ok": 1}, "error": "text"},
])
def test_a_malformed_helper_fixture_is_rejected(fixture: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _ = _parse_fixture(fixture)

def test_wedge_script_runs_isolated() -> None:
    from types import SimpleNamespace
    from typing import cast

    from skillz_experiments._candidate import Candidate
    from skillz_experiments._cases import Case
    from skillz_experiments._contract import resolve
    from skillz_experiments._evaluator import _prompt  # pyright: ignore[reportPrivateUsage]

    candidate = Candidate({"SKILL.md": "s", "scripts/offload.py": "print(1)"}, ("SKILL.md",), None, "scripts/offload.py")
    prompt = _prompt(resolve(None), candidate, "", cast(Case, cast(object, SimpleNamespace(request="task"))))
    assert "python3 -I .agents/skills/skillz/scripts/offload.py" in prompt


def test_deeply_nested_answer_scores_zero_instead_of_stopping_the_run() -> None:
    assert grade("[" * 200_000, [], loaded=True, helper_executed=True) == 0.0

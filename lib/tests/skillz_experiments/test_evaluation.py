from __future__ import annotations

from skillz_experiments._evaluation import grade, usage


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

    assert not executed(events("echo inspect_skill.py"), "inspect_skill.py", skill="skillz")
    assert not executed(events("echo SKILL.md"), "SKILL.md", skill="skillz")
    assert executed(events("cat .agents/skills/skillz/SKILL.md"), "SKILL.md", skill="skillz")
    assert executed(events("python3 -I .agents/skills/skillz/scripts/inspect_skill.py fixture.md"), "inspect_skill.py",
                    skill="skillz")
    assert not executed(events("echo yes; python3 .agents/skills/skillz/scripts/inspect_skill.py fixture.md"),
                        "inspect_skill.py", skill="skillz")
    assert executed(events("cat .agents/skills/echo/SKILL.md"), "SKILL.md", skill="echo")
    assert not executed(events("cat .agents/skills/skillz/SKILL.md"), "SKILL.md", skill="echo")
    assert executed(events("python3 -I .agents/skills/echo/scripts/run.py in.md"), "scripts/run.py", skill="echo", isolated=True)
    assert not executed(events("python3 .agents/skills/echo/scripts/run.py in.md"), "scripts/run.py", skill="echo", isolated=True)
    assert executed(events("python3 .agents/skills/echo/scripts/run.py in.md"), "scripts/run.py", skill="echo")


def test_frozen_helper_contract_rejects_constant_answers() -> None:
    from skillz_experiments._evaluation import HELPER_FIXTURES, fixture_result

    fixture = HELPER_FIXTURES[1]
    assert fixture_result(fixture, 2, '{"schema_version":2,"error":"link escapes package"}')
    assert not fixture_result(fixture, 2, '{"schema_version":1,"error":"link escapes package"}')
    assert not fixture_result(HELPER_FIXTURES[0], 0, '{"schema_version":2,"error":"link escapes package"}')
    assert not fixture_result(fixture, 0, '{"schema_version":2,"error":"link escapes package"}')


def test_deeply_nested_helper_output_fails_the_fixture_instead_of_raising() -> None:
    from skillz_experiments._evaluation import HELPER_FIXTURES, fixture_result

    assert not fixture_result(HELPER_FIXTURES[1], 2, "[" * 100_000)


def test_exact_json_distinguishes_boolean_from_number() -> None:
    from skillz_experiments._evaluation import HELPER_FIXTURES, fixture_result

    assert grade("true", 1, loaded=True, helper_executed=True) == 0.0
    assert not fixture_result(HELPER_FIXTURES[1], 2, '{"schema_version":true,"error":"link escapes package"}')
    assert fixture_result({"returncode": 0, "output": {"ok": 1}}, 0, '{"ok": 1}')
    assert not fixture_result({"returncode": 0, "output": {"ok": 1}}, 0, '{"ok": true}')


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

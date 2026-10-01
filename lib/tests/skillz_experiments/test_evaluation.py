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

    assert not executed(events("echo inspect_skill.py"), "inspect_skill.py")
    assert not executed(events("echo SKILL.md"), "SKILL.md")
    assert executed(events("cat .agents/skills/skillz/SKILL.md"), "SKILL.md")
    assert executed(events("python3 -I .agents/skills/skillz/scripts/inspect_skill.py fixture.md"), "inspect_skill.py")
    assert not executed(events("echo yes; python3 .agents/skills/skillz/scripts/inspect_skill.py fixture.md"), "inspect_skill.py")


def test_frozen_helper_contract_rejects_constant_answers() -> None:
    from skillz_experiments._evaluation import helper_result

    assert helper_result(1, 2, '{"schema_version":1,"error":"link escapes package"}')
    assert not helper_result(0, 0, '{"schema_version":1,"error":"link escapes package"}')
    assert not helper_result(1, 0, '{"schema_version":1,"error":"link escapes package"}')


def test_exact_json_distinguishes_boolean_from_number() -> None:
    from skillz_experiments._evaluation import helper_result

    assert grade("true", 1, loaded=True, helper_executed=True) == 0.0
    assert not helper_result(1, 2, '{"schema_version":true,"error":"link escapes package"}')

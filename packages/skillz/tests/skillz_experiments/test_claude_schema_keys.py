"""The Claude API accepts only schema property keys that match `^[a-zA-Z0-9_.-]{1,64}$`.

A reflection schema names `@hypothesis` and each component by its file path, so the transport sends safe keys
and maps the structured answer back to the real names. Fake `claude` only.
"""
from __future__ import annotations

import stat
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments.harness._claude._keys import API_KEY, restore, safe_keys
from skillz_experiments._budget import Budget
from skillz_experiments.harness._claude._transport import ClaudeCode

pytestmark = pytest.mark.usefixtures("host_login")

KEYED = '''#!/usr/bin/env python3
import json, re, sys
from pathlib import Path
argv = sys.argv[1:]
sys.stdin.read()
if "--input-format" in argv:
    print(json.dumps({"type": "control_response", "response": {"subtype": "success",
                      "response": {"commands": [{"name": path.name} for path in Path.cwd().glob(".claude/skills/*")]
                                                   + [{"name": "doctor", "builtin": True}]}}}))
    sys.exit(0)
properties = json.loads(argv[argv.index("--json-schema") + 1])["properties"]
print(json.dumps({"type": "system", "subtype": "init", "skills": []}))
if any(not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", key) for key in properties):
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 400, "usage": {},
                      "result": "API Error: 400 tools.4.custom.input_schema.properties: Property keys should match pattern"}))
    sys.exit(1)
answer = {key: "text for " + body.get("description", key) for key, body in properties.items()}
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done", "usage": {"output_tokens": 1},
                  "structured_output": answer}))
'''

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"SKILL.md": {"type": "string"}, "scripts/count.py": {"type": "string"},
                   "@hypothesis": {"type": ["string", "null"]}},
    "required": ["SKILL.md", "scripts/count.py", "@hypothesis"], "additionalProperties": False}


def test_safe_keys_rename_only_the_keys_that_the_api_refuses_and_restore_maps_them_back() -> None:
    sent, names = safe_keys(SCHEMA)
    properties = cast(dict[str, object], sent["properties"])
    required = cast(list[str], sent["required"])
    assert all(API_KEY.fullmatch(key) for key in properties) and set(required) == set(properties)
    assert "SKILL.md" in properties and len(properties) == 3 and "@hypothesis" not in properties
    assert sorted(names.values()) == ["@hypothesis", "scripts/count.py"]
    answer: dict[str, object] = {key: f"v:{names.get(key, key)}" for key in properties}
    assert restore(answer, names) == {"SKILL.md": "v:SKILL.md", "scripts/count.py": "v:scripts/count.py",
                                      "@hypothesis": "v:@hypothesis"}


def test_a_schema_with_safe_keys_is_sent_unchanged() -> None:
    schema: dict[str, object] = {"type": "object", "properties": {"score_percent": {"type": "integer"}},
                                 "required": ["score_percent"]}
    sent, names = safe_keys(schema)
    assert sent == schema and restore({"score_percent": 80}, names) == {"score_percent": 80}


def test_a_claude_reflection_call_with_an_unsafe_key_gets_its_answer_under_the_real_names(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "claude"
    executable.parent.mkdir()
    _ = executable.write_text(KEYED)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    transport = ClaudeCode("claude-test-model", Budget(10, 120, 0), lambda: None, executable)
    transport.role = "reflection"
    try:
        result = transport.invoke("improve", schema=SCHEMA)
    finally:
        transport.close()
    assert result["answer"] == {"SKILL.md": "text for SKILL.md", "scripts/count.py": "text for scripts/count.py",
                                "@hypothesis": "text for @hypothesis"}

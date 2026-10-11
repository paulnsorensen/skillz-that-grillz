"""Rows of user_turns and interrupt_events, end to end through `ingest --force`.

Every prompt text is synthetic.
"""

from __future__ import annotations

from .conftest import Sandbox, needs_duckdb

FULL_TEXT = "long synthetic prompt " * 200


def _claude(kind: str, second: int, content: object, **flags: object) -> dict[str, object]:
    return {
        "type": kind,
        "timestamp": f"2026-01-01T00:00:{second:02d}.000Z",
        "sessionId": "s1",
        "message": {"content": content},
        **flags,
    }


def _text(text: str) -> dict[str, object]:
    return {"type": "text", "text": text}


def _codex(second: int, kind: str, payload: dict[str, object]) -> dict[str, object]:
    return {"timestamp": f"2026-01-01T00:01:{second:02d}.000Z", "type": kind, "payload": payload}


def _codex_prompt(second: int, text: str) -> dict[str, object]:
    block: dict[str, object] = {"type": "text", "text": text, "text_elements": []}
    item: dict[str, object] = {"type": "UserMessage", "content": [block]}
    return _codex(second, "event_msg", {"type": "item_completed", "item": item})


def _pi(second: int, role: str, content: object) -> dict[str, object]:
    return {
        "type": "message",
        "timestamp": f"2026-01-01T00:02:{second:02d}.000Z",
        "message": {"role": role, "content": content},
    }


def _seed(sandbox: Sandbox) -> None:
    tool_use = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}
    _ = sandbox.write_log(
        "claude/projects/p/s1.jsonl",
        [
            _claude("user", 1, "first prompt"),
            _claude("assistant", 2, [_text("reply a"), _text("reply b")]),
            _claude("assistant", 3, [tool_use]),
            _claude("user", 4, [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
            _claude("user", 5, "meta text", isMeta=True),
            _claude("user", 6, "summary text", isCompactSummary=True),
            _claude("assistant", 7, [_text("sidechain reply")], isSidechain=True),
            _claude("user", 8, "sidechain prompt", isSidechain=True),
            _claude("user", 9, "<command-name>/clear</command-name>"),
            _claude("user", 10, "[Request interrupted by user]"),
            _claude("user", 11, [_text("[Request interrupted by user for tool use]")]),
            _claude("user", 12, [{"type": "image"}, _text(FULL_TEXT)]),
        ],
    )
    _ = sandbox.write_log(
        "codex/sessions/2026/01/01/rollout.jsonl",
        [
            _codex(0, "session_meta", {"id": "c1", "cwd": "/c"}),
            _codex(1, "response_item", {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "# AGENTS.md preamble"}]}),
            _codex_prompt(2, "codex first"),
            _codex(3, "event_msg", {"type": "task_complete", "last_agent_message": "codex answer"}),
            _codex_prompt(4, "codex second"),
            _codex(5, "event_msg", {"type": "turn_aborted", "reason": "interrupted"}),
            _codex(6, "event_msg", {"type": "turn_aborted", "reason": "replaced"}),
            _codex(7, "event_msg", {"type": "task_complete", "last_agent_message": None}),
            _codex_prompt(8, "codex third"),
        ],
    )
    _ = sandbox.write_log(
        "cursor/projects/x/agent-transcripts/u1/u1.jsonl",
        [{"role": "user", "message": {"content": [_text("cursor prompt")]}}],
    )
    _ = sandbox.write_log(
        ".omp/agent/sessions/-w/o1.jsonl",
        [
            {"type": "session", "id": "o1", "cwd": "/w"},
            _pi(1, "user", [_text("omp first")]),
            _pi(2, "assistant", [{"type": "toolCall", "id": "k1", "name": "bash", "arguments": "{}"}]),
            _pi(3, "toolResult", [_text("tool output")]),
            _pi(4, "assistant", [_text("omp answer")]),
            _pi(5, "user", "steering string"),
            _pi(6, "user", [_text("omp second")]),
        ],
    )
    ingested = sandbox.run("ingest", "--force")
    assert ingested.returncode == 0, ingested.stderr


@needs_duckdb
def test_user_turns_keep_real_prompts_with_full_text_and_prior_assistant_text(sandbox: Sandbox) -> None:
    _seed(sandbox)
    rows = sandbox.query(
        "SELECT harness, sessionId, timestamp, turn_index, text, prev_assistant_text FROM user_turns "
        + "ORDER BY harness, turn_index"
    )
    assert rows == [
        {"harness": "claude", "sessionId": "s1", "timestamp": "2026-01-01T00:00:01.000Z", "turn_index": 0,
         "text": "first prompt", "prev_assistant_text": None},
        {"harness": "claude", "sessionId": "s1", "timestamp": "2026-01-01T00:00:12.000Z", "turn_index": 1,
         "text": FULL_TEXT, "prev_assistant_text": "reply a\nreply b"},
        {"harness": "codex", "sessionId": "c1", "timestamp": "2026-01-01T00:01:02.000Z", "turn_index": 0,
         "text": "codex first", "prev_assistant_text": None},
        {"harness": "codex", "sessionId": "c1", "timestamp": "2026-01-01T00:01:04.000Z", "turn_index": 1,
         "text": "codex second", "prev_assistant_text": "codex answer"},
        {"harness": "codex", "sessionId": "c1", "timestamp": "2026-01-01T00:01:08.000Z", "turn_index": 2,
         "text": "codex third", "prev_assistant_text": None},
        {"harness": "omp", "sessionId": "o1", "timestamp": "2026-01-01T00:02:01.000Z", "turn_index": 0,
         "text": "omp first", "prev_assistant_text": None},
        {"harness": "omp", "sessionId": "o1", "timestamp": "2026-01-01T00:02:06.000Z", "turn_index": 1,
         "text": "omp second", "prev_assistant_text": "omp answer"},
    ]


@needs_duckdb
def test_turn_index_orders_by_timestamp_then_file_order(sandbox: Sandbox) -> None:
    same = "2026-01-01T00:00:05.000Z"
    _ = sandbox.write_log(
        "claude/projects/p/a.jsonl",
        [
            {"type": "user", "timestamp": "2026-01-01T00:00:09.000Z", "sessionId": "s1", "message": {"content": "late"}},
            {"type": "user", "timestamp": same, "sessionId": "s1", "message": {"content": "tie one"}},
            {"type": "user", "timestamp": same, "sessionId": "s1", "message": {"content": "tie two"}},
            {"type": "user", "timestamp": "2026-01-01T00:00:01.000Z", "sessionId": "s2", "message": {"content": "other"}},
        ],
    )
    assert sandbox.run("ingest", "--force").returncode == 0
    rows = sandbox.query("SELECT sessionId, turn_index, text FROM user_turns ORDER BY sessionId, turn_index")
    assert rows == [
        {"sessionId": "s1", "turn_index": 0, "text": "tie one"},
        {"sessionId": "s1", "turn_index": 1, "text": "tie two"},
        {"sessionId": "s1", "turn_index": 2, "text": "late"},
        {"sessionId": "s2", "turn_index": 0, "text": "other"},
    ]


@needs_duckdb
def test_interrupt_events_come_from_claude_markers_and_codex_interrupted_aborts(sandbox: Sandbox) -> None:
    _seed(sandbox)
    rows = sandbox.query("SELECT harness, sessionId, timestamp FROM interrupt_events ORDER BY timestamp")
    assert rows == [
        {"harness": "claude", "sessionId": "s1", "timestamp": "2026-01-01T00:00:10.000Z"},
        {"harness": "claude", "sessionId": "s1", "timestamp": "2026-01-01T00:00:11.000Z"},
        {"harness": "codex", "sessionId": "c1", "timestamp": "2026-01-01T00:01:05.000Z"},
    ]
    view = sandbox.query("SELECT count(*) AS n FROM duckdb_views() WHERE view_name = 'interrupt_events'")
    assert view == [{"n": 1}]


@needs_duckdb
def test_schema_version_two_is_stamped(sandbox: Sandbox) -> None:
    _seed(sandbox)
    assert sandbox.query("SELECT version FROM schema_info") == [{"version": 2}]

"""Adversarial edge cases for user_turns and interrupt_events, end to end through `ingest --force`.

Every prompt text is synthetic.
"""

from __future__ import annotations

from .conftest import Sandbox, needs_duckdb


def _text(text: object) -> dict[str, object]:
    return {"type": "text", "text": text}


def _claude(second: int, kind: str, content: object, **fields: object) -> dict[str, object]:
    return {
        "type": kind,
        "timestamp": f"2026-02-01T00:00:{second:02d}.000Z",
        "sessionId": "cs",
        "message": {"content": content},
        **fields,
    }


def _codex(second: int, payload: dict[str, object], kind: str = "event_msg") -> dict[str, object]:
    return {"timestamp": f"2026-02-01T00:01:{second:02d}.000Z", "type": kind, "payload": payload}


def _codex_user(second: int, content: object) -> dict[str, object]:
    return _codex(second, {"type": "item_completed", "item": {"type": "UserMessage", "content": content}})


def _pi(second: int, role: str, content: object, **fields: object) -> dict[str, object]:
    return {
        "type": "message",
        "timestamp": f"2026-02-01T00:02:{second:02d}.000Z",
        "message": {"role": role, "content": content, **fields},
    }


def _ingest(sandbox: Sandbox) -> None:
    result = sandbox.run("ingest", "--force")
    assert result.returncode == 0, result.stderr


def _turns(sandbox: Sandbox, harness: str) -> list[dict[str, object]]:
    return sandbox.query(
        "SELECT sessionId, turn_index, text, prev_assistant_text FROM user_turns "
        + f"WHERE harness = '{harness}' ORDER BY sessionId, turn_index"
    )


@needs_duckdb
def test_codex_event_shapes_with_missing_or_malformed_fields(sandbox: Sandbox) -> None:
    _ = sandbox.write_log(
        "codex/sessions/2026/02/01/rollout-edge.jsonl",
        [
            _codex(0, {"id": "cx", "cwd": "/c"}, kind="session_meta"),
            _codex_user(1, [_text("alpha")]),
            _codex(2, {"type": "task_complete", "last_agent_message": "answer one"}),
            _codex_user(3, [{"type": "image"}, _text("beta one"), _text("beta two")]),
            # A turn that ended without the key resets the prior text.
            _codex(4, {"type": "task_complete"}),
            _codex_user(5, "plain string content"),
            _codex_user(6, [_text(None), {"type": "text"}]),
            _codex_user(7, []),
            _codex(8, {"type": "item_completed", "item": {"type": "AgentMessage", "content": [_text("agent")]}}),
            _codex(9, {"type": "item_completed"}),
            _codex(10, {"type": "user_message", "message": "legacy event"}),
            _codex(11, {"type": "turn_aborted"}),
            _codex(12, {"type": "turn_aborted", "reason": 5}),
            _codex_user(13, [_text("gamma")]),
            _codex(14, {"type": "task_complete", "last_agent_message": "answer two"}),
            # A non-string last_agent_message also ends the turn without text.
            _codex(15, {"type": "task_complete", "last_agent_message": 42}),
            _codex_user(16, [_text("delta")]),
            # turn_aborted carries no started_at or turn_id here.
            _codex(17, {"type": "turn_aborted", "reason": "interrupted"}),
            _codex_user(18, [_text("epsilon")]),
        ],
    )
    _ingest(sandbox)
    assert _turns(sandbox, "codex") == [
        {"sessionId": "cx", "turn_index": 0, "text": "alpha", "prev_assistant_text": None},
        {"sessionId": "cx", "turn_index": 1, "text": "beta one\nbeta two", "prev_assistant_text": "answer one"},
        {"sessionId": "cx", "turn_index": 2, "text": "gamma", "prev_assistant_text": None},
        {"sessionId": "cx", "turn_index": 3, "text": "delta", "prev_assistant_text": None},
        # An aborted turn resets the prior text to NULL.
        {"sessionId": "cx", "turn_index": 4, "text": "epsilon", "prev_assistant_text": None},
    ]
    assert sandbox.query("SELECT harness, sessionId, timestamp FROM interrupt_events") == [
        {"harness": "codex", "sessionId": "cx", "timestamp": "2026-02-01T00:01:17.000Z"}
    ]


@needs_duckdb
def test_codex_abort_without_task_complete_resets_the_prior_answer(sandbox: Sandbox) -> None:
    _ = sandbox.write_log(
        "codex/sessions/2026/02/01/rollout-abort.jsonl",
        [
            _codex(0, {"id": "ca", "cwd": "/c"}, kind="session_meta"),
            _codex_user(1, [_text("first")]),
            _codex(2, {"type": "task_complete", "last_agent_message": "kept answer"}),
            _codex_user(3, [_text("second")]),
            # AC-3: the interrupted turn ended without last_agent_message.
            _codex(4, {"type": "turn_aborted", "reason": "interrupted"}),
            _codex_user(5, [_text("third")]),
        ],
    )
    _ingest(sandbox)
    assert [row["prev_assistant_text"] for row in _turns(sandbox, "codex")] == [None, "kept answer", None]


@needs_duckdb
def test_codex_subagent_rollouts_give_no_user_turns_or_interrupts(sandbox: Sandbox) -> None:
    spawn = {"subagent": {"thread_spawn": {"parent_thread_id": "cp"}}}
    _ = sandbox.write_log(
        "codex/sessions/2026/02/01/rollout-parent.jsonl",
        [
            _codex(0, {"id": "cp", "cwd": "/c", "source": "cli"}, kind="session_meta"),
            _codex_user(1, [_text("parent prompt")]),
            _codex(2, {"type": "turn_aborted", "reason": "interrupted"}),
        ],
    )
    _ = sandbox.write_log(
        "codex/sessions/2026/02/01/rollout-child.jsonl",
        [
            _codex(3, {"id": "cc", "cwd": "/c", "source": spawn}, kind="session_meta"),
            _codex_user(4, [_text("written by the parent agent")]),
            _codex(5, {"type": "task_complete", "last_agent_message": "child answer"}),
            _codex(6, {"type": "turn_aborted", "reason": "interrupted"}),
        ],
    )
    _ingest(sandbox)
    assert [(row["sessionId"], row["text"]) for row in _turns(sandbox, "codex")] == [("cp", "parent prompt")]
    assert sandbox.query("SELECT sessionId FROM interrupt_events") == [{"sessionId": "cp"}]
    # The sub-agent session itself stays in sessions.
    assert sandbox.query("SELECT parent_session_id FROM sessions WHERE sessionId = 'cc'") == [
        {"parent_session_id": "cp"}
    ]


@needs_duckdb
def test_claude_sidechain_interrupt_markers_are_not_interrupt_events(sandbox: Sandbox) -> None:
    _ = sandbox.write_log(
        "claude/projects/p/cs.jsonl",
        [
            _claude(1, "user", "[Request interrupted by user]", isSidechain=True),
            _claude(2, "user", [_text("[Request interrupted by user for tool use]")], isSidechain=True),
            _claude(3, "user", "[Request interrupted by user]"),
        ],
    )
    _ingest(sandbox)
    assert sandbox.query("SELECT timestamp FROM interrupt_events") == [{"timestamp": "2026-02-01T00:00:03.000Z"}]


@needs_duckdb
def test_blank_prompts_and_blank_answers_are_absent(sandbox: Sandbox) -> None:
    _ = sandbox.write_log(
        "claude/projects/p/cs.jsonl",
        [
            _claude(1, "user", ""),
            _claude(2, "user", [_text(" \n")]),
            _claude(3, "assistant", [_text("")]),
            _claude(4, "user", "claude real"),
        ],
    )
    _ = sandbox.write_log(
        "codex/sessions/2026/02/01/rollout-blank.jsonl",
        [
            _codex(0, {"id": "cb", "cwd": "/c"}, kind="session_meta"),
            _codex_user(1, [_text("")]),
            _codex(2, {"type": "task_complete", "last_agent_message": ""}),
            _codex_user(3, [_text("codex real")]),
        ],
    )
    _ = sandbox.write_log(
        ".pi/agent/sessions/-w/pb.jsonl",
        [{"type": "session", "id": "pb", "cwd": "/w"}, _pi(1, "user", "  "), _pi(2, "user", [_text("pi real")])],
    )
    _ingest(sandbox)
    rows = sandbox.query("SELECT harness, text, prev_assistant_text FROM user_turns ORDER BY harness")
    assert rows == [
        {"harness": "claude", "text": "claude real", "prev_assistant_text": None},
        {"harness": "codex", "text": "codex real", "prev_assistant_text": None},
        {"harness": "pi", "text": "pi real", "prev_assistant_text": None},
    ]


@needs_duckdb
def test_prev_assistant_text_never_crosses_a_session_or_a_file(sandbox: Sandbox) -> None:
    # One file that holds two sessions: the second session starts with NULL.
    _ = sandbox.write_log(
        "claude/projects/p/two.jsonl",
        [
            _claude(1, "user", "s1 first", sessionId="s1"),
            _claude(2, "assistant", [_text("s1 answer")], sessionId="s1"),
            {"type": "file-history-snapshot", "timestamp": "2026-02-01T00:00:03.000Z"},
            _claude(4, "user", "s1 second", sessionId="s1"),
            _claude(5, "user", "s2 first", sessionId="s2"),
        ],
    )
    # One session in two files: the second file starts with NULL, because
    # file order across files is not time order.
    _ = sandbox.write_log(
        "claude/projects/p/a.jsonl",
        [_claude(10, "user", "s3 a", sessionId="s3"), _claude(11, "assistant", [_text("s3 answer")], sessionId="s3")],
    )
    _ = sandbox.write_log("claude/projects/p/b.jsonl", [_claude(12, "user", "s3 b", sessionId="s3")])
    _ingest(sandbox)
    assert [(row["sessionId"], row["text"], row["prev_assistant_text"]) for row in _turns(sandbox, "claude")] == [
        ("s1", "s1 first", None),
        ("s1", "s1 second", "s1 answer"),
        ("s2", "s2 first", None),
        ("s3", "s3 a", None),
        ("s3", "s3 b", None),
    ]


@needs_duckdb
def test_claude_exclusion_rules_and_interrupt_marker_variants(sandbox: Sandbox) -> None:
    tool_result = {"type": "tool_result", "tool_use_id": "t1", "content": "[Request interrupted by user for tool use]"}
    _ = sandbox.write_log(
        "claude/projects/p/cs.jsonl",
        [
            _claude(1, "user", "kept explicit flags", isMeta=False, isSidechain=False, isCompactSummary=False),
            _claude(2, "user", [_text("compact list")], isCompactSummary=True),
            _claude(3, "user", [_text("meta list")], isMeta=True),
            _claude(4, "user", [tool_result]),
            _claude(5, "user", [tool_result, _text("feedback with a result")]),
            _claude(6, "user", []),
            _claude(7, "user", [{"type": "image"}]),
            {"type": "user", "timestamp": "2026-02-01T00:00:08.000Z", "sessionId": "cs"},
            _claude(9, "user", "[Request interrupted"),
            _claude(10, "user", [{"type": "image"}, _text("[Request interrupted by user]")]),
            _claude(11, "user", " [Request interrupted by user]"),
            _claude(12, "user", "[request interrupted by user]"),
            _claude(13, "user", [_text("real words"), _text("[Request interrupted by user]")]),
            _claude(14, "user", "<local-command-stdout>x</local-command-stdout>"),
            _claude(15, "user", [_text("<system-reminder>x</system-reminder>")]),
        ],
    )
    _ingest(sandbox)
    assert [row["text"] for row in _turns(sandbox, "claude")] == [
        "kept explicit flags",
        "feedback with a result",
        " [Request interrupted by user]",
        "[request interrupted by user]",
        "real words\n[Request interrupted by user]",
    ]
    assert sandbox.query("SELECT timestamp FROM interrupt_events ORDER BY timestamp") == [
        {"timestamp": "2026-02-01T00:00:09.000Z"},
        {"timestamp": "2026-02-01T00:00:10.000Z"},
    ]


@needs_duckdb
def test_claude_prev_assistant_text_skips_textless_and_sidechain_entries(sandbox: Sandbox) -> None:
    tool_use: dict[str, object] = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}
    _ = sandbox.write_log(
        "claude/projects/p/cs.jsonl",
        [
            _claude(1, "assistant", [tool_use]),
            _claude(2, "assistant", "string content is not a text block"),
            _claude(3, "assistant", [_text(None)]),
            _claude(4, "user", "one"),
            _claude(5, "assistant", [{"type": "thinking", "thinking": "hidden"}, _text("visible")]),
            _claude(6, "assistant", [tool_use]),
            _claude(7, "assistant", [_text("side")], isSidechain=True),
            _claude(8, "user", "meta", isMeta=True),
            _claude(9, "user", "two"),
            _claude(10, "user", "three"),
        ],
    )
    _ingest(sandbox)
    assert [(row["text"], row["prev_assistant_text"]) for row in _turns(sandbox, "claude")] == [
        ("one", None),
        ("two", "visible"),
        ("three", "visible"),
    ]


@needs_duckdb
def test_turn_index_breaks_ties_by_numeric_file_position(sandbox: Sandbox) -> None:
    same = "2026-02-01T00:00:05.000Z"
    entries: list[dict[str, object]] = [
        {"type": "user", "timestamp": same, "sessionId": "cs", "message": {"content": f"p{n:02d}"}}
        for n in range(12)
    ]
    _ = sandbox.write_log("claude/projects/p/cs.jsonl", entries)
    _ingest(sandbox)
    assert [row["text"] for row in _turns(sandbox, "claude")] == [f"p{n:02d}" for n in range(12)]


@needs_duckdb
def test_turn_index_orders_by_instant_then_source_path_then_missing_times_last(sandbox: Sandbox) -> None:
    def entry(stamp: str | None, text: str) -> dict[str, object]:
        row: dict[str, object] = {"type": "user", "sessionId": "cs", "message": {"content": text}}
        if stamp is not None:
            row["timestamp"] = stamp
        return row

    _ = sandbox.write_log(
        "claude/projects/p/b.jsonl",
        [
            entry(None, "no time"),
            entry("not a time", "bad time"),
            entry("2026-02-01T00:00:05Z", "tie in b"),
            # 01:00 at +02:00 is 23:00 UTC on the day before.
            entry("2026-02-01T01:00:00+02:00", "offset earliest"),
        ],
    )
    _ = sandbox.write_log(
        "claude/projects/p/a.jsonl",
        [entry("2026-02-01T00:00:05.000Z", "tie in a")],
    )
    _ingest(sandbox)
    texts = [row["text"] for row in _turns(sandbox, "claude")]
    assert texts[:3] == ["offset earliest", "tie in a", "tie in b"]
    assert texts[3:] == ["no time", "bad time"]


@needs_duckdb
def test_pi_harness_applies_the_attribution_rule_and_prior_text(sandbox: Sandbox) -> None:
    _ = sandbox.write_log(
        ".pi/agent/sessions/-w/p1.jsonl",
        [
            {"type": "session", "id": "p1", "cwd": "/w"},
            _pi(1, "user", [_text("pi first")], attribution=None),
            _pi(2, "assistant", [{"type": "thinking", "thinking": "x"}, {"type": "toolCall", "id": "k", "name": "bash"}]),
            _pi(3, "user", [_text("pi second")]),
            _pi(4, "assistant", [_text("pi answer")], stopReason="aborted"),
            _pi(5, "user", [_text("agent follow up")], attribution="agent"),
            _pi(6, "assistant", [{"type": "toolCall", "id": "k2", "name": "bash"}]),
            _pi(7, "user", "pi third", attribution="user"),
        ],
    )
    _ingest(sandbox)
    assert _turns(sandbox, "pi") == [
        {"sessionId": "p1", "turn_index": 0, "text": "pi first", "prev_assistant_text": None},
        {"sessionId": "p1", "turn_index": 1, "text": "pi second", "prev_assistant_text": None},
        {"sessionId": "p1", "turn_index": 2, "text": "pi third", "prev_assistant_text": "pi answer"},
    ]
    assert sandbox.query("SELECT count(*) AS n FROM interrupt_events") == [{"n": 0}]

"""Codex adapter and end-to-end ingest behavior, ported from the dotfiles suite."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import cast

from agent_session_analytics import _adapters, _tables

from .conftest import Sandbox, needs_duckdb

Row = dict[str, object]


def sub(entry: Row, key: str) -> Row:
    """Return the nested object stored under `key`."""
    return cast(Row, entry[key])


def usage(inp: int, out: int, cached: int) -> Row:
    return {
        "input_tokens": inp,
        "cached_input_tokens": cached,
        "output_tokens": out,
        "total_tokens": inp + out,
    }


def token_count(ts: str, last: Row | None, total: Row | None) -> Row:
    info = None
    if last is not None or total is not None:
        info = {"last_token_usage": last, "total_token_usage": total}
    return {
        "timestamp": ts,
        "type": "event_msg",
        "payload": {"type": "token_count", "info": info},
    }


def meta(version: str | None = "9.9.9") -> Row:
    payload: Row = {"id": "s-1", "cwd": "/work/x"}
    if version is not None:
        payload["cli_version"] = version
    return {"timestamp": "t0", "type": "session_meta", "payload": payload}


def turn_context(model: str) -> Row:
    return {
        "timestamp": "t1",
        "type": "turn_context",
        "payload": {"cwd": "/work/x", "model": model},
    }


def call(call_id: str) -> Row:
    return {
        "timestamp": "t2",
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "shell",
            "arguments": json.dumps({"command": ["ls"]}),
            "call_id": call_id,
        },
    }


def output(call_id: str) -> Row:
    return {
        "timestamp": "t3",
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": "ok",
        },
    }


def normalize(rows: list[Row]) -> list[Row]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rollout.jsonl"
        _ = path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return list(_adapters.codex_normalize(str(path)))


def token_turns(entries: list[Row]) -> list[Row]:
    return [e for e in entries if "usage" in cast(Row, e.get("message", {}))]


class TestCodexIngest(unittest.TestCase):
    def test_cli_version_becomes_raw_entries_version(self) -> None:
        entries = normalize(
            [
                meta("0.159.3"),
                turn_context("gpt-x"),
                call("c1"),
                output("c1"),
                token_count("t4", usage(10, 2, 4), usage(10, 2, 4)),
            ]
        )
        self.assertEqual(len(entries), 3)
        self.assertEqual({e["version"] for e in entries}, {"0.159.3"})
        self.assertIn("version", _tables.RAW_COLUMNS)

    def test_missing_cli_version_yields_null_version(self) -> None:
        entries = normalize([meta(None), call("c1")])
        self.assertIsNone(entries[0]["version"])

    def test_token_count_yields_one_model_turn_entry(self) -> None:
        entries = normalize(
            [
                meta(),
                turn_context("gpt-x"),
                token_count("t4", usage(100, 7, 40), usage(100, 7, 40)),
            ]
        )
        (turn,) = token_turns(entries)
        self.assertEqual(turn["type"], "token_usage")
        self.assertEqual(turn["harness"], "codex")
        self.assertEqual(turn["sessionId"], "s-1")
        msg = sub(turn, "message")
        self.assertEqual(msg["model"], "gpt-x")
        self.assertEqual(
            msg["usage"],
            {
                "input_tokens": 100,
                "output_tokens": 7,
                "cache_read_input_tokens": 40,
            },
        )

    def test_token_count_uses_latest_turn_context_model(self) -> None:
        entries = normalize(
            [
                meta(),
                turn_context("gpt-a"),
                token_count("t4", usage(1, 1, 0), usage(1, 1, 0)),
                turn_context("gpt-b"),
                token_count("t5", usage(2, 2, 0), usage(3, 3, 0)),
            ]
        )
        models = [sub(e, "message")["model"] for e in token_turns(entries)]
        self.assertEqual(models, ["gpt-a", "gpt-b"])

    def test_token_count_skips_repeated_total_usage(self) -> None:
        entries = normalize(
            [
                meta(),
                turn_context("gpt-x"),
                token_count("t4", usage(5, 1, 0), usage(5, 1, 0)),
                token_count("t5", usage(5, 1, 0), usage(5, 1, 0)),
                token_count("t6", usage(6, 2, 0), usage(11, 3, 0)),
            ]
        )
        turns = token_turns(entries)
        self.assertEqual(len(turns), 2)
        self.assertEqual(sub(sub(turns[1], "message"), "usage")["input_tokens"], 6)

    def test_token_count_null_last_usage_yields_nothing(self) -> None:
        entries = normalize(
            [
                meta(),
                turn_context("gpt-x"),
                token_count("t4", None, None),
                token_count("t5", None, usage(5, 1, 0)),
            ]
        )
        self.assertEqual(entries, [])

    def test_token_count_ids_are_unique_and_leave_tool_blocks_alone(self) -> None:
        entries = normalize(
            [
                meta(),
                turn_context("gpt-x"),
                call("c1"),
                token_count("t4", usage(1, 1, 0), usage(1, 1, 0)),
                output("c1"),
                token_count("t5", usage(2, 1, 0), usage(3, 2, 0)),
            ]
        )
        turns = token_turns(entries)
        ids = [sub(e, "message")["id"] for e in turns]
        self.assertEqual(len(set(ids)), 2)
        blocks = [b for e in entries for b in cast(list[Row], sub(e, "message")["content"])]
        self.assertEqual(sorted(str(b["type"]) for b in blocks), ["tool_result", "tool_use"])
        for turn in turns:
            self.assertEqual(sub(turn, "message")["content"], [])
            self.assertNotIn("stop_reason", sub(turn, "message"))

    def test_forked_rollout_keeps_first_meta_id_and_unique_token_ids(self) -> None:
        def session_meta(sid: str) -> Row:
            return {
                "timestamp": "t0",
                "type": "session_meta",
                "payload": {"id": sid, "cwd": "/work/x"},
            }

        body = [
            turn_context("gpt-x"),
            call("c1"),
            token_count("t4", usage(1, 1, 0), usage(1, 1, 0)),
        ]
        parent = normalize([session_meta("parent-1"), *body])
        fork = normalize([session_meta("child-1"), session_meta("parent-1"), *body])
        self.assertEqual({e["sessionId"] for e in fork}, {"child-1"})
        parent_ids = {sub(e, "message")["id"] for e in token_turns(parent)}
        fork_ids = {sub(e, "message")["id"] for e in token_turns(fork)}
        self.assertEqual(len(fork_ids), 1)
        self.assertFalse(parent_ids & fork_ids)

    def test_sub_agent_rollout_records_its_parent_session(self) -> None:
        def session_meta(sid: str, source: object) -> Row:
            payload: Row = {"id": sid, "cwd": "/work/x"}
            if source is not None:
                payload["source"] = source
            return {"timestamp": "t0", "type": "session_meta", "payload": payload}

        spawn = {"subagent": {"thread_spawn": {"parent_thread_id": "parent-1"}}}
        body = [call("c1"), token_count("t4", usage(1, 1, 0), usage(1, 1, 0))]
        child = normalize([session_meta("child-1", spawn), *body])
        self.assertEqual({e["sessionId"] for e in child}, {"child-1"})
        self.assertEqual({e["parentSessionId"] for e in child}, {"parent-1"})
        self.assertIn("parentSessionId", _tables.RAW_COLUMNS)
        sources: list[object] = [None, "cli", {"subagent": "review"}]
        for source in sources:
            top = normalize([session_meta("top-1", source), *body])
            self.assertEqual({e["parentSessionId"] for e in top}, {None})

    def test_parent_meta_of_a_fork_does_not_set_the_parent_link(self) -> None:
        def session_meta(sid: str, source: object) -> Row:
            return {
                "timestamp": "t0",
                "type": "session_meta",
                "payload": {"id": sid, "cwd": "/work/x", "source": source},
            }

        spawn = {"subagent": {"thread_spawn": {"parent_thread_id": "gp-1"}}}
        fork = normalize(
            [
                session_meta("child-1", "cli"),
                session_meta("parent-1", spawn),
                call("c1"),
            ]
        )
        self.assertEqual({e["parentSessionId"] for e in fork}, {None})


class TestCodexNormalizeAttacks(unittest.TestCase):
    def test_tokenCount_beforeAnySessionMeta_doesNotCrashAndLaterEntriesCarryVersion(
        self,
    ) -> None:
        entries = normalize(
            [
                token_count("t0", usage(1, 1, 0), usage(1, 1, 0)),
                meta("1.2.3"),
                turn_context("gpt-x"),
                call("c1"),
            ]
        )
        self.assertEqual(entries[-1]["version"], "1.2.3")
        self.assertIsNone(entries[0]["version"])

    def test_tokenCount_beforeAnySessionMeta_isNotAttributedToASession(self) -> None:
        entries = normalize([token_count("t0", usage(1, 1, 0), usage(1, 1, 0)), meta()])
        for turn in token_turns(entries):
            self.assertIsNone(turn["sessionId"])

    def test_tokenCount_beforeTurnContext_stillCarriesAModelForModelTurns(self) -> None:
        # model_turns keeps only rows whose message.model is not null (the model_turns SQL).
        entries = normalize([meta(), token_count("t0", usage(5, 1, 0), usage(5, 1, 0))])
        (turn,) = token_turns(entries)
        self.assertIsNotNone(sub(turn, "message")["model"])

    def test_tokenCount_totalEqualsNonAdjacentEarlierEvent_isEmitted(self) -> None:
        a, b = usage(5, 1, 0), usage(9, 2, 0)
        entries = normalize(
            [
                meta(),
                turn_context("m"),
                token_count("t1", a, a),
                token_count("t2", b, b),
                token_count("t3", a, a),
            ]
        )
        self.assertEqual(len(token_turns(entries)), 3)

    def test_tokenCount_threeAdjacentRepeats_emitOnce(self) -> None:
        a = usage(5, 1, 0)
        entries = normalize(
            [meta(), turn_context("m")] + [token_count(f"t{i}", a, a) for i in range(3)]
        )
        self.assertEqual(len(token_turns(entries)), 1)

    def test_tokenCount_lastUsageAllZeros_isStillARow(self) -> None:
        z = usage(0, 0, 0)
        entries = normalize([meta(), turn_context("m"), token_count("t1", z, usage(3, 3, 0))])
        (turn,) = token_turns(entries)
        turn_usage = sub(sub(turn, "message"), "usage")
        self.assertEqual(turn_usage["input_tokens"], 0)
        self.assertEqual(turn_usage["output_tokens"], 0)

    def test_tokenCount_totalMissingOnBothEvents_bothEmitted(self) -> None:
        a = usage(5, 1, 0)
        entries = normalize(
            [
                meta(),
                turn_context("m"),
                token_count("t1", a, None),
                token_count("t2", a, None),
            ]
        )
        self.assertEqual(len(token_turns(entries)), 2)

    def test_tokenCount_totalRepeatSeparatedByNullInfoEvent_isStillSkipped(
        self,
    ) -> None:
        a = usage(5, 1, 0)
        entries = normalize(
            [
                meta(),
                turn_context("m"),
                token_count("t1", a, a),
                token_count("t2", None, None),
                token_count("t3", a, a),
            ]
        )
        self.assertEqual(len(token_turns(entries)), 1)

    def test_tokenCount_totalChangedByNullLastEvent_thenOldTotal_isEmitted(
        self,
    ) -> None:
        # t2 moves the total without a last_token_usage; t3 repeats t1's total, not t2's.
        a, b = usage(5, 1, 0), usage(9, 2, 0)
        entries = normalize(
            [
                meta(),
                turn_context("m"),
                token_count("t1", a, a),
                token_count("t2", None, b),
                token_count("t3", a, a),
            ]
        )
        self.assertEqual(len(token_turns(entries)), 2, "t3 differs from the previous event (t2)")

    def test_tokenCount_malformedInfoShapes_areIgnored(self) -> None:
        rows = [meta(), turn_context("m")]
        infos: list[object] = [[], "x", 5, {"last_token_usage": "x"}, {"last_token_usage": []}]
        for info in infos:
            rows.append(
                {
                    "timestamp": "t",
                    "type": "event_msg",
                    "payload": {"type": "token_count", "info": info},
                }
            )
        rows.append({"timestamp": "t", "type": "event_msg", "payload": None})
        rows.append({"timestamp": "t", "type": "event_msg", "payload": {"type": "token_count"}})
        self.assertEqual(token_turns(normalize(rows)), [])

    def test_cliVersion_appearsAfterEarlierEntries_earlierEntriesStayNull(self) -> None:
        entries = normalize(
            [
                meta(None),
                turn_context("m"),
                call("c1"),
                output("c1"),
                meta("4.5.6"),
                call("c2"),
            ]
        )
        self.assertEqual([e["version"] for e in entries], [None, None, "4.5.6"])

    def test_cliVersion_laterMetaWithoutVersionKeepsEarlierOne(self) -> None:
        entries = normalize([meta("1.0.0"), meta(None), call("c1")])
        self.assertEqual(entries[0]["version"], "1.0.0")

    def test_cliVersion_emptyString_isNull(self) -> None:
        self.assertIsNone(normalize([meta(""), call("c1")])[0]["version"])

    def test_cliVersion_nonStringValue_doesNotCrash(self) -> None:
        entries = normalize(
            [
                {
                    "timestamp": "t",
                    "type": "session_meta",
                    "payload": {"cli_version": 5},
                },
                meta(),
                call("c1"),
            ]
        )
        self.assertIsNotNone(entries[0]["version"])

    def test_tokenTurnIds_uniqueAcrossSessionsSharingNoId(self) -> None:
        a = normalize(
            [
                meta(),
                turn_context("m"),
                token_count("t", usage(1, 1, 0), usage(1, 1, 0)),
            ]
        )
        b_meta = meta()
        sub(b_meta, "payload")["id"] = "s-2"
        b = normalize(
            [
                b_meta,
                turn_context("m"),
                token_count("t", usage(1, 1, 0), usage(1, 1, 0)),
            ]
        )
        self.assertNotEqual(sub(a[0], "message")["id"], sub(b[0], "message")["id"])

    def test_malformedJsonlLineInMiddle_doesNotDropLaterTokenRows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.jsonl"
            good = [
                meta(),
                turn_context("m"),
                token_count("t1", usage(1, 1, 0), usage(1, 1, 0)),
            ]
            _ = path.write_text(
                json.dumps(good[0])
                + "\n{not json\n"
                + "".join(json.dumps(r) + "\n" for r in good[1:])
            )
            entries = list(_adapters.codex_normalize(str(path)))
        self.assertEqual(len(token_turns(entries)), 1)

    def test_tokenTurnIds_filesWithoutSessionMeta_stayDistinctPerFile(self) -> None:
        rows = [token_count("t", usage(1, 1, 0), usage(1, 1, 0))]
        ids: list[object] = []
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a.jsonl", "b.jsonl"):
                path = Path(tmp) / name
                _ = path.write_text("".join(json.dumps(r) + "\n" for r in rows))
                found = list(_adapters.codex_normalize(str(path)))
                ids.extend(sub(t, "message")["id"] for t in token_turns(found))
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_forkedRollout_keepsFirstSessionMetaVersionAndCwd(self) -> None:
        child, parent = meta("1.0.0"), meta("2.0.0")
        sub(child, "payload").update(id="child", cwd="/child")
        sub(parent, "payload").update(id="parent", cwd="/parent")
        entries = normalize([child, parent, call("c1")])
        self.assertEqual(entries[-1]["version"], "1.0.0")
        self.assertEqual(entries[-1]["cwd"], "/child")


def _claude_entry(ts: str, version: str) -> Row:
    return {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": "c-1",
        "cwd": "/w",
        "version": version,
        "message": {"content": [{"type": "text", "text": "x"}]},
    }


def _codex_rollout(*rows: Row) -> list[Row]:
    return [
        {
            "timestamp": "2026-05-30T11:00:00Z",
            "type": "session_meta",
            "payload": {"id": "x-1", "cwd": "/w"},
        },
        *rows,
    ]


@needs_duckdb
def test_sessions_version_session_spanning_numeric_versions_reports_the_latest(
    sandbox: Sandbox,
) -> None:
    _ = sandbox.write_log(
        "claude/projects/p/s.jsonl",
        [
            _claude_entry("2026-05-30T10:00:00Z", "2.1.9"),
            _claude_entry("2026-05-30T10:05:00Z", "2.1.10"),
        ],
    )
    assert sandbox.run("ingest", "--force").returncode == 0
    rows = sandbox.query("SELECT version FROM sessions WHERE harness='claude'")
    assert rows == [{"version": "2.1.10"}]


@needs_duckdb
def test_sessions_version_codex_session_without_cli_version_is_null(sandbox: Sandbox) -> None:
    _ = sandbox.write_log(
        "codex/sessions/2026/05/30/r.jsonl",
        _codex_rollout(
            {
                "timestamp": "2026-05-30T11:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "shell",
                    "arguments": '{"command":["ls"]}',
                    "call_id": "c",
                },
            }
        ),
    )
    assert sandbox.run("ingest", "--force").returncode == 0
    rows = sandbox.query("SELECT version FROM sessions WHERE harness='codex'")
    assert rows == [{"version": None}]


@needs_duckdb
def test_model_turns_codex_token_count_before_turn_context_still_becomes_a_row(
    sandbox: Sandbox,
) -> None:
    rollout = _codex_rollout(
        token_count("2026-05-30T11:00:01Z", usage(10, 2, 0), usage(10, 2, 0))
    )
    sub(rollout[0], "payload")["cli_version"] = "0.1.0"
    _ = sandbox.write_log("codex/sessions/2026/05/30/r.jsonl", rollout)
    assert sandbox.run("ingest", "--force").returncode == 0
    rows = sandbox.query("SELECT count(*) AS n FROM model_turns WHERE harness='codex'")
    assert rows == [{"n": 1}]


@needs_duckdb
def test_model_turns_two_codex_sessions_keep_separate_token_rows(sandbox: Sandbox) -> None:
    for i in (1, 2):
        rollout = _codex_rollout(
            {
                "timestamp": "2026-05-30T11:00:01Z",
                "type": "turn_context",
                "payload": {"cwd": "/w", "model": "m"},
            },
            token_count("2026-05-30T11:00:02Z", usage(10, 2, 0), usage(10, 2, 0)),
        )
        sub(rollout[0], "payload").update(id=f"x-{i}", cli_version="0.1.0")
        _ = sandbox.write_log(f"codex/sessions/2026/05/30/r{i}.jsonl", rollout)
    assert sandbox.run("ingest", "--force").returncode == 0
    rows = sandbox.query(
        "SELECT count(*) AS n, count(DISTINCT sessionId) AS s "
        + "FROM model_turns WHERE harness='codex'"
    )
    assert rows == [{"n": 2, "s": 2}]


@needs_duckdb
def test_model_turns_claude_context_tokens_add_cache_reads_and_creation(
    sandbox: Sandbox,
) -> None:
    claude_turn: Row = {
        "type": "assistant",
        "timestamp": "2026-05-30T10:00:00Z",
        "sessionId": "c-1",
        "cwd": "/w",
        "message": {
            "id": "m1",
            "model": "claude-x",
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 1000,
            },
            "content": [{"type": "text", "text": "x"}],
        },
    }
    _ = sandbox.write_log("claude/projects/p/s.jsonl", [claude_turn])
    assert sandbox.run("ingest", "--force").returncode == 0
    rows = sandbox.query(
        "SELECT input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
        + "context_tokens, tool_calls FROM model_turns WHERE harness='claude'"
    )
    assert rows == [
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_tokens": 100,
            "cache_creation_tokens": 1000,
            "context_tokens": 1110,
            "tool_calls": 0,
        }
    ]


@needs_duckdb
def test_model_turns_codex_tool_calls_are_null_and_context_tokens_stay_the_input(
    sandbox: Sandbox,
) -> None:
    rollout = _codex_rollout(
        turn_context("gpt-x"),
        token_count("2026-05-30T11:00:02Z", usage(10, 2, 4), usage(10, 2, 4)),
    )
    _ = sandbox.write_log("codex/sessions/2026/05/30/r.jsonl", rollout)
    assert sandbox.run("ingest", "--force").returncode == 0
    rows = sandbox.query(
        "SELECT tool_calls IS NULL AS unmeasured, context_tokens, cache_read_tokens "
        + "FROM model_turns WHERE harness='codex'"
    )
    assert rows == [{"unmeasured": True, "context_tokens": 10, "cache_read_tokens": 4}]

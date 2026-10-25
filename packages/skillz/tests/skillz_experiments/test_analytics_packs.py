"""The analytics packs: static text rules, and their SQL run against the built agent-session-analytics bundle."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeAlias, cast

import pytest

SKILL_DIR = Path(__file__).resolve().parents[4] / "skills/skillz"
PACKS = SKILL_DIR / "references"
CONVENTIONS = PACKS / "query-conventions.md"
BUNDLE = SKILL_DIR / "scripts" / "agent-session-analytics.pyz"
PACK_NAMES = ("skill-usage.md", "agent-orchestration.md", "drift-regression.md")
SQL_BLOCK = re.compile(r"```sql\n(.*?)```", re.S)

REQUIRE_DUCKDB = os.environ.get("REQUIRE_DUCKDB") == "1"
needs_duckdb = pytest.mark.skipif(
    not (REQUIRE_DUCKDB or shutil.which("duckdb")), reason="DuckDB CLI unavailable"
)

RowValue: TypeAlias = str | int | float | bool | None
Row: TypeAlias = dict[str, RowValue]


def _start_tables() -> dict[str, tuple[str, str]]:
    """Read the kind-to-table mapping from its single source, the conventions file."""
    matches = re.finditer(r"^- `(\w+)`: `(\w+)`, `(\w+)`$", CONVENTIONS.read_text(), re.M)
    return {match.group(1): (match.group(2), match.group(3)) for match in matches}


START = _start_tables()


def _pack_sql(pack: str, kind: str, target: str) -> list[str]:
    table, column = START[kind]
    return [
        match.group(1)
        .replace("{START_TABLE}", table)
        .replace("{NAME_COLUMN}", column)
        .replace("{TARGET}", target)
        for match in SQL_BLOCK.finditer((PACKS / pack).read_text())
    ]


def test_mapping_has_one_source_with_both_kinds() -> None:
    assert set(START) == {"skill", "agent"}
    for pack in PACK_NAMES:
        text = (PACKS / pack).read_text()
        assert "the `conventions` input defines" in text, pack
        for table, column in START.values():
            assert f"`{table}`, `{column}`" not in text, pack


@pytest.mark.parametrize("pack", PACK_NAMES)
def test_every_pack_query_starts_from_the_start_table(pack: str) -> None:
    blocks = [match.group(1) for match in SQL_BLOCK.finditer((PACKS / pack).read_text())]
    assert blocks, pack
    for block in blocks:
        assert "{START_TABLE}" in block, block[:60]
        assert "skill_invocations" not in block, block[:60]


@pytest.mark.parametrize("pack", PACK_NAMES)
def test_packs_state_the_unavailable_rule(pack: str) -> None:
    assert "as `unavailable`, never as 0" in (PACKS / pack).read_text()


def test_drift_pack_separates_unavailable_from_dormant() -> None:
    assert "`unavailable`, not `dormant`" in (PACKS / "drift-regression.md").read_text()


def test_ceremony_prompt_carries_target_kind_and_the_data_inputs() -> None:
    text = (PACKS / "analytics-ceremony.md").read_text()
    assert "target_kind=" in text
    for name in ("schema=", "conventions=", "coverage=", "database="):
        assert name in text, name
    assert "ingest=" not in text


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("packs").resolve()
    logs = root / "claude" / "projects" / "sample"
    logs.mkdir(parents=True)
    base = datetime.now(timezone.utc) - timedelta(days=3)

    def stamp(minutes: int) -> str:
        return (base + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def use(minutes: int, call_id: str, name: str, tool_input: dict[str, str]) -> dict[str, object]:
        return {
            "type": "assistant",
            "timestamp": stamp(minutes),
            "sessionId": "fixture-session",
            "cwd": str(root / "project"),
            "message": {
                "content": [{"type": "tool_use", "name": name, "id": call_id, "input": tool_input}]
            },
        }

    def result(minutes: int, call_id: str, content: str, is_error: bool = False) -> dict[str, object]:
        block: dict[str, RowValue] = {
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": content,
        }
        if is_error:
            block["is_error"] = True
        return {
            "type": "user",
            "timestamp": stamp(minutes),
            "sessionId": "fixture-session",
            "cwd": str(root / "project"),
            "message": {"content": [block]},
        }

    # Skill at minute 0, reviewer spawn at 1. Inside the spawn window: an
    # error Read at 2 and an MCP call at 3. Outside it: a Read at 30.
    entries = [
        use(0, "skill-call", "Skill", {"skill": "skillz"}),
        use(1, "agent-call", "Agent", {"subagent_type": "reviewer", "description": "review"}),
        result(1, "agent-call", "spawned"),
        use(2, "read-in", "Read", {"file_path": "a"}),
        result(2, "read-in", "boom", is_error=True),
        use(3, "mcp-in", "mcp__srv__tool", {}),
        result(3, "mcp-in", "ok"),
        use(30, "read-out", "Read", {"file_path": "b"}),
        result(30, "read-out", "fine"),
    ]
    _ = (logs / "session.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))

    # Cursor logs carry minute-resolution timestamps: one user turn stamps
    # the spawn, the Read, and the second spawn with the same time.
    transcripts = root / "cursor" / "projects" / "sample" / "agent-transcripts"
    transcripts.mkdir(parents=True)

    def turn(role: str, content: list[dict[str, object]]) -> dict[str, object]:
        return {"role": role, "message": {"content": content}}

    def call(name: str, tool_input: dict[str, str]) -> dict[str, object]:
        return turn("assistant", [{"type": "tool_use", "name": name, "input": tool_input}])

    user = turn(
        "user",
        [{"type": "text", "text": "<timestamp>Monday, Jan 5, 2026, 10:00 AM (UTC+0)</timestamp> go"}],
    )
    shared = [
        user,
        call("Task", {"subagent_type": "cursor-reviewer", "description": "review"}),
        call("Read", {"file_path": "a"}),
        call("Task", {"subagent_type": "helper", "description": "help"}),
    ]
    _ = (transcripts / "cursor-shared.jsonl").write_text("".join(json.dumps(e) + "\n" for e in shared))
    _ = (transcripts / "cursor-readonly.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in (user, call("Read", {"file_path": "c"})))
    )

    env = os.environ | {
        "HOME": str(root),
        "CLAUDE_CONFIG_DIR": str(root / "claude"),
        "CODEX_HOME": str(root / "codex"),
        "CURSOR_HOME": str(root / "cursor"),
        "SESSIONS_DB": str(root / "sessions.duckdb"),
    }
    _ = subprocess.run(
        [sys.executable, "-B", str(BUNDLE), "ingest"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    yield root / "sessions.duckdb"


def _run(database: Path, sql: str) -> list[Row]:
    result = subprocess.run(
        ["duckdb", "-readonly", str(database), "-json", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    if not result.stdout.strip():
        return []
    return cast(list[Row], json.loads(result.stdout))


def _pack(database: Path, pack: str, kind: str, target: str) -> list[list[Row]]:
    return [_run(database, sql) for sql in _pack_sql(pack, kind, target)]


@needs_duckdb
def test_agent_target_reports_usage_trend_and_decay(database: Path) -> None:
    coverage, total, weekly, projects, peers = _pack(database, "skill-usage.md", "agent", "reviewer")
    assert {row["harness"] for row in coverage} == {"claude", "cursor"}
    assert total[0]["total_invocations"] == 1
    assert sum(cast(int, row["invocations"]) for row in weekly) == 1
    assert len(projects) == 1
    assert (peers[0]["target_rank"], peers[0]["population"]) == (1, 3)
    _, decay, trend, signatures = _pack(database, "drift-regression.md", "agent", "reviewer")
    assert int(cast(int, decay[0]["recent_4w"])) == 1
    assert sum(cast(int, row["calls"]) for row in trend) == 2
    errors = sum(round(cast(float, row["error_pct"]) * cast(int, row["calls"]) / 100) for row in trend)
    assert errors == 1
    assert [(row["error"], row["occurrences"]) for row in signatures] == [("boom", 1)]


@needs_duckdb
def test_agent_window_excludes_its_own_anchor_and_later_events(database: Path) -> None:
    _, tools, spawns, mcp, windows = _pack(database, "agent-orchestration.md", "agent", "reviewer")
    assert {row["tool_name"]: row["uses"] for row in tools} == {"Read": 1, "mcp__srv__tool": 1}
    assert spawns == []
    assert [(row["harness"], row["tool_name"], row["calls"]) for row in mcp] == [
        ("claude", "mcp__srv__tool", 1)
    ]
    assert [(row["harness"], row["correlated_spawns"]) for row in windows] == [("claude", 0)]


@needs_duckdb
def test_cursor_events_sharing_the_anchor_timestamp_stay_in_the_window(database: Path) -> None:
    _, tools, spawns, _, windows = _pack(database, "agent-orchestration.md", "agent", "cursor-reviewer")
    assert {row["tool_name"]: row["uses"] for row in tools} == {"Read": 1, "Task": 1}
    assert [row["agent_type"] for row in spawns] == ["helper"]
    assert [(row["harness"], row["correlated_spawns"]) for row in windows] == [("cursor", 1)]


@needs_duckdb
def test_skill_target_counts_events_after_the_anchor(database: Path) -> None:
    _, tools, spawns, mcp, windows = _pack(database, "agent-orchestration.md", "skill", "skillz")
    assert {row["tool_name"]: row["uses"] for row in tools} == {
        "Agent": 1,
        "Read": 1,
        "mcp__srv__tool": 1,
    }
    assert [row["agent_type"] for row in spawns] == ["reviewer"]
    assert [row["tool_name"] for row in mcp] == ["mcp__srv__tool"]
    assert windows[0]["correlated_spawns"] == 1


@needs_duckdb
def test_skill_target_still_starts_from_skill_invocations(database: Path) -> None:
    _, total, _, _, peers = _pack(database, "skill-usage.md", "skill", "skillz")
    assert total[0]["total_invocations"] == 1
    assert peers[0]["target_rank"] == 1
    assert _pack(database, "skill-usage.md", "agent", "skillz")[1][0]["total_invocations"] == 0
    assert _pack(database, "skill-usage.md", "skill", "reviewer")[1][0]["total_invocations"] == 0


@needs_duckdb
def test_harness_without_start_events_is_absent_from_coverage(database: Path) -> None:
    sessions = {row["harness"] for row in _run(database, "SELECT DISTINCT harness FROM sessions")}
    assert "cursor" in sessions
    expected = {"skill": {"claude"}, "agent": {"claude", "cursor"}}
    for kind in START:
        coverage = _run(database, _pack_sql("skill-usage.md", kind, "x")[0])
        assert {row["harness"] for row in coverage} == expected[kind], kind


def test_bundle_db_path_uses_the_xdg_cache_default_and_schema_ships_the_reference(
    tmp_path: Path,
) -> None:
    env = {k: v for k, v in os.environ.items() if k != "SESSIONS_DB"}
    env |= {"HOME": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path / "cache")}

    def run(command: str) -> dict[str, str]:
        result = subprocess.run(
            [sys.executable, "-B", str(BUNDLE), command],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return cast(dict[str, str], json.loads(result.stdout))

    expected = tmp_path / "cache" / "agent-session-analytics" / "sessions.duckdb"
    assert run("db-path") == {"path": str(expected)}
    schema = run("schema")
    assert schema["reference"] == "canonical-schema.md"
    assert "schema_info" in schema["content"]

"""Verify the bundled analytics engine with isolated session logs."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, TypeAlias, cast

if TYPE_CHECKING:
    from typing_extensions import override
else:
    # The engine ships with the skill and runs on bare Python 3.11, which lacks typing.override.
    def override(method):
        return method

_RowValue: TypeAlias = str | int | float | bool | None
_Row: TypeAlias = dict[str, _RowValue]

ENGINE = Path(__file__).resolve().parents[1]
REQUIRE_DUCKDB = os.environ.get("REQUIRE_DUCKDB") == "1"


def _bash_db_path(env: dict[str, str], cwd: Path) -> str:
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; sessions_db_path', "_",
         str(ENGINE / "scripts" / "db-path.sh")],
        env=env, cwd=cwd, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _python_db_path(env: dict[str, str], cwd: Path, engine: Path = ENGINE) -> str:
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import ingest; print(ingest.DB_PATH)",
         str(engine / "scripts")],
        env=env, cwd=cwd, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


class DbPathParityTest(unittest.TestCase):
    """Bash and Python must resolve the same database path — no DuckDB needed."""

    CASES: ClassVar[dict[str, Callable[[Path], dict[str, str]]]] = {
        "sessions_db_absolute": lambda root: {"SESSIONS_DB": str(root / "abs.duckdb")},
        "sessions_db_relative": lambda root: {"SESSIONS_DB": "rel.duckdb"},
        "sessions_db_tilde": lambda root: {"SESSIONS_DB": "~/x.duckdb"},
        "xdg_cache_absolute": lambda root: {"XDG_CACHE_HOME": str(root / "cache")},
        "xdg_cache_relative": lambda root: {"XDG_CACHE_HOME": "rel/c"},
        "xdg_cache_tilde": lambda root: {"XDG_CACHE_HOME": "~/c"},
        "unset": lambda root: {},
    }

    def test_python_path_does_not_create_package_bytecode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = shutil.copytree(ENGINE, root / "engine",
                                     ignore=shutil.ignore_patterns("__pycache__"))
            env = os.environ.copy()
            _ = env.pop("PYTHONDONTWRITEBYTECODE", None)
            _ = env.pop("PYTHONPYCACHEPREFIX", None)
            env["SESSIONS_DB"] = str(root / "sessions.duckdb")
            self.assertEqual(_python_db_path(env, root, engine), env["SESSIONS_DB"])
            self.assertEqual(list(engine.rglob("__pycache__")), [])

    def test_paths_match_for_every_case(self):
        for name, make_overrides in self.CASES.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                home = root / "home"
                cwd = root / "cwd"
                home.mkdir()
                cwd.mkdir()
                env = os.environ | {"HOME": str(home)}
                _ = env.pop("SESSIONS_DB", None)
                _ = env.pop("XDG_CACHE_HOME", None)
                env |= make_overrides(root)
                bash_path = _bash_db_path(env, cwd)
                python_path = _python_db_path(env, cwd)
                self.assertEqual(bash_path, python_path)


def _normalize(adapter: str, path: Path, env: dict[str, str]) -> list[object]:
    """Run one ingest adapter in a child process and return its canonical rows."""
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import json, sys; sys.path.insert(0, sys.argv[1]); import ingest; "
         + "print(json.dumps(list(getattr(ingest, sys.argv[2])(sys.argv[3]))))",
         str(ENGINE / "scripts"), adapter, str(path)],
        env=env, capture_output=True, text=True, check=True,
    )
    return cast(list[object], json.loads(result.stdout))


class MalformedRowTest(unittest.TestCase):
    """A malformed log field is dropped instead of stopping the ingest. No DuckDB needed."""

    def test_pi_rows_keep_their_valid_parts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            entries = [
                {"type": "session", "id": "s1", "cwd": "/work"},
                {"type": "message", "message": {"role": "assistant", "content": 5, "stopReason": ["x"]}},
                {"type": "message", "message": {"role": "toolResult", "toolCallId": "c1", "content": [
                    {"type": "text", "text": 7}, {"type": "text", "text": "ok"}]}},
            ]
            _ = path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
            rows = _normalize("pi_normalize", path, dict(os.environ))
        envelope = {"harness": "pi", "timestamp": None, "sessionId": "s1", "cwd": "/work"}
        usage = {"input_tokens": None, "output_tokens": None, "cache_read_input_tokens": None}
        self.assertEqual(rows, [
            envelope | {"type": "assistant", "message": {"content": [], "usage": usage}},
            envelope | {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "c1", "content": "ok", "is_error": "false"}]}},
        ])

    def test_cursor_user_text_that_is_not_a_string_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cursor"
            transcripts = root / "projects" / "sample" / "agent-transcripts"
            transcripts.mkdir(parents=True)
            path = transcripts / "t1.jsonl"
            stamp = "<timestamp>Monday, Jan 5, 2026, 10:00 AM (UTC+0)</timestamp> go"
            entries = [
                {"role": "user", "message": {"content": [
                    {"type": "text", "text": 9}, {"type": "text", "text": stamp}]}},
                {"type": "turn_ended", "status": "completed"},
            ]
            _ = path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
            rows = _normalize("cursor_normalize", path, os.environ | {"CURSOR_HOME": str(root)})
        self.assertEqual(len(rows), 1)
        row = rows[0]
        assert isinstance(row, dict)
        self.assertEqual(cast(dict[str, object], row)["timestamp"], "2026-01-05T10:00:00Z")

    def test_codex_error_flag_survives_a_non_text_content_block(self) -> None:
        header = "Script completed\nWall time: 0.1 seconds\nOutput:\n"
        output = [
            {"type": "input_text", "text": header},
            {"type": "input_text", "text": json.dumps({"status": "error"})},
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
        ]
        entry = {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "c1", "output": output}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout.jsonl"
            _ = path.write_text(json.dumps(entry) + "\n")
            rows = _normalize("codex_normalize", path, dict(os.environ))
        self.assertEqual(len(rows), 1)
        row = cast(dict[str, dict[str, list[dict[str, object]]]], rows[0])
        self.assertEqual(row["message"]["content"][0]["is_error"], "true")


class EngineSmokeTest(unittest.TestCase):
    @unittest.skipUnless(REQUIRE_DUCKDB or shutil.which("duckdb"), "DuckDB CLI unavailable")
    def test_ingest_query_and_cache(self):
        if REQUIRE_DUCKDB:
            self.assertTrue(shutil.which("duckdb"), "REQUIRE_DUCKDB=1 but no duckdb CLI on PATH")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / "claude" / "projects" / "sample"
            logs.mkdir(parents=True)
            entry = {
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:00Z",
                "sessionId": "fixture-session",
                "cwd": str(root / "project"),
                "message": {
                    "content": [{
                        "type": "tool_use",
                        "name": "Skill",
                        "id": "fixture-call",
                        "input": {"skill": "skillz", "args": "audit"},
                    }],
                },
            }
            _ = (logs / "session.jsonl").write_text(json.dumps(entry) + "\n")
            env = os.environ | {
                "HOME": str(root),
                "CLAUDE_CONFIG_DIR": str(root / "claude"),
                "CODEX_HOME": str(root / "codex"),
                "CURSOR_HOME": str(root / "cursor"),
                "XDG_CACHE_HOME": str(root / "cache"),
            }
            _ = env.pop("SESSIONS_DB", None)
            database = root / "cache" / "dotfiles" / "session-analytics" / "sessions.duckdb"
            path = subprocess.run(
                ["bash", "-c", 'source "$1"; sessions_db_path', "_",
                 str(ENGINE / "scripts" / "db-path.sh")],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(path.stdout.strip(), str(database))
            ingest = subprocess.run(
                [sys.executable, str(ENGINE / "scripts" / "ingest.py")],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertTrue(database.is_file(), ingest.stdout + ingest.stderr)
            self.assertFalse(
                (root / "cache" / "dotfiles" / "session-analytics" / "sessions.duckdb.stage").exists()
            )
            query = subprocess.run(
                ["bash", str(ENGINE / "scripts" / "query.sh"), "sql",
                 "SELECT count(*) AS n FROM skill_invocations WHERE skill_name = 'skillz'"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertRegex(query.stdout, r"\|\s*1\s*\|")
            cached = subprocess.run(
                [sys.executable, str(ENGINE / "scripts" / "ingest.py")],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertIn("Skipping ingestion", cached.stdout)


    @unittest.skipUnless(REQUIRE_DUCKDB or shutil.which("duckdb"), "DuckDB CLI unavailable")
    def test_query_fails_when_automatic_ingest_fails(self):
        for has_database in (False, True):
            with self.subTest(has_database=has_database), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                database = root / "cache" / "dotfiles" / "session-analytics" / "sessions.duckdb"
                if has_database:
                    database.parent.mkdir(parents=True)
                    database.touch()
                    os.utime(database, (0, 0))
                env = os.environ | {
                    "HOME": str(root),
                    "CLAUDE_CONFIG_DIR": str(root / "claude"),
                    "CODEX_HOME": str(root / "codex"),
                    "CURSOR_HOME": str(root / "cursor"),
                    "XDG_CACHE_HOME": str(root / "cache"),
                }
                _ = env.pop("SESSIONS_DB", None)
                query = subprocess.run(
                    ["bash", str(ENGINE / "scripts" / "query.sh"), "sql", "SELECT 1"],
                    env=env, capture_output=True, text=True,
                )
                self.assertEqual(query.returncode, 1)
                self.assertEqual(query.stdout, "")
                self.assertIn("Automatic ingestion failed (exit 1)", query.stderr)
                if has_database:
                    self.assertIn("stale data", query.stderr)
                else:
                    self.assertIn("no session database is available", query.stderr)
                    self.assertNotIn("stale data", query.stderr)


PACKS = ENGINE.parent / "references"
CONVENTIONS = ENGINE / "references" / "query-conventions.md"
PACK_NAMES = ("skill-usage.md", "agent-orchestration.md", "drift-regression.md")
SQL_BLOCK = re.compile(r"```sql\n(.*?)```", re.S)


def _start_tables() -> dict[str, tuple[str, str]]:
    """Read the kind-to-table mapping from its single source, the conventions file."""
    matches = re.finditer(r"^- `(\w+)`: `(\w+)`, `(\w+)`$", CONVENTIONS.read_text(), re.M)
    return {match.group(1): (match.group(2), match.group(3)) for match in matches}


START = _start_tables()


def _pack_sql(pack: str, kind: str, target: str) -> list[str]:
    table, column = START[kind]
    return [
        match.group(1).replace("{START_TABLE}", table)
        .replace("{NAME_COLUMN}", column)
        .replace("{TARGET}", target)
        for match in SQL_BLOCK.finditer((PACKS / pack).read_text())
    ]


class PackStaticTest(unittest.TestCase):
    """Pack text rules that need no DuckDB."""

    def test_mapping_has_one_source_with_both_kinds(self):
        self.assertEqual(set(START), {"skill", "agent"})
        for pack in PACK_NAMES:
            with self.subTest(pack=pack):
                text = (PACKS / pack).read_text()
                self.assertIn("engine/references/query-conventions.md", text)
                for table, column in START.values():
                    self.assertNotIn(f"`{table}`, `{column}`", text)

    def test_every_pack_query_starts_from_the_start_table(self):
        for pack in PACK_NAMES:
            blocks = [match.group(1) for match in SQL_BLOCK.finditer((PACKS / pack).read_text())]
            self.assertTrue(blocks, pack)
            for block in blocks:
                with self.subTest(pack=pack, sql=block[:60]):
                    self.assertIn("{START_TABLE}", block)
                    self.assertNotIn("skill_invocations", block)

    def test_packs_state_the_unavailable_rule(self):
        for pack in PACK_NAMES:
            with self.subTest(pack=pack):
                self.assertIn("as `unavailable`, never as 0", (PACKS / pack).read_text())
        self.assertIn("`unavailable`, not `dormant`",
                      (PACKS / "drift-regression.md").read_text())

    def test_ceremony_prompt_carries_target_kind(self):
        self.assertIn("target_kind=", (PACKS / "analytics-ceremony.md").read_text())


@unittest.skipUnless(REQUIRE_DUCKDB or shutil.which("duckdb"), "DuckDB CLI unavailable")
class PackTargetKindTest(unittest.TestCase):
    """Each pack starts from the table that matches the target kind."""

    directory: ClassVar[tempfile.TemporaryDirectory[str]]
    database: ClassVar[Path]

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        logs = root / "claude" / "projects" / "sample"
        logs.mkdir(parents=True)
        base = datetime.now(timezone.utc) - timedelta(days=3)

        def stamp(minutes: int) -> str:
            return (base + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

        def use(minutes: int, call_id: str, name: str, tool_input: dict[str, str]) -> dict[str, object]:
            return {
                "type": "assistant", "timestamp": stamp(minutes),
                "sessionId": "fixture-session", "cwd": str(root / "project"),
                "message": {"content": [{
                    "type": "tool_use", "name": name, "id": call_id, "input": tool_input,
                }]},
            }

        def result(minutes: int, call_id: str, content: str, is_error: bool = False) -> dict[str, object]:
            block: dict[str, _RowValue] = {"type": "tool_result", "tool_use_id": call_id, "content": content}
            if is_error:
                block["is_error"] = True
            return {
                "type": "user", "timestamp": stamp(minutes),
                "sessionId": "fixture-session", "cwd": str(root / "project"),
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

        user = turn("user", [{"type": "text", "text":
                              "<timestamp>Monday, Jan 5, 2026, 10:00 AM (UTC+0)</timestamp> go"}])
        shared = [
            user,
            call("Task", {"subagent_type": "cursor-reviewer", "description": "review"}),
            call("Read", {"file_path": "a"}),
            call("Task", {"subagent_type": "helper", "description": "help"}),
        ]
        _ = (transcripts / "cursor-shared.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in shared))
        _ = (transcripts / "cursor-readonly.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in (user, call("Read", {"file_path": "c"}))))

        env = os.environ | {
            "HOME": str(root),
            "CLAUDE_CONFIG_DIR": str(root / "claude"),
            "CODEX_HOME": str(root / "codex"),
            "CURSOR_HOME": str(root / "cursor"),
            "SESSIONS_DB": str(root / "sessions.duckdb"),
        }
        _ = subprocess.run([sys.executable, "-B", str(ENGINE / "scripts" / "ingest.py")],
                       env=env, capture_output=True, text=True, check=True)
        cls.database = root / "sessions.duckdb"

    @classmethod
    @override
    def tearDownClass(cls) -> None:
        cls.directory.cleanup()

    def _run(self, sql: str) -> list[_Row]:
        result = subprocess.run(
            ["duckdb", "-readonly", str(self.database), "-json", "-c", sql],
            capture_output=True, text=True, check=True,
        )
        if not result.stdout.strip():
            return []
        rows = cast(object, json.loads(result.stdout))
        assert isinstance(rows, list)
        checked: list[_Row] = []
        for raw_row in cast(list[object], rows):
            assert isinstance(raw_row, dict)
            row = cast(dict[object, object], raw_row)
            assert all(
                isinstance(key, str)
                and isinstance(value, (str, int, float, bool, type(None)))
                for key, value in row.items()
            )
            checked.append(cast(_Row, row))
        return checked

    def _pack(self, pack: str, kind: str, target: str) -> list[list[_Row]]:
        return [self._run(sql) for sql in _pack_sql(pack, kind, target)]

    def test_agent_target_reports_usage_trend_and_decay(self):
        coverage, total, weekly, projects, peers = self._pack("skill-usage.md", "agent", "reviewer")
        self.assertEqual({row["harness"] for row in coverage}, {"claude", "cursor"})
        self.assertEqual(total[0]["total_invocations"], 1)
        assert all(isinstance(row["invocations"], int) for row in weekly)
        self.assertEqual(sum(cast(int, row["invocations"]) for row in weekly), 1)
        self.assertEqual(len(projects), 1)
        self.assertEqual((peers[0]["target_rank"], peers[0]["population"]), (1, 3))
        _, decay, trend, signatures = self._pack("drift-regression.md", "agent", "reviewer")
        recent = decay[0]["recent_4w"]
        assert isinstance(recent, (str, int, float))
        self.assertEqual(int(recent), 1)
        assert all(isinstance(row["calls"], int) and isinstance(row["error_pct"], (int, float)) for row in trend)
        self.assertEqual(sum(cast(int, row["calls"]) for row in trend), 2)
        self.assertEqual(sum(round(cast(float, row["error_pct"]) * cast(int, row["calls"]) / 100) for row in trend), 1)
        self.assertEqual([(row["error"], row["occurrences"]) for row in signatures],
                         [("boom", 1)])

    def test_agent_window_excludes_its_own_anchor_and_later_events(self):
        _, tools, spawns, mcp, windows = self._pack("agent-orchestration.md", "agent", "reviewer")
        self.assertEqual({row["tool_name"]: row["uses"] for row in tools},
                         {"Read": 1, "mcp__srv__tool": 1})
        self.assertEqual(spawns, [])
        self.assertEqual([(row["harness"], row["tool_name"], row["calls"]) for row in mcp],
                         [("claude", "mcp__srv__tool", 1)])
        self.assertEqual([(row["harness"], row["correlated_spawns"]) for row in windows],
                         [("claude", 0)])

    def test_cursor_events_sharing_the_anchor_timestamp_stay_in_the_window(self):
        _, tools, spawns, _, windows = self._pack(
            "agent-orchestration.md", "agent", "cursor-reviewer")
        self.assertEqual({row["tool_name"]: row["uses"] for row in tools},
                         {"Read": 1, "Task": 1})
        self.assertEqual([row["agent_type"] for row in spawns], ["helper"])
        self.assertEqual([(row["harness"], row["correlated_spawns"]) for row in windows],
                         [("cursor", 1)])

    def test_skill_target_counts_events_after_the_anchor(self):
        _, tools, spawns, mcp, windows = self._pack("agent-orchestration.md", "skill", "skillz")
        self.assertEqual({row["tool_name"]: row["uses"] for row in tools},
                         {"Agent": 1, "Read": 1, "mcp__srv__tool": 1})
        self.assertEqual([row["agent_type"] for row in spawns], ["reviewer"])
        self.assertEqual([row["tool_name"] for row in mcp], ["mcp__srv__tool"])
        self.assertEqual(windows[0]["correlated_spawns"], 1)

    def test_skill_target_still_starts_from_skill_invocations(self):
        _, total, _, _, peers = self._pack("skill-usage.md", "skill", "skillz")
        self.assertEqual(total[0]["total_invocations"], 1)
        self.assertEqual(peers[0]["target_rank"], 1)
        self.assertEqual(self._pack("skill-usage.md", "agent", "skillz")[1][0]["total_invocations"], 0)
        self.assertEqual(self._pack("skill-usage.md", "skill", "reviewer")[1][0]["total_invocations"], 0)

    def test_harness_without_start_events_is_absent_from_coverage(self):
        sessions = {row["harness"] for row in self._run("SELECT DISTINCT harness FROM sessions")}
        self.assertIn("cursor", sessions)
        expected = {"skill": {"claude"}, "agent": {"claude", "cursor"}}
        for kind in START:
            with self.subTest(kind=kind):
                coverage = self._run(_pack_sql("skill-usage.md", kind, "x")[0])
                self.assertEqual({row["harness"] for row in coverage}, expected[kind])


if __name__ == "__main__":
    _ = unittest.main()

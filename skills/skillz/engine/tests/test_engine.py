"""Verify the bundled analytics engine with isolated session logs."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
REQUIRE_DUCKDB = os.environ.get("REQUIRE_DUCKDB") == "1"


def _bash_db_path(env, cwd):
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; sessions_db_path', "_",
         str(ENGINE / "scripts" / "db-path.sh")],
        env=env, cwd=cwd, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _python_db_path(env, cwd, engine=ENGINE):
    result = subprocess.run(
        ["python3", "-B", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import ingest; "
         "print(ingest.DB_PATH)", str(engine / "scripts")],
        env=env, cwd=cwd, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


class DbPathParityTest(unittest.TestCase):
    """Bash and Python must resolve the same database path — no DuckDB needed."""

    CASES = {
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
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            env.pop("PYTHONPYCACHEPREFIX", None)
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
                env.pop("SESSIONS_DB", None)
                env.pop("XDG_CACHE_HOME", None)
                env |= make_overrides(root)
                bash_path = _bash_db_path(env, cwd)
                python_path = _python_db_path(env, cwd)
                self.assertEqual(bash_path, python_path)


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
            (logs / "session.jsonl").write_text(json.dumps(entry) + "\n")
            env = os.environ | {
                "HOME": str(root),
                "CLAUDE_CONFIG_DIR": str(root / "claude"),
                "CODEX_HOME": str(root / "codex"),
                "CURSOR_HOME": str(root / "cursor"),
                "XDG_CACHE_HOME": str(root / "cache"),
            }
            env.pop("SESSIONS_DB", None)
            database = root / "cache" / "dotfiles" / "session-analytics" / "sessions.duckdb"
            path = subprocess.run(
                ["bash", "-c", 'source "$1"; sessions_db_path', "_",
                 str(ENGINE / "scripts" / "db-path.sh")],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(path.stdout.strip(), str(database))
            ingest = subprocess.run(
                ["python3", str(ENGINE / "scripts" / "ingest.py")],
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
                ["python3", str(ENGINE / "scripts" / "ingest.py")],
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
                env.pop("SESSIONS_DB", None)
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


def _start_tables():
    """Read the kind-to-table mapping from its single source, the conventions file."""
    rows = re.findall(r"^- `(\w+)`: `(\w+)`, `(\w+)`$", CONVENTIONS.read_text(), re.M)
    return {kind: (table, column) for kind, table, column in rows}


START = _start_tables()


def _pack_sql(pack, kind, target):
    table, column = START[kind]
    return [
        block.replace("{START_TABLE}", table)
        .replace("{NAME_COLUMN}", column)
        .replace("{TARGET}", target)
        for block in SQL_BLOCK.findall((PACKS / pack).read_text())
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
            blocks = SQL_BLOCK.findall((PACKS / pack).read_text())
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

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        logs = root / "claude" / "projects" / "sample"
        logs.mkdir(parents=True)
        base = datetime.now(timezone.utc) - timedelta(days=3)

        def stamp(minutes):
            return (base + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

        def use(minutes, call_id, name, tool_input):
            return {
                "type": "assistant", "timestamp": stamp(minutes),
                "sessionId": "fixture-session", "cwd": str(root / "project"),
                "message": {"content": [{
                    "type": "tool_use", "name": name, "id": call_id, "input": tool_input,
                }]},
            }

        def result(minutes, call_id, content, is_error=False):
            block = {"type": "tool_result", "tool_use_id": call_id, "content": content}
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
        (logs / "session.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))

        # Cursor logs carry minute-resolution timestamps: one user turn stamps
        # the spawn, the Read, and the second spawn with the same time.
        transcripts = root / "cursor" / "projects" / "sample" / "agent-transcripts"
        transcripts.mkdir(parents=True)

        def turn(role, content):
            return {"role": role, "message": {"content": content}}

        def call(name, tool_input):
            return turn("assistant", [{"type": "tool_use", "name": name, "input": tool_input}])

        user = turn("user", [{"type": "text", "text":
                              "<timestamp>Monday, Jan 5, 2026, 10:00 AM (UTC+0)</timestamp> go"}])
        shared = [
            user,
            call("Task", {"subagent_type": "cursor-reviewer", "description": "review"}),
            call("Read", {"file_path": "a"}),
            call("Task", {"subagent_type": "helper", "description": "help"}),
        ]
        (transcripts / "cursor-shared.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in shared))
        (transcripts / "cursor-readonly.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in (user, call("Read", {"file_path": "c"}))))

        env = os.environ | {
            "HOME": str(root),
            "CLAUDE_CONFIG_DIR": str(root / "claude"),
            "CODEX_HOME": str(root / "codex"),
            "CURSOR_HOME": str(root / "cursor"),
            "SESSIONS_DB": str(root / "sessions.duckdb"),
        }
        subprocess.run(["python3", "-B", str(ENGINE / "scripts" / "ingest.py")],
                       env=env, capture_output=True, text=True, check=True)
        cls.database = root / "sessions.duckdb"

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def _run(self, sql):
        result = subprocess.run(
            ["duckdb", "-readonly", str(self.database), "-json", "-c", sql],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else []

    def _pack(self, pack, kind, target):
        return [self._run(sql) for sql in _pack_sql(pack, kind, target)]

    def test_agent_target_reports_usage_trend_and_decay(self):
        coverage, total, weekly, projects, peers = self._pack("skill-usage.md", "agent", "reviewer")
        self.assertEqual({row["harness"] for row in coverage}, {"claude", "cursor"})
        self.assertEqual(total[0]["total_invocations"], 1)
        self.assertEqual(sum(row["invocations"] for row in weekly), 1)
        self.assertEqual(len(projects), 1)
        self.assertEqual((peers[0]["target_rank"], peers[0]["population"]), (1, 3))
        _, decay, trend, signatures = self._pack("drift-regression.md", "agent", "reviewer")
        self.assertEqual(int(decay[0]["recent_4w"]), 1)
        self.assertEqual(sum(row["calls"] for row in trend), 2)
        self.assertEqual(sum(round(row["error_pct"] * row["calls"] / 100) for row in trend), 1)
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
    unittest.main()

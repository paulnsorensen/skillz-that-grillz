"""Verify the bundled analytics engine with isolated session logs."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
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


def _python_db_path(env, cwd):
    result = subprocess.run(
        ["python3", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import ingest; "
         "print(ingest.DB_PATH)", str(ENGINE / "scripts")],
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


if __name__ == "__main__":
    unittest.main()

"""Verify the bundled analytics engine with isolated session logs."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]


class EngineSmokeTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("duckdb"), "DuckDB CLI unavailable")
    def test_ingest_query_and_cache(self):
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

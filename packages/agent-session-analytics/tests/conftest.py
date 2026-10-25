"""Shared fixtures: an isolated HOME and a runner for the installed CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

REQUIRE_DUCKDB = os.environ.get("REQUIRE_DUCKDB") == "1"
needs_duckdb = pytest.mark.skipif(
    not (REQUIRE_DUCKDB or shutil.which("duckdb")), reason="DuckDB CLI unavailable"
)

@dataclass
class Sandbox:
    """One temporary HOME with log roots, a cache dir, and no inherited overrides."""

    root: Path
    env: dict[str, str]

    @property
    def database(self) -> Path:
        return self.root / "cache" / "agent-session-analytics" / "sessions.duckdb"

    def run(
        self, *args: str, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = self.env | (extra_env or {})
        return subprocess.run(
            [sys.executable, "-m", "agent_session_analytics", *args],
            env=env,
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

    def write_log(self, relative: str, entries: list[dict[str, object]]) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
        return path

    def query(self, sql: str, database: Path | None = None) -> list[dict[str, object]]:
        result = subprocess.run(
            ["duckdb", "-readonly", str(database or self.database), "-json", "-c", sql],
            capture_output=True,
            text=True,
            check=True,
        )
        if not result.stdout.strip():
            return []
        return cast(list[dict[str, object]], json.loads(result.stdout))


def sandbox_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in ("SESSIONS_DB", "SESSIONS_DUCKDB_MEMORY_LIMIT", "SESSIONS_DUCKDB_THREADS"):
        _ = env.pop(name, None)
    env.update(
        HOME=str(root),
        CLAUDE_CONFIG_DIR=str(root / "claude"),
        CODEX_HOME=str(root / "codex"),
        CURSOR_HOME=str(root / "cursor"),
        XDG_CACHE_HOME=str(root / "cache"),
    )
    return env


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    root = tmp_path.resolve()
    return Sandbox(root=root, env=sandbox_env(root))


def claude_skill_entry(root: Path) -> dict[str, object]:
    """One Claude assistant entry that invokes the `skillz` skill."""
    return {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:00Z",
        "sessionId": "fixture-session",
        "cwd": str(root / "project"),
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Skill",
                    "id": "fixture-call",
                    "input": {"skill": "skillz", "args": "audit"},
                }
            ],
        },
    }

"""Path resolution for the database and the adapter log roots. No DuckDB needed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from .conftest import Sandbox


def eval_ingest(expr: str, env: dict[str, str], cwd: Path, *argv: str) -> str:
    """Import the module in a fresh interpreter and print one expression.

    The paths are module constants read at import, so each case needs its own process.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import json, sys; from session_analytics import _ingest; " + f"print({expr})",
            *argv,
        ],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


DEFAULT = "home/.cache/dotfiles/session-analytics/sessions.duckdb"

DB_PATH_CASES: dict[str, tuple[Callable[[Path], dict[str, str]], Callable[[Path], str]]] = {
    "sessions_db_absolute": (
        lambda root: {"SESSIONS_DB": str(root / "abs.duckdb")},
        lambda root: str(root / "abs.duckdb"),
    ),
    "sessions_db_relative": (
        lambda root: {"SESSIONS_DB": "rel.duckdb"},
        lambda root: str(root / "cwd" / "rel.duckdb"),
    ),
    "sessions_db_tilde_stays_literal": (
        lambda root: {"SESSIONS_DB": "~/x.duckdb"},
        lambda root: str(root / "cwd" / "~" / "x.duckdb"),
    ),
    "xdg_cache_absolute": (
        lambda root: {"XDG_CACHE_HOME": str(root / "cache")},
        lambda root: str(root / "cache" / "dotfiles" / "session-analytics" / "sessions.duckdb"),
    ),
    "xdg_cache_relative_is_ignored": (
        lambda root: {"XDG_CACHE_HOME": "rel/c"},
        lambda root: str(root / DEFAULT),
    ),
    "xdg_cache_tilde_is_ignored": (
        lambda root: {"XDG_CACHE_HOME": "~/c"},
        lambda root: str(root / DEFAULT),
    ),
    "unset": (lambda root: {}, lambda root: str(root / DEFAULT)),
}


@pytest.mark.parametrize("name", list(DB_PATH_CASES))
def test_database_path_resolution(name: str, sandbox: Sandbox) -> None:
    make_overrides, expected = DB_PATH_CASES[name]
    home = sandbox.root / "home"
    cwd = sandbox.root / "cwd"
    home.mkdir()
    cwd.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("SESSIONS_DB", "XDG_CACHE_HOME")}
    env |= {"HOME": str(home)} | make_overrides(sandbox.root)
    assert eval_ingest("_ingest.DB_PATH", env, cwd) == expected(sandbox.root)


def test_db_path_command_prints_the_default(sandbox: Sandbox) -> None:
    result = sandbox.run("db-path")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"path": str(sandbox.database)}


def test_db_path_command_honours_sessions_db(sandbox: Sandbox) -> None:
    override = sandbox.root / "elsewhere.duckdb"
    result = sandbox.run("db-path", extra_env={"SESSIONS_DB": str(override)})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"path": str(override)}


NAMES = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "CURSOR_HOME")


def _adapter_sandbox(root: Path) -> tuple[Path, Path, dict[str, str]]:
    """Return a fresh HOME, a separate cwd, and an env without adapter overrides."""
    # Resolve symlinks (macOS /var -> /private/var): os.getcwd() in the child
    # returns the real path, so a relative value resolves there.
    root = root.resolve()
    home, cwd = root / "home", root / "cwd"
    cwd.mkdir()
    env = dict(os.environ) | {"HOME": str(home)}
    for name in NAMES:
        _ = env.pop(name, None)
    return home, cwd, env


def _layout(base: Path) -> list[list[str]]:
    logs = [
        base / ".claude" / "projects" / "p" / "s.jsonl",
        base / ".codex" / "sessions" / "s.jsonl",
        base / ".cursor" / "projects" / "p" / "agent-transcripts" / "s.jsonl",
    ]
    for log in logs:
        log.parent.mkdir(parents=True)
        _ = log.write_text("{}\n")
    return [[str(log)] for log in logs]


def _discovered(env: dict[str, str], cwd: Path) -> list[list[str]]:
    return cast(
        list[list[str]],
        json.loads(
            eval_ingest(
                "json.dumps([_ingest.claude_discover(), _ingest.codex_discover(), "
                + "_ingest.cursor_discover()])",
                env,
                cwd,
            )
        ),
    )


def test_unset_adapter_defaults_expand_home_outside_home(tmp_path: Path) -> None:
    home, cwd, env = _adapter_sandbox(tmp_path)
    expected = _layout(home)
    assert _discovered(env, cwd) == expected


def test_configured_tilde_value_stays_literal(tmp_path: Path) -> None:
    home, cwd, env = _adapter_sandbox(tmp_path)
    _ = _layout(home)
    literal = _layout(cwd / "~")
    env |= {
        "CLAUDE_CONFIG_DIR": "~/.claude",
        "CODEX_HOME": "~/.codex",
        "CURSOR_HOME": "~/.cursor",
    }
    assert _discovered(env, cwd) == literal


def test_empty_adapter_value_uses_the_home_default(tmp_path: Path) -> None:
    home, cwd, env = _adapter_sandbox(tmp_path)
    expected = _layout(home)
    env |= {name: "" for name in NAMES}
    assert _discovered(env, cwd) == expected


def test_cursor_project_slug_resolves_under_home_default(tmp_path: Path) -> None:
    home, cwd, env = _adapter_sandbox(tmp_path)
    log = home / ".cursor" / "projects" / "tmp" / "agent-transcripts" / "s.jsonl"
    decoded = eval_ingest("_ingest._cursor_project_cwd(sys.argv[1])", env, cwd, str(log))
    assert decoded == "/tmp"

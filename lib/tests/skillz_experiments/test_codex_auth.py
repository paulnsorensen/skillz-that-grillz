"""The Codex transport reuses the host login through a link and forwards no token variable (AC-6)."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from skillz_experiments._codex import Codex
from skillz_experiments._runtime import Budget, process

TOKENS = ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


def test_codex_links_auth_json_and_sets_only_codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")
    host = tmp_path / "host-codex"
    host.mkdir()
    _ = (host / "auth.json").write_text('{"login": "host"}')
    fake = tmp_path / "bin/codex"
    fake.parent.mkdir()
    _ = fake.write_text('#!/bin/sh\n/usr/bin/env\necho "auth=$(cat "$CODEX_HOME/auth.json")"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(host))
    for name in TOKENS:
        monkeypatch.setenv(name, "token-value")
    adapter = Codex("m", Budget(10, 120, 0), lambda: None)
    link = adapter.codex_home / "auth.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        assert link.is_symlink() and link.resolve() == (host / "auth.json").resolve()
        assert [path.name for path in adapter.codex_home.iterdir()] == ["auth.json"]
        result = process([str(adapter.executable)], cwd=workspace, timeout=10,
                         environment=adapter._environment(workspace))  # pyright: ignore[reportPrivateUsage]
    finally:
        adapter.close()
    lines = result.stdout.splitlines()
    child = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("auth="))
    assert set(child) - {"PWD", "SHLVL", "_", "LC_CTYPE"} == {"PATH", "HOME", "TMPDIR", "LANG", "CODEX_HOME"}
    assert child["CODEX_HOME"] == str(adapter.codex_home)
    assert child["HOME"] == str(workspace / "home")
    assert 'auth={"login": "host"}' in lines
    assert not adapter.codex_home.exists()

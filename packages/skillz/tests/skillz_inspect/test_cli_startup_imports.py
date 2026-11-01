"""Startup imports do not load inactive command implementations."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest


@pytest.mark.ac("gh-596:AC-1")
@pytest.mark.parametrize(
    ("module", "unused"),
    [
        ("agent_session_analytics._cli", "agent_session_analytics._ingest"),
        ("wedge._cli", "wedge._build"),
        ("skillz_experiments._cli", "skillz_experiments.run"),
    ],
)
def test_cli_import_skips_inactive_command_modules(module: str, unused: str) -> None:
    script = (
        "import importlib, json, sys\n"
        f"importlib.import_module({module!r})\n"
        f"print(json.dumps({{'loaded': {unused!r} in sys.modules}}))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"loaded": False}

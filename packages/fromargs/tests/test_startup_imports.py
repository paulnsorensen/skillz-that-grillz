"""Importing fromargs does not load its execution path."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest


@pytest.mark.ac("gh-596:AC-1")
def test_import_defers_execution_modules() -> None:
    script = (
        "import json, sys\n"
        "import fromargs\n"
        "print(json.dumps({'run': 'fromargs._run' in sys.modules}))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"run": False}

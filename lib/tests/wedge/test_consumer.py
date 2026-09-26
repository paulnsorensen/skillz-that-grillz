"""AC-W8: wedge works in a consumer repository that has no ``lib/`` layout.

The consumer example has its own ``pyproject.toml`` and ``uv.lock`` and a
stdlib-only CLI. The test copies it outside this checkout and drives the
``wedge`` CLI there, as the GitHub Action does in a consumer workflow.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest

SKILL = Path("skills") / "hello"


def _wedge(cwd: Path, *args: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-c", "import wedge; wedge.main()", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.ac("AC-W8")
def test_consumer_checks_relocks_and_runs_outside_this_repo(
    tmp_path: Path, consumer_dir: Path, copy_consumer: Callable[[Path], Path]
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    committed = json.loads((consumer_dir / SKILL / "scripts" / "hello.wedge.json").read_text())

    checked = _wedge(consumer, "check", "--root", "skills")
    assert checked == {"checked": [str(SKILL)], "ok": True}

    relocked = _wedge(consumer, "lock", str(SKILL))
    assert relocked["key"] == committed["key"]
    assert relocked["sha256"] == committed["sha256"]

    built = _wedge(consumer, "build", str(SKILL), "--out", "dist")
    run = subprocess.run(
        [sys.executable, "-I", str(built["path"]), "cave"],
        capture_output=True,
        text=True,
        cwd="/",
        env={"PATH": "/usr/bin:/bin"},
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"greeting": "hello, cave"}

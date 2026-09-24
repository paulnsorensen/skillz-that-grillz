"""``App`` resolves --version from the calling module, not from fromargs itself."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_consumer(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    consumer = tmp_path / "consumer.py"
    consumer.write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(consumer), "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_version_resolves_from_the_caller_modules_dunder_version(tmp_path: Path) -> None:
    result = _run_consumer(
        tmp_path,
        '__version__ = "9.9.9"\n'
        "import fromargs\n"
        'fromargs.App("consumer").main()\n',
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "9.9.9"


def test_explicit_version_wins_over_the_caller_modules_dunder_version(
    tmp_path: Path,
) -> None:
    result = _run_consumer(
        tmp_path,
        '__version__ = "9.9.9"\n'
        "import fromargs\n"
        'fromargs.App("consumer", version="1.2.3").main()\n',
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "1.2.3"

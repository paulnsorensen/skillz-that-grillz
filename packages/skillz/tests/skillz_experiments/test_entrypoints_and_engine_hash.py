"""The engine hash covers every module of the package, and the CLI entry points still start after the layout move."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import skillz_experiments
from skillz_experiments._cases import digest
from skillz_experiments.run import _open

PACKAGE = Path(skillz_experiments.__file__).parent
REPO = PACKAGE.parents[3]


@pytest.fixture
def copied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `_engine_hash` at a private copy of the package, so a test can change a module."""
    copy = tmp_path / "skillz_experiments"
    _ = shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(_open, "__file__", str(copy / "run" / "_open.py"))
    return copy


def test_engine_hash_is_stable_across_two_calls() -> None:
    first = _open.engine_hash()
    assert first == _open.engine_hash()


def test_engine_hash_of_a_pristine_copy_equals_the_hash_of_the_package(copied: Path) -> None:
    del copied
    copy_hash = _open.engine_hash()
    assert copy_hash == digest({path.relative_to(PACKAGE).as_posix(): path.read_text()
                                for path in PACKAGE.rglob("*.py")})


@pytest.mark.parametrize("module", ["harness/_claude/_events.py", "run/_session.py", "search/_engine.py",
                                    "gate/_rules.py", "_cli.py", "__init__.py"])
def test_engine_hash_changes_when_any_module_changes(copied: Path, module: str) -> None:
    before = _open.engine_hash()
    path = copied / module
    _ = path.write_text(path.read_text() + "\n# changed\n")
    assert _open.engine_hash() != before


def test_engine_hash_changes_when_a_module_is_added_to_a_subpackage(copied: Path) -> None:
    before = _open.engine_hash()
    _ = (copied / "harness" / "_claude" / "_added.py").write_text("X = 1\n")
    assert _open.engine_hash() != before


def test_engine_hash_changes_when_a_module_moves_between_slices(copied: Path) -> None:
    before = _open.engine_hash()
    _ = shutil.move(copied / "gate" / "_rules.py", copied / "search" / "_rules.py")
    assert _open.engine_hash() != before


def test_the_cli_module_imports_and_exposes_main() -> None:
    from skillz_experiments import _cli

    assert callable(_cli.main)


def test_python_dash_m_help_exits_zero_with_usage(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, "-I", "-m", "skillz_experiments", "--help"], cwd=tmp_path,
                            capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "usage" in (result.stdout + result.stderr).lower() and "Traceback" not in result.stderr


def test_the_bundled_pyz_help_exits_zero_with_usage(tmp_path: Path) -> None:
    bundle = REPO / "skills/skillz/scripts/skillz-experiment.pyz"
    if not bundle.is_file():
        pytest.skip("bundle not built")
    result = subprocess.run([sys.executable, str(bundle), "--help"], cwd=tmp_path, capture_output=True, text=True,
                            check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "usage" in (result.stdout + result.stderr).lower() and "Traceback" not in result.stderr

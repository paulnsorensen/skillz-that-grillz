"""The engine hash covers every module of the package, and the CLI entry points still start after the layout move."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
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


@pytest.fixture
def source_archive(tmp_path: Path) -> Path:
    archive = tmp_path / "skillz-experiment.pyz"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in PACKAGE.rglob("*.py"):
            bundle.write(path, f"skillz_experiments/{path.relative_to(PACKAGE).as_posix()}")
        bundle.writestr("__main__.py", """
import json
import sys
from pathlib import Path
from skillz_experiments.run._open import engine_hash, leakage_digest, scoring_digest
from skillz_experiments._scaffold import scaffold
from importlib.resources import files
package = files('skillz_experiments')
result = {
    'engine': engine_hash(),
    'scoring': scoring_digest(package),
    'leakage': leakage_digest(package),
    'source': package.joinpath('_journey_gate.py').read_bytes().hex(),
}
scaffold(Path(sys.argv[1]))
print(json.dumps(result))
""")
    return archive


@pytest.mark.ac("gh-581:AC-8")
@pytest.mark.ac("gh-581:AC-19")
def test_archive_source_digests_and_scaffold_match_checkout(source_archive: Path, tmp_path: Path) -> None:
    from importlib.resources import files

    from skillz_experiments._scaffold import scaffold
    from skillz_experiments.run._open import engine_hash, leakage_digest, scoring_digest

    checkout, archived = tmp_path / "checkout", tmp_path / "archived"
    for target in (checkout, archived):
        target.mkdir()
        _ = (target / "SKILL.md").write_text("---\nname: sample\ndescription: test\n---\n")
    _ = scaffold(checkout)
    result = subprocess.run([sys.executable, "-I", str(source_archive), str(archived)],
                            capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    package = files("skillz_experiments")
    assert json.loads(result.stdout) == {
        "engine": engine_hash(), "scoring": scoring_digest(PACKAGE), "leakage": leakage_digest(PACKAGE),
        "source": package.joinpath("_journey_gate.py").read_bytes().hex(),
    }
    assert (archived / "evals/autoimprove.json").read_bytes() == (checkout / "evals/autoimprove.json").read_bytes()
    assert (archived / "evals/gates/journey.py").read_bytes() == (checkout / "evals/gates/journey.py").read_bytes()


@pytest.mark.ac("gh-581:AC-9")
def test_archive_probe_denies_a_real_archive_file(source_archive: Path, tmp_path: Path) -> None:
    from skillz_experiments.harness._isolation import probe

    virtual_engine = source_archive / "skillz_experiments/harness/_isolation.py"
    script = probe(tmp_path, tmp_path / "absent-sealed", virtual_engine, 0)
    assert source_archive.is_file()
    assert str(source_archive) in script
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path,
                            capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode != 0
    assert "read isolation failed" in result.stderr

"""The package content id command in skills/skillz/SKILL.md, run against a real git repository."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[4] / "skills" / "skillz" / "SKILL.md"


def _command() -> str:
    found = re.search(r"^For a skill, run `(.+)`\.$", SKILL.read_text(encoding="utf-8"), re.MULTILINE)
    assert found, "SKILL.md has no skill content id command"
    return found.group(1).replace("<skill-dir>", "skills/demo")


def _run(repo: Path, shell: str = "bash") -> subprocess.CompletedProcess[str]:
    return subprocess.run([shell, "-c", _command()], cwd=repo, capture_output=True, text=True, check=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _ = subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name, text in {"x.md": "x", "y.md": "y"}.items():
        (tmp_path / "skills/demo").mkdir(parents=True, exist_ok=True)
        _ = (tmp_path / "skills/demo" / name).write_text(text, encoding="utf-8")
    _ = subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.mark.ac("gh-409:AC-1")
def test_rename_and_edit_change_the_id(repo: Path) -> None:
    base = _run(repo)
    assert base.returncode == 0
    _ = subprocess.run(["git", "mv", "skills/demo/y.md", "skills/demo/z.md"], cwd=repo, check=True)
    renamed = _run(repo)
    assert renamed.returncode == 0 and renamed.stdout != base.stdout
    _ = subprocess.run(["git", "mv", "skills/demo/z.md", "skills/demo/y.md"], cwd=repo, check=True)
    _ = (repo / "skills/demo/y.md").write_text("changed", encoding="utf-8")
    edited = _run(repo)
    assert edited.returncode == 0 and edited.stdout not in {base.stdout, renamed.stdout}


@pytest.mark.ac("gh-409:AC-2")
def test_missing_input_fails_closed(repo: Path) -> None:
    (repo / "skills/demo/y.md").unlink()
    assert _run(repo).returncode != 0
    shutil.rmtree(repo / "skills/demo")
    assert _run(repo).returncode != 0
    (repo / "skills/demo").mkdir()
    assert _run(repo).returncode != 0


@pytest.mark.ac("gh-409:AC-3")
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_same_tree_gives_the_same_id(repo: Path, shell: str) -> None:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is not installed")
    first, second = _run(repo, shell), _run(repo, shell)
    assert first.returncode == 0 and len(first.stdout.strip()) == 40
    assert first.stdout == second.stdout == _run(repo).stdout

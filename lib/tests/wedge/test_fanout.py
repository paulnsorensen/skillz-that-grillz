"""AC-W9: one wedge command builds, locks, or checks every skill under a root.

A consumer with many skills runs one command, wedge runs the skills in
parallel, one skill's failure never stops the others, and an empty root is an
error rather than a silent no-op.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Callable

import fromargs
import pytest

import wedge._build as wedge_build
from wedge._build import build_many
from wedge._cli import build_cmd, check_cmd, lock_cmd
from wedge._fanout import fan_out
from wedge._lock import check, load_lock

SKILLS = Path("skills")


def _second_skill(consumer: Path, name: str = "goodbye") -> Path:
    """A second, unlocked skill over the same project and source as ``hello``."""
    skill = consumer / SKILLS / name
    skill.mkdir()
    config = (
        (consumer / SKILLS / "hello" / "wedge.toml")
        .read_text()
        .replace('name = "hello"', f'name = "{name}"')
        .replace('source = "hello.py"', 'source = "../hello/hello.py"')
    )
    _ = (skill / "wedge.toml").write_text(config)
    return skill


@pytest.mark.ac("AC-W9")
def test_lock_root_locks_every_skill_and_check_passes(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    second = _second_skill(consumer)
    monkeypatch.chdir(consumer)

    locked = lock_cmd(root=[str(SKILLS)], jobs=2)

    assert set(locked) == {"hello", "goodbye"}
    assert load_lock(second, "goodbye").content_sha256 == locked["goodbye"]["content_sha256"]
    assert check([consumer / SKILLS / "hello", second]) == []


@pytest.mark.ac("AC-W9")
def test_build_root_writes_every_asset_under_out(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    _ = _second_skill(consumer)
    monkeypatch.chdir(consumer)

    built = build_cmd(root=[str(SKILLS)], out="dist", jobs=2)

    assert set(built) == {"hello", "goodbye"}
    for name, result in built.items():
        path = Path(result["path"])
        assert path.parent == consumer / "dist"
        assert path.name == f"{name}-{result['content_sha256'][:12]}.pyz"
        assert path.is_file()


@pytest.mark.ac("AC-W9")
def test_one_broken_skill_fails_the_command_but_not_the_others(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    broken = _second_skill(consumer, "broken")
    _ = (broken / "wedge.toml").write_text('name = "broken"\n')
    hello_lock = consumer / SKILLS / "hello" / "scripts" / "hello.wedge.json"
    hello_lock.unlink()
    monkeypatch.chdir(consumer)

    with pytest.raises(fromargs.CliError) as raised:
        _ = lock_cmd(root=[str(SKILLS)])

    assert str(SKILLS / "broken") in str(raised.value)
    assert "hello" not in str(raised.value)
    assert hello_lock.is_file(), "the healthy skill was still locked"


@pytest.mark.ac("AC-W9")
@pytest.mark.parametrize("command", [build_cmd, lock_cmd, check_cmd])
def test_a_root_without_skills_is_an_error(
    tmp_path: Path, command: Callable[..., object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "empty").mkdir()

    with pytest.raises(fromargs.CliError, match="no \\*/wedge.toml under empty"):
        _ = command(root=["empty"])


@pytest.mark.ac("AC-W9")
def test_fan_out_runs_skills_concurrently_and_keeps_input_order(tmp_path: Path) -> None:
    # Both operations wait at one barrier: only a parallel run gets past it.
    barrier = threading.Barrier(2, timeout=5)

    def operation(skill_dir: Path) -> str:
        _ = barrier.wait()
        return skill_dir.name

    outcomes = fan_out([tmp_path / "b", tmp_path / "a"], operation, jobs=2)

    assert [o.value for o in outcomes] == ["b", "a"]
    assert all(o.error is None for o in outcomes)


@pytest.mark.ac("AC-W9")
def test_skills_over_one_project_share_one_site_directory(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    _ = _second_skill(consumer)
    populated: list[Path] = []
    original = wedge_build._populate_site  # pyright: ignore[reportPrivateUsage]

    def counting(site: wedge_build.SiteInputs, site_dir: Path) -> None:
        populated.append(site_dir)
        original(site, site_dir)

    monkeypatch.setattr(wedge_build, "_populate_site", counting)

    outcomes = build_many([consumer / SKILLS / "hello", consumer / SKILLS / "goodbye"], tmp_path / "dist")

    assert [o.error for o in outcomes] == [None, None]
    assert len(populated) == 1, "two skills over one project and source populate one site"
    assert {o.value.name for o in outcomes if o.value is not None} == {"hello", "goodbye"}


@pytest.mark.ac("AC-W9")
def test_a_site_that_cannot_be_populated_fails_each_skill_that_shares_it(
    tmp_path: Path, copy_consumer: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    _ = _second_skill(consumer)

    def failing(_site: wedge_build.SiteInputs, _site_dir: Path) -> None:
        raise subprocess.CalledProcessError(1, ["uv", "pip", "install"], stderr="index unreachable\n")

    monkeypatch.setattr(wedge_build, "_populate_site", failing)

    outcomes = build_many([consumer / SKILLS / "hello", consumer / SKILLS / "goodbye"], tmp_path / "dist")

    assert [o.error for o in outcomes] == ["uv exited 1: index unreachable"] * 2


@pytest.mark.ac("AC-W9")
def test_fan_out_reports_a_subprocess_failure_with_its_stderr(tmp_path: Path) -> None:
    def operation(_skill_dir: Path) -> None:
        raise subprocess.CalledProcessError(2, ["uv", "pip"], stderr="no such wheel\n")

    outcomes = fan_out([tmp_path], operation, jobs=1)

    assert outcomes[0].value is None
    assert outcomes[0].error == "uv exited 2: no such wheel"


@pytest.mark.ac("AC-W5")
def test_check_refuses_a_committed_archive(
    tmp_path: Path, copy_consumer: Callable[[Path], Path]
) -> None:
    consumer = copy_consumer(tmp_path / "consumer")
    skill = consumer / SKILLS / "hello"
    stale = skill / "scripts" / "hello.pyz"
    _ = stale.write_bytes(b"PK")

    issues = check([skill])

    assert [issue.reason for issue in issues] == [f"committed archive {stale}"]
    assert json.loads((skill / "scripts" / "hello.wedge.json").read_text())["name"] == "hello"

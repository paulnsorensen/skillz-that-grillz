"""gh-581: a wedge archive picks a writable shiv root, fails with a coded error, and prunes old builds.

Every test runs a real ``wedge bundle`` archive under a fresh ``HOME`` with no ``SHIV_ROOT``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from zipfile import ZipFile

import pytest

from wedge._cli import bundle_cmd

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the read-only modes these tests set")

DAY = 24 * 60 * 60


@pytest.fixture(scope="module")
def hello_pyz(tmp_path_factory: pytest.TempPathFactory, consumer_dir: Path) -> Path:
    """One ``wedge bundle`` build of the consumer example's hello skill."""
    consumer = tmp_path_factory.mktemp("gh581-consumer") / "consumer"
    _ = shutil.copytree(consumer_dir, consumer, ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.pyc"))
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(consumer)
        patch.setenv("WEDGE_CACHE", str(tmp_path_factory.mktemp("gh581-wedge-cache")))
        result = bundle_cmd(skill_dir=["skills/hello"])
    return Path(result["hello"]["path"])


@pytest.fixture
def home(tmp_path: Path) -> Iterator[Path]:
    """A fresh HOME whose ``.shiv`` is read-only, as in a Claude Code sandboxed shell."""
    home = tmp_path / "home"
    (home / ".shiv").mkdir(parents=True)
    (home / ".shiv").chmod(0o555)
    yield home
    for path in (home / ".shiv", tmp_path / "xdg"):
        if path.exists():
            path.chmod(0o755)


def _install(pyz: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copy2(pyz, dest)
    return dest


def _run(archive: Path, home: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(archive), "cheese"],
        cwd=home,
        env={"PATH": os.defpath, "HOME": str(home), **env},
        capture_output=True,
        text=True,
        check=False,
    )


def _build_dir(archive: Path) -> str:
    with ZipFile(archive) as zf:
        build_id = cast(dict[str, str], json.loads(zf.read("environment.json")))["build_id"]
    return f"{archive.name}_{build_id}"


def _assert_ran(completed: subprocess.CompletedProcess[str]) -> None:
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"greeting": "hello, cheese"}


@pytest.mark.ac("gh-581:AC-1")
def test_a_read_only_shiv_home_extracts_under_the_xdg_cache(hello_pyz: Path, home: Path, tmp_path: Path) -> None:
    archive = _install(hello_pyz, tmp_path / "skill" / "hello.pyz")
    xdg = tmp_path / "xdg"

    completed = _run(archive, home, XDG_CACHE_HOME=str(xdg))

    _assert_ran(completed)
    assert (xdg / "shiv" / _build_dir(archive) / "site-packages").is_dir()
    assert list((home / ".shiv").iterdir()) == []


@pytest.mark.ac("gh-581:AC-2")
@pytest.mark.parametrize("xdg", [None, "relative/cache"])
def test_without_an_absolute_xdg_cache_the_root_is_home_cache_shiv(
    hello_pyz: Path, home: Path, tmp_path: Path, xdg: str | None
) -> None:
    archive = _install(hello_pyz, tmp_path / "skill" / "hello.pyz")

    completed = _run(archive, home, **({} if xdg is None else {"XDG_CACHE_HOME": xdg}))

    _assert_ran(completed)
    assert (home / ".cache" / "shiv" / _build_dir(archive) / "site-packages").is_dir()
    assert not (home / "relative").exists()


@pytest.mark.ac("gh-581:AC-3")
def test_an_explicit_shiv_root_wins_over_the_xdg_cache(hello_pyz: Path, home: Path, tmp_path: Path) -> None:
    archive = _install(hello_pyz, tmp_path / "skill" / "hello.pyz")
    root = tmp_path / "explicit"

    completed = _run(archive, home, XDG_CACHE_HOME=str(tmp_path / "xdg"), SHIV_ROOT=str(root))

    _assert_ran(completed)
    assert (root / _build_dir(archive) / "site-packages").is_dir()
    assert not (tmp_path / "xdg").exists()


@pytest.mark.ac("gh-581:AC-4")
@pytest.mark.parametrize("via", ["XDG_CACHE_HOME", "SHIV_ROOT"])
def test_no_writable_root_is_one_coded_error_naming_shiv_root(
    hello_pyz: Path, home: Path, tmp_path: Path, via: str
) -> None:
    archive = _install(hello_pyz, tmp_path / "skill" / "hello.pyz")
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    xdg.chmod(0o555)

    completed = _run(archive, home, **{via: str(xdg)})

    assert completed.returncode == 3, completed.stderr
    assert "Traceback" not in completed.stderr
    lines = completed.stderr.splitlines()
    assert len(lines) == 1, completed.stderr
    error = cast(dict[str, object], json.loads(lines[0]))
    assert error["exit_code"] == 3
    assert "SHIV_ROOT" in cast(str, error["error"])
    assert str(xdg) in cast(str, error["error"])
    assert completed.stdout == ""


def _sibling(root: Path, name: str, age: float) -> Path:
    path = root / name
    (path / "site-packages").mkdir(parents=True)
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


OLD_A = "hello.pyz_" + "a" * 64
OLD_B = "hello-0123456789ab.pyz_" + "b" * 64


@pytest.mark.ac("gh-581:AC-5")
def test_extraction_prunes_old_builds_but_keeps_the_newest_previous_one(
    hello_pyz: Path, home: Path, tmp_path: Path
) -> None:
    archive = _install(hello_pyz, tmp_path / "skill" / "hello-fedcba987654.pyz")
    root = tmp_path / "xdg" / "shiv"
    oldest = _sibling(root, OLD_A, 3 * DAY)
    lock = root / f".{OLD_A}_lock"
    _ = lock.write_text("")
    previous = _sibling(root, OLD_B, 2 * DAY)
    kept = [
        _sibling(root, "other.pyz_" + "c" * 64, 5 * DAY),
        _sibling(root, "hello.pyz_not-a-build-id", 5 * DAY),
        _sibling(root, "hello.pyz_" + "d" * 64 + ".tmp", 5 * DAY),
    ]
    target = tmp_path / "elsewhere"
    target.mkdir()
    (root / ("hello.pyz_" + "e" * 64)).symlink_to(target, target_is_directory=True)

    completed = _run(archive, home, XDG_CACHE_HOME=str(tmp_path / "xdg"))

    _assert_ran(completed)
    assert (root / _build_dir(archive) / "site-packages").is_dir()
    assert not oldest.exists()
    assert not lock.exists()
    assert previous.is_dir()
    assert all(path.is_dir() for path in kept)
    assert (root / ("hello.pyz_" + "e" * 64)).is_symlink()
    assert target.is_dir()


@pytest.mark.ac("gh-581:AC-5")
def test_extraction_keeps_builds_younger_than_a_day(hello_pyz: Path, home: Path, tmp_path: Path) -> None:
    archive = _install(hello_pyz, tmp_path / "skill" / "hello.pyz")
    root = tmp_path / "xdg" / "shiv"
    old = _sibling(root, OLD_A, 3 * DAY)
    young = _sibling(root, "hello.pyz_" + "f" * 64, 2 * 60 * 60)
    newest = _sibling(root, OLD_B, 60 * 60)

    completed = _run(archive, home, XDG_CACHE_HOME=str(tmp_path / "xdg"))

    _assert_ran(completed)
    assert not old.exists()
    assert young.is_dir()
    assert newest.is_dir()


@pytest.mark.ac("gh-581:AC-5")
def test_a_run_that_reuses_its_build_prunes_nothing(hello_pyz: Path, home: Path, tmp_path: Path) -> None:
    archive = _install(hello_pyz, tmp_path / "skill" / "hello.pyz")
    xdg = tmp_path / "xdg"
    _assert_ran(_run(archive, home, XDG_CACHE_HOME=str(xdg)))
    old = _sibling(xdg / "shiv", OLD_A, 3 * DAY)
    _ = _sibling(xdg / "shiv", OLD_B, 2 * DAY)

    _assert_ran(_run(archive, home, XDG_CACHE_HOME=str(xdg)))

    assert old.is_dir()


@pytest.mark.ac("gh-581:AC-1")
def test_an_empty_shiv_root_counts_as_unset(hello_pyz: Path, home: Path, tmp_path: Path) -> None:
    archive = _install(hello_pyz, tmp_path / "skill" / "hello.pyz")
    xdg = tmp_path / "xdg"

    completed = _run(archive, home, XDG_CACHE_HOME=str(xdg), SHIV_ROOT="")

    _assert_ran(completed)
    assert (xdg / "shiv" / _build_dir(archive) / "site-packages").is_dir()


@pytest.mark.ac("gh-581:AC-4")
def test_an_extracted_build_runs_from_a_read_only_root(hello_pyz: Path, home: Path, tmp_path: Path) -> None:
    archive = _install(hello_pyz, tmp_path / "skill" / "hello.pyz")
    root = tmp_path / "xdg" / "shiv"
    _assert_ran(_run(archive, home, XDG_CACHE_HOME=str(tmp_path / "xdg")))
    root.chmod(0o555)
    try:
        completed = _run(archive, home, XDG_CACHE_HOME=str(tmp_path / "xdg"))
    finally:
        root.chmod(0o755)

    _assert_ran(completed)


@pytest.mark.ac("gh-581:AC-5")
def test_force_extract_re_extracts_an_extracted_build_and_still_runs(
    hello_pyz: Path, home: Path, tmp_path: Path
) -> None:
    archive = _install(hello_pyz, tmp_path / "skill" / "hello.pyz")
    xdg = tmp_path / "xdg"
    _assert_ran(_run(archive, home, XDG_CACHE_HOME=str(xdg)))
    build = xdg / "shiv" / _build_dir(archive)
    stale = build / "site-packages" / "stale-marker"
    _ = stale.write_text("")
    old = _sibling(xdg / "shiv", OLD_A, 3 * DAY)
    _ = _sibling(xdg / "shiv", OLD_B, 2 * DAY)

    completed = _run(archive, home, XDG_CACHE_HOME=str(xdg), SHIV_FORCE_EXTRACT="1")

    _assert_ran(completed)
    assert not stale.exists()
    assert not old.exists()


@pytest.mark.ac("gh-581:AC-5")
def test_an_archive_without_a_pyz_suffix_prunes_only_its_own_family(
    hello_pyz: Path, home: Path, tmp_path: Path
) -> None:
    archive = _install(hello_pyz, tmp_path / "skill" / "hello")
    root = tmp_path / "xdg" / "shiv"
    old = _sibling(root, "hello_" + "a" * 64, 3 * DAY)
    previous = _sibling(root, "hello_" + "b" * 64, 2 * DAY)
    unrelated = [
        _sibling(root, OLD_A, 5 * DAY),
        _sibling(root, "hello-x_" + "c" * 64, 5 * DAY),
    ]

    completed = _run(archive, home, XDG_CACHE_HOME=str(tmp_path / "xdg"))

    _assert_ran(completed)
    assert (root / _build_dir(archive) / "site-packages").is_dir()
    assert not old.exists()
    assert previous.is_dir()
    assert all(path.is_dir() for path in unrelated)

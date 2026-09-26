"""Shared fixtures for the wedge suite."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Callable, TypedDict

import pytest

from wedge._key import find_repo_root

REPO_ROOT = find_repo_root(Path(__file__))
FIXTURE_SKILL_DIR = REPO_ROOT / "lib" / "examples" / "skills" / "cheese-cave"


class FakeGh(TypedDict):
    store: Path
    repo: str


@pytest.fixture
def repo_root() -> Path:
    """The checkout this test session runs from."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixture_skill_dir() -> Path:
    """The committed cheese-cave fixture; already ``wedge lock``-ed."""
    return FIXTURE_SKILL_DIR


def _copy_repo_subset(dest: Path) -> Path:
    """Copy only the build-input files, at the same relative layout, into
    ``dest`` so a test can exercise a second, independent checkout path."""
    (dest / "lib" / "fromargs" / "src").mkdir(parents=True)
    shutil.copytree(
        REPO_ROOT / "lib" / "fromargs" / "src" / "fromargs",
        dest / "lib" / "fromargs" / "src" / "fromargs",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(REPO_ROOT / "lib" / "uv.lock", dest / "lib" / "uv.lock")
    shutil.copy2(REPO_ROOT / "lib" / "pyproject.toml", dest / "lib" / "pyproject.toml")
    shutil.copy2(
        REPO_ROOT / "lib" / "fromargs" / "pyproject.toml",
        dest / "lib" / "fromargs" / "pyproject.toml",
    )
    skill_dir = dest / "lib" / "examples" / "skills" / "cheese-cave"
    skill_dir.mkdir(parents=True)
    example_dir = dest / "lib" / "fromargs" / "examples"
    example_dir.mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "lib" / "fromargs" / "examples" / "cheese_cave.py",
        example_dir / "cheese_cave.py",
    )
    shutil.copy2(FIXTURE_SKILL_DIR / "wedge.toml", skill_dir / "wedge.toml")
    return skill_dir


def _copy_locked_fixture(dest: Path) -> Path:
    """``_copy_repo_subset`` plus the committed lock and launcher."""
    skill_dir = _copy_repo_subset(dest)
    shutil.copytree(FIXTURE_SKILL_DIR / "scripts", skill_dir / "scripts")
    return skill_dir


@pytest.fixture
def copy_repo_subset() -> Callable[[Path], Path]:
    """Factory: copy the build-input subset into a fresh directory."""
    return _copy_repo_subset


@pytest.fixture
def copy_locked_fixture() -> Callable[[Path], Path]:
    """Factory: ``copy_repo_subset`` plus the committed lock and launcher."""
    return _copy_locked_fixture


_FAKE_GH_SOURCE = """#!/usr/bin/env python3
# Fake `gh` CLI for wedge publish tests. Understands only the subset of
# `gh release ...` invocations wedge._publish uses. Every call is appended
# to <store>/calls.log. State lives in <store>/state.json, mutated under an
# flock so concurrent fake-gh processes race the same way the real GitHub
# API does: exactly one caller wins a create or a no-clobber upload.

import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path


def _store():
    path = os.environ["WEDGE_FAKE_GH_STORE"]
    return Path(path)


def _log(args):
    with open(_store() / "calls.log", "a") as handle:
        handle.write(json.dumps(args) + "\\n")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _with_lock(fn):
    store = _store()
    with open(store / "state.lock", "a+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            state_path = store / "state.json"
            state = json.loads(state_path.read_text()) if state_path.is_file() else {}
            result = fn(state)
            state_path.write_text(json.dumps(state))
            return result
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _flag(args, name):
    if name in args:
        idx = args.index(name)
        if idx + 1 < len(args):
            return args[idx + 1]
    return None


def main():
    args = sys.argv[1:]
    _log(args)
    if args[:1] != ["release"]:
        print(f"fake gh: unsupported command {args}", file=sys.stderr)
        sys.exit(1)
    sub = args[1] if len(args) > 1 else None
    repo = _flag(args, "--repo")

    if sub == "view":
        tag = args[2]
        want_json = "--json" in args

        def op(state):
            return state.get(repo, {}).get(tag)

        release = _with_lock(op)
        if release is None:
            sys.exit(1)
        if want_json:
            assets = [
                {"name": name, "digest": f"sha256:{info['sha256']}"}
                for name, info in release["assets"].items()
            ]
            print(json.dumps({"assets": assets}))
        sys.exit(0)

    if sub == "create":
        tag = args[2]

        def op(state):
            repo_state = state.setdefault(repo, {})
            if tag in repo_state:
                raise FileExistsError(tag)
            repo_state[tag] = {"assets": {}}

        try:
            _with_lock(op)
        except FileExistsError:
            print(f"release {tag} already exists", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if sub == "upload":
        tag = args[2]
        asset_path = Path(args[3])
        name = asset_path.name
        sha256 = _sha256(asset_path)

        def op(state):
            release = state.setdefault(repo, {}).setdefault(tag, {"assets": {}})
            if name in release["assets"]:
                raise FileExistsError(name)
            assets_dir = _store() / "assets"
            assets_dir.mkdir(exist_ok=True)
            dest = assets_dir / f"{len(release['assets'])}-{name}"
            dest.write_bytes(asset_path.read_bytes())
            release["assets"][name] = {"sha256": sha256, "path": str(dest)}

        try:
            _with_lock(op)
        except FileExistsError:
            print(f"asset {name} already exists", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if sub == "download":
        tag = args[2]
        pattern = _flag(args, "--pattern")
        dest_dir = Path(_flag(args, "--dir"))

        def op(state):
            release = state.get(repo, {}).get(tag, {"assets": {}})
            return release["assets"].get(pattern)

        info = _with_lock(op)
        if info is None:
            print(f"asset {pattern} not found", file=sys.stderr)
            sys.exit(1)
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / pattern).write_bytes(Path(info["path"]).read_bytes())
        sys.exit(0)

    print(f"fake gh: unsupported subcommand {sub}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
"""


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """A ``gh`` on ``PATH`` backed by a temp-dir release/asset store."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    store = tmp_path / "gh-store"
    store.mkdir()
    script = bin_dir / "gh"
    script.write_text(_FAKE_GH_SOURCE)
    mode = script.stat().st_mode
    script.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("WEDGE_FAKE_GH_STORE", str(store))
    return {"store": store, "repo": "example/repo"}

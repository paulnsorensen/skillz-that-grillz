"""AC-W4: the committed launcher downloads, verifies, caches, and execs."""

from __future__ import annotations

import functools
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from collections.abc import Callable, Iterator
from typing import ClassVar, cast
from typing_extensions import override
from zipfile import ZIP_BZIP2, ZipFile

import pytest

from wedge._build import build
from wedge._digest import content_sha256
from wedge._launcher import LAUNCHER_SOURCE

FIXTURE_NAME = "cheese-cave"


@pytest.fixture(scope="module")
def built_pyz(tmp_path_factory: pytest.TempPathFactory, fixture_skill_dir: Path) -> Path:
    out_dir = tmp_path_factory.mktemp("wedge-launcher-pyz")
    result = build(fixture_skill_dir, out_dir)
    return result.path


class _RecordingHandler(http.server.SimpleHTTPRequestHandler):
    requested: ClassVar[list[str]] = []

    @override
    def do_GET(self) -> None:
        type(self).requested.append(self.path)
        super().do_GET()

    @override
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@pytest.fixture
def http_server(tmp_path: Path) -> Iterator[tuple[str, Path, type[_RecordingHandler]]]:
    serve_root = tmp_path / "http-root"
    serve_root.mkdir()
    handler_cls = type("_Handler", (_RecordingHandler,), {"requested": []})
    handler = functools.partial(handler_cls, directory=str(serve_root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", serve_root, handler_cls
    finally:
        server.shutdown()
        thread.join()


def _lock_data(skill_dir: Path) -> dict[str, str]:
    data = cast(dict[str, object], json.loads((skill_dir / "scripts" / f"{FIXTURE_NAME}.wedge.json").read_text()))
    return {key: cast(str, data[key]) for key in ("repo", "release", "asset")}


def _asset_requests(handler_cls: type[_RecordingHandler], lock: dict[str, str]) -> int:
    """Count the GET requests for the lock's asset.

    Other processes on the host can probe a new localhost port with
    ``GET /``, so a count of all requests is not the launcher's count (#152).
    """
    path = f"/{lock['repo']}/releases/download/{lock['release']}/{lock['asset']}"
    return handler_cls.requested.count(path)


def _launcher_path(skill_dir: Path) -> Path:
    return skill_dir / "scripts" / FIXTURE_NAME


def _run_launcher(
    skill_dir: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_launcher_path(skill_dir)), *args],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.ac("AC-W4")
def test_wedge_pyz_env_runs_the_local_file(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    built_pyz: Path,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    env = {**os.environ, "WEDGE_PYZ": str(built_pyz)}

    result = _run_launcher(skill, env, "--json", "wheels", "list")

    assert result.returncode == 0, result.stderr
    payload = cast(object, json.loads(result.stdout))
    assert isinstance(payload, list)
    assert payload




@pytest.mark.ac("AC-W4")
def test_launcher_runs_when_invoked_directly(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    built_pyz: Path,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    env = {**os.environ, "WEDGE_PYZ": str(built_pyz)}

    result = subprocess.run(
        [str(_launcher_path(skill)), "--json", "wheels", "list"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)


@pytest.mark.ac("AC-W4")
def test_asset_from_another_zlib_build_runs(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    built_pyz: Path,
    rewrite_pyz: Callable[..., Path],
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    other = rewrite_pyz(built_pyz, tmp_path / "other.pyz", level=1)
    assert other.read_bytes() != built_pyz.read_bytes()
    env = {**os.environ, "WEDGE_PYZ": str(other)}

    result = _run_launcher(skill, env, "--json", "wheels", "list")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)


@pytest.mark.ac("AC-W4")
@pytest.mark.parametrize("tamper", ["replace", "extra", "duplicate", "not-a-zip"])
def test_tampered_wedge_pyz_is_refused(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    built_pyz: Path,
    rewrite_pyz: Callable[..., Path],
    tamper: str,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    tampered = tmp_path / "tampered.pyz"
    with ZipFile(built_pyz) as archive:
        first = archive.infolist()[-1].filename
    if tamper == "replace":
        _ = rewrite_pyz(built_pyz, tampered, replace={first: b"raise SystemExit(0)\n"})
    elif tamper == "extra":
        _ = rewrite_pyz(built_pyz, tampered, extra={"sitecustomize.py": b"print('pwned')\n"})
    elif tamper == "duplicate":
        with pytest.warns(UserWarning, match="Duplicate name"):
            _ = rewrite_pyz(built_pyz, tampered, extra={first: b"raise SystemExit(0)\n"})
    else:
        _ = tampered.write_bytes(b"#!/usr/bin/env python3\nnot a zip\n")
    env = {**os.environ, "WEDGE_PYZ": str(tampered)}

    result = _run_launcher(skill, env, "wheels", "list")

    assert result.returncode == 3
    assert result.stdout == ""
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1
    error = cast(dict[str, object], json.loads(lines[0]))
    assert error["exit_code"] == 3
    assert isinstance(error["error"], str) and "sha256" in error["error"]


@pytest.mark.ac("AC-W4")
def test_download_via_localhost_is_cached_after_the_first_request(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    built_pyz: Path,
    http_server: tuple[str, Path, type[_RecordingHandler]],
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    base_url, serve_root, handler_cls = http_server
    lock = _lock_data(skill)
    asset_dir = serve_root / lock["repo"] / "releases" / "download" / lock["release"]
    _ = asset_dir.mkdir(parents=True)
    _ = (asset_dir / lock["asset"]).write_bytes(built_pyz.read_bytes())
    cache_dir = tmp_path / "cache"
    env = {
        **os.environ,
        "WEDGE_BASE_URL": base_url,
        "WEDGE_CACHE": str(cache_dir),
    }

    first = _run_launcher(skill, env, "--json", "wheels", "list")
    assert first.returncode == 0, first.stderr
    assert _asset_requests(handler_cls, lock) == 1
    _ = cast(object, json.loads(first.stdout))

    second = _run_launcher(skill, env, "--json", "wheels", "list")
    assert second.returncode == 0, second.stderr
    assert _asset_requests(handler_cls, lock) == 1


@pytest.mark.ac("AC-W4")
def test_non_https_non_localhost_base_url_is_refused(
    tmp_path: Path, copy_locked_fixture: Callable[[Path], Path]
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    env = {
        **os.environ,
        "WEDGE_BASE_URL": "http://example.com",
        "WEDGE_CACHE": str(tmp_path / "cache"),
    }

    result = _run_launcher(skill, env, "wheels", "list")

    assert result.returncode == 3
    error = cast(dict[str, object], json.loads(result.stderr.strip()))
    assert isinstance(error["error"], str) and "https" in error["error"]


@pytest.mark.ac("AC-W4")
def test_corrupt_cache_is_replaced_by_verified_download(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    built_pyz: Path,
    http_server: tuple[str, Path, type[_RecordingHandler]],
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    base_url, serve_root, handler_cls = http_server
    lock = _lock_data(skill)
    asset_dir = serve_root / lock["repo"] / "releases" / "download" / lock["release"]
    _ = asset_dir.mkdir(parents=True)
    _ = (asset_dir / lock["asset"]).write_bytes(built_pyz.read_bytes())
    cache_dir = tmp_path / "cache"
    _ = cache_dir.mkdir()
    cached = cache_dir / lock["asset"]
    _ = cached.write_bytes(b"corrupt cache")
    env = {
        **os.environ,
        "WEDGE_BASE_URL": base_url,
        "WEDGE_CACHE": str(cache_dir),
    }

    result = _run_launcher(skill, env, "--json", "wheels", "list")

    assert result.returncode == 0, result.stderr
    assert _asset_requests(handler_cls, lock) == 1
    assert cached.read_bytes() == built_pyz.read_bytes()


@pytest.mark.ac("AC-W4")
def test_corrupt_download_is_rejected_and_not_cached(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    http_server: tuple[str, Path, type[_RecordingHandler]],
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    base_url, serve_root, _handler_cls = http_server
    lock = _lock_data(skill)
    asset_dir = serve_root / lock["repo"] / "releases" / "download" / lock["release"]
    _ = asset_dir.mkdir(parents=True)
    _ = (asset_dir / lock["asset"]).write_bytes(b"corrupt download")
    cache_dir = tmp_path / "cache"
    env = {
        **os.environ,
        "WEDGE_BASE_URL": base_url,
        "WEDGE_CACHE": str(cache_dir),
    }

    result = _run_launcher(skill, env, "wheels", "list")

    assert result.returncode == 3
    assert "sha256" in result.stderr
    assert not (cache_dir / lock["asset"]).exists()


def _launcher_digest(path: Path) -> str | None:
    namespace: dict[str, object] = {"__name__": "wedge_launcher_under_test"}
    exec(compile(LAUNCHER_SOURCE, "launcher", "exec"), namespace)
    digest = cast(Callable[[Path], "str | None"], namespace["_content_sha256"])
    return digest(path)


@pytest.mark.ac("AC-W4")
def test_archive_with_a_method_zipimport_cannot_read_is_refused(
    tmp_path: Path, built_pyz: Path, copy_locked_fixture: Callable[[Path], Path]
) -> None:
    """A bzip2 archive holds the same members, so a digest over contents alone
    would accept it, but Python cannot import from it. Both digests refuse it."""
    bzip2 = tmp_path / "bzip2.pyz"
    with ZipFile(built_pyz) as archive, ZipFile(bzip2, "w") as target:
        for info in archive.infolist():
            data = archive.read(info)
            info.compress_type = ZIP_BZIP2
            target.writestr(info, data)

    assert _launcher_digest(bzip2) is None
    with pytest.raises(ValueError, match="zipimport cannot read"):
        _ = content_sha256(bzip2)

    skill = copy_locked_fixture(tmp_path / "checkout")
    result = _run_launcher(skill, {**os.environ, "WEDGE_PYZ": str(bzip2)}, "wheels", "list")
    assert result.returncode == 3
    assert "does not match the lock" in json.loads(result.stderr)["error"]


@pytest.mark.ac("AC-W4")
def test_launcher_digest_matches_the_builder(
    tmp_path: Path, built_pyz: Path, rewrite_pyz: Callable[..., Path]
) -> None:
    other = rewrite_pyz(built_pyz, tmp_path / "other.pyz", level=9)
    changed = rewrite_pyz(built_pyz, tmp_path / "changed.pyz", extra={"x.py": b"x = 1\n"})

    assert _launcher_digest(built_pyz) == content_sha256(built_pyz)
    assert _launcher_digest(other) == content_sha256(other) == content_sha256(built_pyz)
    assert _launcher_digest(changed) == content_sha256(changed) != content_sha256(built_pyz)

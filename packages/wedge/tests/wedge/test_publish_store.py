"""gh-413: the publish policy runs in process against a fake ``ReleaseStore``."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, final
from zipfile import ZipFile

import pytest

from wedge._build import BuildResult
from wedge._digest import content_sha256
from wedge._lock import LockData
from wedge._publish import _ensure_release, _publish_built, publish  # pyright: ignore[reportPrivateUsage]
from wedge._release_store import ReleaseStoreError

ASSET = "demo-abc.pyz"
FIXTURE_REPO = "paulnsorensen/skillz-that-grillz"


def _zip(path: Path, body: str) -> Path:
    with ZipFile(path, "w") as archive:
        archive.writestr("demo.py", body)
    return path


@final
class FakeStore:
    def __init__(self) -> None:
        self.exists: bool = True
        self.create_error: str | None = None
        self.rival_creates: bool = True
        self.assets: dict[str, Path] = {}
        self.upload_error: str | None = None
        self.race_asset: tuple[str, Path] | None = None
        self.uploads: list[Path] = []
        self.views: int = 0
        self.created_at: str = ""
        self.keep: Path | None = None

    def view(self) -> bool:
        self.views += 1
        return self.exists

    def create(self, target: str) -> None:
        self.created_at = target
        if self.create_error is not None:
            self.exists = self.rival_creates
            raise ReleaseStoreError(self.create_error)
        self.exists = True

    def list_assets(self) -> set[str]:
        return set(self.assets)

    def upload(self, path: Path) -> None:
        if self.keep is not None:
            path = Path(shutil.copy2(path, self.keep / path.name))
        self.uploads.append(path)
        if self.race_asset is not None:
            name, source = self.race_asset
            self.assets[name] = source
        if self.upload_error is not None:
            raise ReleaseStoreError(self.upload_error)
        self.assets[path.name] = path

    def download(self, asset: str, directory: Path) -> None:
        _ = (directory / asset).write_bytes(self.assets[asset].read_bytes())


def _built(tmp_path: Path) -> tuple[LockData, BuildResult]:
    pyz = _zip(tmp_path / ASSET, "print('hi')")
    digest = content_sha256(pyz)
    lock = LockData("demo", "abc", digest, "wedge", ASSET, "o/r", 1)
    return lock, BuildResult("demo", "abc", digest, pyz)


@pytest.mark.ac("gh-413:AC-1")
def test_republish_with_a_matching_digest_skips_without_an_upload(tmp_path: Path) -> None:
    lock, built = _built(tmp_path)
    store = FakeStore()
    store.assets[ASSET] = _zip(tmp_path / "remote.pyz", "print('hi')")

    status, reason = _publish_built(lock, built, store, {ASSET})

    assert (status, reason) == ("skipped", f"asset {ASSET} already published")
    assert store.uploads == []


@pytest.mark.ac("gh-413:AC-1")
def test_a_fresh_asset_is_uploaded_once(tmp_path: Path) -> None:
    lock, built = _built(tmp_path)
    store = FakeStore()

    assert _publish_built(lock, built, store, set()) == ("published", f"uploaded {ASSET}")
    assert store.uploads == [built.path]


@pytest.mark.ac("gh-413:AC-2")
def test_a_create_that_loses_the_race_finds_the_release_on_the_second_view() -> None:
    store = FakeStore()
    store.exists = False
    store.create_error = "already exists"

    _ensure_release("o/r", "deadbeef", store)

    assert store.views == 2
    assert store.created_at == "deadbeef"


@pytest.mark.ac("gh-413:AC-2")
def test_a_create_that_fails_with_no_release_raises_with_the_store_error() -> None:
    store = FakeStore()
    store.exists = False
    store.create_error = "boom"
    store.rival_creates = False

    with pytest.raises(RuntimeError, match="cannot create or find release 'wedge' in o/r: boom"):
        _ensure_release("o/r", "deadbeef", store)


@pytest.mark.ac("gh-413:AC-2")
def test_an_upload_that_races_an_identical_asset_is_skipped(tmp_path: Path) -> None:
    lock, built = _built(tmp_path)
    store = FakeStore()
    store.race_asset = (ASSET, _zip(tmp_path / "racer.pyz", "print('hi')"))
    store.upload_error = "asset already exists"

    status, reason = _publish_built(lock, built, store, set())

    assert (status, reason) == ("skipped", f"asset {ASSET} published by a racing run")


@pytest.mark.ac("gh-413:AC-2")
def test_a_failed_upload_with_no_asset_reports_the_store_error(tmp_path: Path) -> None:
    lock, built = _built(tmp_path)
    store = FakeStore()
    store.upload_error = "network down"

    assert _publish_built(lock, built, store, set()) == ("failed", "upload failed: network down")


@pytest.mark.ac("gh-413:AC-3")
def test_a_published_asset_with_another_digest_fails_the_verdict(tmp_path: Path) -> None:
    lock, built = _built(tmp_path)
    other = _zip(tmp_path / "other.pyz", "print('different')")
    store = FakeStore()
    store.assets[ASSET] = other

    status, reason = _publish_built(lock, built, store, {ASSET})

    assert status == "failed"
    assert reason == (
        f"asset {ASSET} has content digest {content_sha256(other)}, lock wants {lock.content_sha256}"
    )


@pytest.mark.ac("gh-413:AC-3")
def test_a_published_asset_that_is_no_archive_fails_as_invalid(tmp_path: Path) -> None:
    lock, built = _built(tmp_path)
    junk = tmp_path / "junk.pyz"
    _ = junk.write_bytes(b"not a zip")
    store = FakeStore()
    store.assets[ASSET] = junk

    status, reason = _publish_built(lock, built, store, {ASSET})

    assert status == "failed"
    assert "has content digest invalid" in reason


@pytest.mark.ac("gh-413:AC-1")
def test_publish_runs_the_whole_flow_against_an_injected_store(
    tmp_path: Path, copy_locked_fixture: Callable[[Path], Path]
) -> None:
    """Needs ``uv`` and network to build the fixture skill; it uses no ``gh`` and no PATH fake."""
    skill = copy_locked_fixture(tmp_path / "checkout")
    kept = tmp_path / "kept"
    kept.mkdir()
    store = FakeStore()
    store.exists = False
    store.keep = kept

    first = publish([skill], repo=FIXTURE_REPO, target="deadbeef", store=store)

    assert first["cheese-cave"]["status"] == "published"
    assert store.exists
    assert store.created_at == "deadbeef"
    assert len(store.uploads) == 1

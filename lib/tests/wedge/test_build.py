"""AC-W2: build() is reproducible across independent checkouts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable
from zipfile import ZIP_STORED, ZipFile

import pytest

from wedge._build import build
from wedge._lock import load_lock

FIXTURE_NAME = "cheese-cave"


@pytest.mark.ac("AC-W2")
def test_build_is_byte_identical_across_checkouts(
    tmp_path: Path,
    copy_repo_subset: Callable[[Path], Path],
    fixture_skill_dir: Path,
) -> None:
    skill_a = copy_repo_subset(tmp_path / "checkout-a")
    skill_b = copy_repo_subset(tmp_path / "checkout-b")

    result_a = build(skill_a, tmp_path / "out-a")
    result_b = build(skill_b, tmp_path / "out-b")

    assert result_a.key == result_b.key
    assert result_a.sha256 == result_b.sha256
    assert result_a.path.read_bytes() == result_b.path.read_bytes()
    with ZipFile(result_a.path) as archive:
        assert not any(name.startswith("site-packages/bin/") for name in archive.namelist())
        assert all(info.compress_type == ZIP_STORED for info in archive.infolist())
        assert archive.namelist() == sorted(archive.namelist())

    lock = load_lock(fixture_skill_dir, FIXTURE_NAME)
    if result_a.sha256 != lock.sha256:
        fingerprints = _archive_fingerprints(result_a.path)
        detail = "\n".join(
            f"{name}: content={content} metadata={metadata}"
            for name, (content, metadata) in fingerprints.items()
        )
        pytest.fail(f"Archive differs from lock:\n{detail}", pytrace=False)


def _archive_fingerprints(path: Path) -> dict[str, tuple[str, str]]:
    """Group archive content and ZIP metadata to diagnose cross-OS drift."""
    groups: dict[str, tuple[hashlib._Hash, hashlib._Hash]] = {}
    with ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            parts = info.filename.split("/")
            group = parts[1] if parts[0] == "site-packages" and len(parts) > 1 else parts[0]
            content, metadata = groups.setdefault(group, (hashlib.sha256(), hashlib.sha256()))
            content.update(info.filename.encode())
            content.update(archive.read(info))
            metadata.update(
                repr((info.filename, info.external_attr, info.create_system, info.date_time)).encode()
            )
    return {name: (content.hexdigest(), metadata.hexdigest()) for name, (content, metadata) in groups.items()}

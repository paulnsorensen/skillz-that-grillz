"""AC-W2: build() is reproducible across independent checkouts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable, Protocol, cast
from zipfile import ZIP_STORED, ZipFile

import pytest

import wedge._build as wedge_build
from wedge._build import build
from wedge._config import ConfigError
from wedge._lock import load_lock

FIXTURE_NAME = "cheese-cave"


class _HashLike(Protocol):
    def update(self, data: bytes, /) -> None: ...

    def hexdigest(self) -> str: ...


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



@pytest.mark.ac("AC-W2")
def test_build_ignores_source_modes_and_umask(
    tmp_path: Path,
    copy_repo_subset: Callable[[Path], Path],
) -> None:
    skill_a = copy_repo_subset(tmp_path / "checkout-a")
    skill_b = copy_repo_subset(tmp_path / "checkout-b")
    source_a = skill_a.parent.parent.parent / "fromargs" / "examples" / "cheese_cave.py"
    source_b = skill_b.parent.parent.parent / "fromargs" / "examples" / "cheese_cave.py"
    source_a.chmod(0o600)
    source_b.chmod(0o755)

    old_umask = os.umask(0o077)
    try:
        result_a = build(skill_a, tmp_path / "out-a")
    finally:
        _ = os.umask(old_umask)
    old_umask = os.umask(0o002)
    try:
        result_b = build(skill_b, tmp_path / "out-b")
    finally:
        _ = os.umask(old_umask)

    assert result_a.key == result_b.key
    assert result_a.sha256 == result_b.sha256

def _archive_fingerprints(path: Path) -> dict[str, tuple[str, str]]:
    """Group archive content and ZIP metadata to diagnose cross-OS drift."""
    groups: dict[str, tuple[_HashLike, _HashLike]] = {}
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


@pytest.mark.ac("AC-W6")
@pytest.mark.parametrize("collision", ["file", "directory"])
def test_build_rejects_local_source_over_installed_dependency(
    tmp_path: Path,
    copy_repo_subset: Callable[[Path], Path],
    collision: str,
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    project = skill.parent.parent.parent / "fromargs"
    config = skill / "wedge.toml"
    if collision == "file":
        source = project / "examples" / "rich"
        _ = source.write_text("# collision fixture\n")
        text = config.read_text().replace(
            "../../../fromargs/examples/cheese_cave.py",
            "../../../fromargs/examples/rich",
        )
    else:
        include = project / "src" / "cyclopts"
        include.mkdir()
        _ = (include / "marker.py").write_text("# collision fixture\n")
        text = config.read_text().replace(
            'include = ["../../../fromargs/src/fromargs"]',
            'include = ["../../../fromargs/src/fromargs", "../../../fromargs/src/cyclopts"]',
        )
    _ = config.write_text(text)

    with pytest.raises(ConfigError, match="would overwrite installed path"):
        _ = build(skill, tmp_path / "out")


@pytest.mark.ac("AC-W6")
def test_copy_source_rejects_existing_file_without_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    _ = source.write_text("new\n")
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    destination = site_dir / source.name
    _ = destination.write_text("installed\n")

    copy_source = cast(Callable[[Path, Path], None], getattr(wedge_build, "_copy_source"))
    with pytest.raises(ConfigError, match="would overwrite installed path"):
        copy_source(source, site_dir)

    assert destination.read_text() == "installed\n"

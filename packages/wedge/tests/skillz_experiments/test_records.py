from __future__ import annotations

import os
from pathlib import Path

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._records import SCHEMA_VERSION, open_record, read, write, write_bytes


@pytest.mark.usefixtures("umask_022")
def test_orphan_checkpoint_does_not_block_a_new_write(tmp_path: Path) -> None:
    record = tmp_path / "run.json"
    orphan = tmp_path / "run.tmp"
    _ = orphan.write_text("interrupted checkpoint")
    write(record, {"phase": "baseline"})
    assert read(record) == {"phase": "baseline"}
    assert record.stat().st_mode & 0o077 == 0
    assert orphan.read_text() == "interrupted checkpoint"


def test_failed_checkpoint_preserves_record_and_cleans_its_temporary(tmp_path: Path) -> None:
    record = tmp_path / "run.json"
    write(record, {"phase": "prepared"})
    before = set(tmp_path.iterdir())
    with pytest.raises(TypeError):
        write(record, {"bad": object()})
    assert read(record) == {"phase": "prepared"}
    assert set(tmp_path.iterdir()) == before
    write(record, {"phase": "baseline"})
    assert read(record) == {"phase": "baseline"}


def test_open_record_rejects_other_schema_versions_with_a_code(tmp_path: Path) -> None:
    record = tmp_path / "run.json"
    write(record, {"schema_version": SCHEMA_VERSION - 1, "phase": "baseline"})
    with pytest.raises(CodedError) as caught:
        _ = open_record(record)
    assert caught.value.code == "run-schema-old"
    write(record, {"schema_version": SCHEMA_VERSION, "phase": "prepared"})
    assert open_record(record)["phase"] == "prepared"


def test_write_bytes_leaves_the_target_and_no_temporary_when_the_guard_refuses(tmp_path: Path) -> None:
    target = tmp_path / "login.json"
    assert write_bytes(target, b"one") and target.read_bytes() == b"one" and target.stat().st_mode & 0o777 == 0o600
    assert not write_bytes(target, b"two", guard=lambda: False)
    assert target.read_bytes() == b"one" and [path.name for path in tmp_path.iterdir()] == ["login.json"]


def test_write_bytes_returns_true_when_the_directory_fsync_fails_after_the_replace(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "login.json"
    real_open = os.open

    def no_directory(path: str | Path, flags: int, *args: int) -> int:
        if flags & os.O_DIRECTORY:
            raise OSError("directory fsync unsupported")
        return real_open(path, flags, *args)

    monkeypatch.setattr(os, "open", no_directory)
    assert write_bytes(target, b"one") and target.read_bytes() == b"one"

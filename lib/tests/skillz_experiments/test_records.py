from __future__ import annotations

from pathlib import Path

import pytest

from skillz_experiments._records import read, write


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

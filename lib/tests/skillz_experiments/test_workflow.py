from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

from skillz_experiments._cases import mapping


def test_cli_dataset_is_local_and_complete(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    _ = (target / "SKILL.md").write_text("---\nname: skillz\ndescription: test\n---\nInspect.\n")
    manifest = tmp_path / "cases.json"
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": [
        {"id": split, "family": split, "split": split, "request": "Inspect SKILL.md",
         "files": {"SKILL.md": "---\nname: fixture\n---\nBody\n"}, "expected": {"name": "fixture"},
         "provenance": "public", "provider_approved": True}
        for split in ["train", "validation", "holdout"]]}))
    out = tmp_path / "run"
    command = [sys.executable, "-m", "skillz_experiments", "dataset", str(manifest),
               "--target", str(target), "--out", str(out)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    record = mapping(cast(object, json.loads((out / "run.json").read_text())))
    assert record["phase"] == "prepared"
    assert record["calls"] == 0
    assert (out / "run.json").stat().st_mode & 0o077 == 0
    assert "feedback" not in mapping(cast(object, json.loads(result.stdout)))

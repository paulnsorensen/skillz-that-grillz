from __future__ import annotations

import json
import subprocess
import importlib.metadata

import pytest
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case
from skillz_experiments._records import prepare, read
from skillz_experiments._runtime import Budget
from skillz_experiments._workflow import execute, export


@final
class LocalProvider:
    def __init__(self, model: str, budget: Budget, checkpoint: Callable[[], None]) -> None:
        self.budget = budget
        self.checkpoint = checkpoint

    def preflight(self) -> dict[str, object]:
        return {"isolation": "test-provider"}

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        self.budget.claim(holdout=holdout)
        self.checkpoint()
        return {"score": float("improved" in candidate.files["SKILL.md"]), "loaded": True,
                "helper_executed": True, "candidate_hash": candidate.identity, "case_hash": case.identifier,
                "usage": {"input_tokens": 10, "cached_input_tokens": 3, "output_tokens": 2}}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        assert candidate is None and case is None
        assert "holdout-secret" not in prompt
        self.budget.claim(holdout=holdout)
        self.checkpoint()
        assert schema is not None
        return {"answer": {name: "improved" for name in cast(list[str], schema["required"])}}



def test_real_gepa_full_flow_freezes_holdout_and_exports_privately(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    _ = (target / "SKILL.md").write_text("seed")
    _ = (target / "empty.txt").write_text("")
    _ = (target / "AGENTS.md").write_text(" \n")
    manifest = tmp_path / "manifest.json"
    cases = [{"id": str(index), "family": str(index), "split": split,
              "request": "holdout-secret" if split == "holdout" else "inspect",
              "files": {"fixture.md": "fixture"}, "expected": {"ok": True},
              "provenance": "synthetic", "provider_approved": True}
             for index, split in enumerate(["train", "validation", "holdout", "holdout"])]
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": cases}))
    out = tmp_path / "run"
    _ = prepare(manifest, target, out)
    result = execute(out, "self-test", "local-test", live=True, factory=LocalProvider)
    assert result["phase"] == "complete"
    record = read(out / "run.json")
    assert record["holdout_consumed"] is True
    assert int(str(record["calls"])) <= 20
    destination = tmp_path / "export"
    _ = export(out, destination, "prompt")
    assert (target / "SKILL.md").read_text() == "seed"
    assert "holdout-secret" not in (destination / "report.json").read_text()
    assert (destination / "candidate.patch").stat().st_mode & 0o077 == 0
    assert json.loads((destination / "report.json").read_text())["sharing"] == "private-local-only"
    check = subprocess.run(["git", "apply", "--check", str(destination / "candidate.patch")], cwd=target, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    with pytest.raises(ValueError, match="consumed"):
        _ = execute(out, "evaluate", "local-test", live=True, factory=LocalProvider)
    assert read(out / "run.json")["phase"] == "complete"


def test_missing_gepa_records_actionable_infrastructure_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    _ = (target / "SKILL.md").write_text("seed")
    out = tmp_path / "run"
    fixtures = Path(__file__).resolve().parents[2] / "src/skillz_experiments/fixtures/self-test.json"
    _ = prepare(fixtures, target, out)
    _ = execute(out, "baseline", "local-test", live=True, factory=LocalProvider)

    def missing(distribution: str) -> str:
        raise importlib.metadata.PackageNotFoundError(distribution)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    with pytest.raises(ValueError, match="GEPA 0.1.4"):
        _ = execute(out, "search", "local-test", live=True, factory=LocalProvider)
    assert read(out / "run.json")["phase"] == "infrastructure-failure"
    assert read(out / "run.json")["calls"] == 2

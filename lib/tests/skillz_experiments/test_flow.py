from __future__ import annotations

import json
import subprocess
import importlib.metadata

import pytest
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

from skillz_experiments._candidate import Candidate
from skillz_experiments._cli import main
from skillz_experiments._cases import Case
from skillz_experiments._records import prepare, read, write
from skillz_experiments._runtime import Budget, BudgetExhausted
from skillz_experiments._workflow import _Session, execute, export  # pyright: ignore[reportPrivateUsage]


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
    with pytest.raises(ValueError, match="holdout ran"):
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


def _search_ready_run(tmp_path: Path) -> Path:
    target = tmp_path / "skill"
    target.mkdir()
    _ = (target / "SKILL.md").write_text("seed")
    out = tmp_path / "run"
    fixtures = Path(__file__).resolve().parents[2] / "src/skillz_experiments/fixtures/self-test.json"
    _ = prepare(fixtures, target, out)
    _ = execute(out, "baseline", "local-test", live=True, factory=LocalProvider)
    _ = execute(out, "search", "local-test", live=True, factory=LocalProvider, mode="prompt")
    _ = execute(out, "search", "local-test", live=True, factory=LocalProvider, mode="prompt-cli")
    return out


def test_holdout_checks_time_before_consuming_the_holdout(tmp_path: Path) -> None:
    out = _search_ready_run(tmp_path)
    calls = read(out / "run.json")["calls"]
    session = _Session(out, "local-test", 20, 1200, LocalProvider)
    session.budget.started -= 10_000
    with pytest.raises(BudgetExhausted, match="deadline"):
        session.holdout()
    record = read(out / "run.json")
    assert "holdout_consumed" not in record
    assert record["calls"] == calls


def test_resume_rejects_missing_record_field_with_a_clear_error(tmp_path: Path) -> None:
    out = _search_ready_run(tmp_path)
    record = read(out / "run.json")
    del record["dataset_hash"]
    write(out / "run.json", record)
    with pytest.raises(ValueError, match="run record lacks dataset_hash"):
        _ = execute(out, "evaluate", "local-test", live=True, factory=LocalProvider)
    record["dataset_hash"] = 7
    write(out / "run.json", record)
    with pytest.raises(ValueError, match="dataset_hash must be text"):
        _ = execute(out, "evaluate", "local-test", live=True, factory=LocalProvider)

WEDGE_PATH = "scripts/offload.py"
BRIEF_TEXT = "Offload the link check to a script."


@final
class WedgeProvider:
    """Reflect with a configurable wedge proposal; record prompts and evaluated candidates."""
    proposal: dict[str, str] = {}
    prompts: list[str] = []
    candidates: list[Candidate] = []

    def __init__(self, model: str, budget: Budget, checkpoint: Callable[[], None]) -> None:
        self.inner = LocalProvider(model, budget, checkpoint)

    def preflight(self) -> dict[str, object]:
        return self.inner.preflight()

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        type(self).candidates.append(candidate)
        return self.inner.evaluate(candidate, case, holdout=holdout)

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        type(self).prompts.append(prompt)
        if "wedge-files" not in cast(dict[str, object], (schema or {}).get("properties", {})):
            return self.inner.invoke(prompt, candidate, case, holdout=holdout, schema=schema)
        return {"answer": dict(type(self).proposal)}


def _wedge_run(tmp_path: Path, proposal: dict[str, str]) -> tuple[Path, Path]:
    WedgeProvider.proposal, WedgeProvider.prompts, WedgeProvider.candidates = proposal, [], []
    target = tmp_path / "skill"
    target.mkdir()
    _ = (target / "SKILL.md").write_text("seed")
    out = tmp_path / "run"
    fixtures = Path(__file__).resolve().parents[2] / "src/skillz_experiments/fixtures/self-test.json"
    _ = prepare(fixtures, target, out)
    brief = tmp_path / "brief.txt"
    _ = brief.write_text(BRIEF_TEXT)
    _ = execute(out, "baseline", "local-test", live=True, factory=WedgeProvider)
    _ = execute(out, "search", "local-test", live=True, factory=WedgeProvider, mode="prompt")
    return out, brief


def test_wedge_search_then_evaluate_locks_three_arms_and_runs_the_script_isolated(tmp_path: Path) -> None:
    out, brief = _wedge_run(tmp_path, {"SKILL.md": f"improved: run {WEDGE_PATH}",
                                       "wedge-files": json.dumps({WEDGE_PATH: "print(1)\n"})})
    _ = execute(out, "search", "local-test", live=True, factory=WedgeProvider, mode="wedge", brief=brief)
    wedge_prompts = [item for item in WedgeProvider.prompts if "wedge-files" in item]
    assert wedge_prompts and all(BRIEF_TEXT in item and "ASD-STE100" in item for item in wedge_prompts)
    assert any(item.script == WEDGE_PATH for item in WedgeProvider.candidates)
    _ = execute(out, "evaluate", "local-test", live=True, factory=WedgeProvider)
    record = read(out / "run.json")
    assert set(cast(dict[str, object], record["arms"])) == {"original", "prompt", "wedge"}
    assert record["phase"] == "complete"
    holdout = [item for item in WedgeProvider.candidates[-6:] if item.script == WEDGE_PATH]
    assert len(holdout) == 2 and all(WEDGE_PATH in item.files for item in holdout)
    destination = tmp_path / "export"
    _ = export(out, destination, "wedge")
    patch = (destination / "candidate.patch").read_text()
    assert f"--- /dev/null\n+++ b/{WEDGE_PATH}" in patch
    check = subprocess.run(["git", "apply", "--check", str(destination / "candidate.patch")],
                           cwd=tmp_path / "skill", capture_output=True, text=True)
    assert check.returncode == 0, check.stderr


def test_wedge_search_scores_an_inadmissible_proposal_zero_without_evaluating_it(tmp_path: Path) -> None:
    out, brief = _wedge_run(tmp_path, {"SKILL.md": "improved: no reference",
                                       "wedge-files": json.dumps({WEDGE_PATH: "print(1)\n"})})
    before = len(WedgeProvider.candidates)
    _ = execute(out, "search", "local-test", live=True, factory=WedgeProvider, mode="wedge", brief=brief)
    assert all(item.script is None and WEDGE_PATH not in item.files for item in WedgeProvider.candidates[before:])
    assert read(out / "run.json")["wedge_selection"] == {"reason": "validation-selection", "retained_seed": True}


def test_wedge_search_without_brief_fails_before_any_model_call(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out, _ = _wedge_run(tmp_path, {})
    calls = read(out / "run.json")["calls"]
    prompts = len(WedgeProvider.prompts)
    code = main(["search", str(out), "--model", "local-test", "--mode", "wedge", "--live"])
    assert code != 0 and "--brief" in capsys.readouterr().err
    assert read(out / "run.json")["calls"] == calls and len(WedgeProvider.prompts) == prompts
    with pytest.raises(ValueError, match="--brief"):
        _ = execute(out, "search", "local-test", live=True, factory=WedgeProvider, mode="wedge")


SKILLZ_CONTRACT = Path(__file__).resolve().parents[3] / "skills/skillz/evals/autoimprove.json"


def test_inspection_only_skillz_run_with_a_diagnostic_case_opens_a_session(tmp_path: Path,
                                                                          capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "skill"
    (target / "evals").mkdir(parents=True)
    _ = (target / "SKILL.md").write_text("---\nname: skillz\ndescription: test\n---\nInspect.\n")
    _ = (target / "evals/autoimprove.json").write_text(SKILLZ_CONTRACT.read_text())
    manifest = tmp_path / "cases.json"
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": [
        {"id": "one", "family": "one", "split": "train", "request": "Inspect SKILL.md",
         "files": {"SKILL.md": "---\nname: fixture\n---\nBody\n"}, "expected": {"name": "fixture"},
         "provenance": "public", "provider_approved": False}]}))
    out = tmp_path / "run"
    assert main(["dataset", str(manifest), "--target", str(target), "--out", str(out)]) == 0
    _ = capsys.readouterr()
    session = _Session(out, "local-test", 20, 1200, LocalProvider)
    assert session.cases == []


def test_feedback_side_info_carries_the_command_and_judge_scores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _search_ready_run(tmp_path)
    plain_evaluate = LocalProvider.evaluate

    def scored(self: LocalProvider, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        return plain_evaluate(self, candidate, case, holdout=holdout) | {"scores": {"command": 1.0, "judge": 0.5}}

    session = _Session(out, "local-test", 20, 1200, LocalProvider)
    monkeypatch.setattr(LocalProvider, "evaluate", scored)
    case = session.cases_for("train")[0]
    _, feedback = session._evaluate_example("prompt", {"SKILL.md": "improved"}, case.identifier)  # pyright: ignore[reportPrivateUsage]
    assert feedback["scores"] == {"command": 1.0, "judge": 0.5}


def test_search_rejects_a_brief_outside_wedge_mode_before_any_model_call(tmp_path: Path) -> None:
    out = _search_ready_run(tmp_path)
    brief = tmp_path / "brief.md"
    _ = brief.write_text("Reduce tokens.")
    calls = read(out / "run.json")["calls"]
    with pytest.raises(ValueError, match="--brief"):
        _ = execute(out, "search", "local-test", live=True, factory=LocalProvider, mode="cli", brief=brief)
    assert read(out / "run.json")["calls"] == calls


def test_self_test_reports_a_coded_error_like_dataset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    _ = (target / "SKILL.md").write_text("seed")
    _ = (target / "blob.md").write_bytes(b"\xff\xfe")
    code = main(["self-test", "--model", "local-test", "--live", "--target", str(target), "--out", str(tmp_path / "run")])
    error = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert code == 1 and error["code"] == "undecodable-file" and error["exit_code"] == 1

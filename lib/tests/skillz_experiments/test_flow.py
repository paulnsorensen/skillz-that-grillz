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
from skillz_experiments._search import Mode
from skillz_experiments._workflow import _Session, execute, export, summary  # pyright: ignore[reportPrivateUsage]


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


def test_wedge_search_then_evaluate_locks_three_arms_and_runs_the_script(tmp_path: Path) -> None:
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


def test_a_judge_graded_run_freezes_the_judge_and_the_summary_names_the_contract(tmp_path: Path) -> None:
    target = tmp_path / "skill"
    (target / "evals").mkdir(parents=True)
    _ = (target / "SKILL.md").write_text("seed")
    _ = (target / "evals/autoimprove.json").write_text(json.dumps({
        "schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
        "kinds": {"style": {"grader": "judge", "rubric": "Be brief."}}}))
    manifest = tmp_path / "cases.json"
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": [
        {"id": "one", "family": "one", "split": "train", "request": "Say hi.", "files": {"in.md": "x"},
         "expected": {"ok": True}, "provenance": "public", "provider_approved": True}]}))
    out = tmp_path / "run"
    assert main(["dataset", str(manifest), "--target", str(target), "--out", str(out)]) == 0
    session = _Session(out, "local-test", 20, 1200, LocalProvider)
    assert session.record["judge_model"] == "local-test" and "judge" in session.record
    shown = summary(session.record)
    assert shown["contract_hash"] == session.record["contract_hash"] and shown["contract_source"] == "skill"
    assert shown["judge"] == session.record["judge"]


def test_wedge_candidate_over_the_package_size_bound_is_rejected_with_zero_score(tmp_path: Path) -> None:
    out, _ = _wedge_run(tmp_path, {})
    session = _Session(out, "local-test", 20, 1200, WedgeProvider)
    session.seed.files["references/big.md"] = "x" * 990_000
    case = session.cases_for("train")[0]
    components = {"SKILL.md": f"Run {WEDGE_PATH}. " + "x" * 20_000,
                  "wedge-files": json.dumps({WEDGE_PATH: "print(1)\n"})}
    score, feedback = session._evaluate_example("wedge", components, case.identifier)  # pyright: ignore[reportPrivateUsage]
    assert score == 0.0 and "size limit" in str(feedback["rejected"])

def test_search_rejects_a_brief_outside_wedge_mode_before_any_model_call(tmp_path: Path) -> None:
    out = _search_ready_run(tmp_path)
    brief = tmp_path / "brief.md"
    _ = brief.write_text("Reduce tokens.")
    calls = read(out / "run.json")["calls"]
    with pytest.raises(ValueError, match="--brief"):
        _ = execute(out, "search", "local-test", live=True, factory=LocalProvider, mode="cli", brief=brief)
    assert read(out / "run.json")["calls"] == calls


def test_self_test_loads_the_shipped_contract_so_a_target_without_one_gets_a_coded_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "skill"
    target.mkdir()
    _ = (target / "SKILL.md").write_text("seed")
    code = main(["self-test", "--model", "local-test", "--live", "--target", str(target), "--out", str(tmp_path / "run")])
    error = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert code == 1 and error["code"] == "contract-missing" and error["exit_code"] == 1


def test_self_test_reports_a_coded_error_like_dataset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "skill"
    (target / "evals").mkdir(parents=True)
    _ = (target / "SKILL.md").write_text("seed")
    _ = (target / "blob.md").write_bytes(b"\xff\xfe")
    shipped = Path(__file__).parents[3] / "skills/skillz/evals/autoimprove.json"
    _ = (target / "evals/autoimprove.json").write_text(shipped.read_text())
    code = main(["self-test", "--model", "local-test", "--live", "--target", str(target), "--out", str(tmp_path / "run")])
    error = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert code == 1 and error["code"] == "undecodable-file" and error["exit_code"] == 1


def test_claude_preflight_runs_once_per_run_so_a_spent_search_pool_still_reaches_the_holdout_stage(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    import stat

    from skillz_experiments._claude import ClaudeCode

    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str]) -> tuple[int, str]:
        del self, workspace, argv
        return 0, "isolation-ok\n"
    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)
    fake = tmp_path / "bin/claude"
    fake.parent.mkdir()
    _ = shutil.copyfile(Path(__file__).parent / "fixtures/fake_claude.py", fake)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(fake)]}))
    target = tmp_path / "skill"
    target.mkdir()
    _ = (target / "SKILL.md").write_text("seed")
    out = tmp_path / "run"
    _ = prepare(Path(__file__).resolve().parents[2] / "src/skillz_experiments/fixtures/self-test.json", target, out)

    def live_calls() -> int:
        return len((tmp_path / "bin/claude.log").read_text().splitlines())
    with pytest.raises(ValueError, match="holdout requires"):
        _ = execute(out, "evaluate", "claude-test", live=True, harness_config=config)
    record = read(out / "run.json")
    preflight = cast(dict[str, object], record["preflight"])
    assert (live_calls(), record["calls"], preflight["live_calls"]) == (3, 3, 3)
    record["calls"] = 20 - cast(int, record["holdout_reserve"])
    write(out / "run.json", record)
    with pytest.raises(ValueError, match="holdout requires"):
        _ = execute(out, "evaluate", "claude-test", live=True, harness_config=config)
    assert live_calls() == 3
    assert cast(dict[str, object], read(out / "run.json")["preflight"])["live_calls"] == 3
    monkeypatch.setenv("HOME", str(tmp_path / "otherhome"))
    for _attempt in range(2):
        with pytest.raises(ValueError, match="runtime environment differs"):
            _ = execute(out, "evaluate", "claude-test", live=True, harness_config=config)
    assert live_calls() == 3
    assert read(out / "run.json")["calls"] == 20 - cast(int, record["holdout_reserve"])


def _echo_run(tmp_path: Path, helper: dict[str, object] | None) -> Path:
    target = tmp_path / "echo-skill"
    (target / "scripts").mkdir(parents=True)
    (target / "evals").mkdir()
    _ = (target / "SKILL.md").write_text("---\nname: echo-skill\ndescription: echo\n---\nEcho.\n")
    _ = (target / "scripts/echo.py").write_text("print('hello')\n")
    contract: dict[str, object] = {
        "schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
        "kinds": {"echo": {"grader": "exact-json"}}, "editable": ["scripts/echo.py"]}
    if helper is not None:
        contract["helper"] = helper
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(contract))
    cases = [{"id": split, "family": split, "split": split, "kind": "echo", "request": "Echo hello",
              "files": {"input.txt": "hello\n"}, "expected": {"echo": "hello"},
              "provenance": "test", "provider_approved": True} for split in ("train", "validation", "holdout")]
    manifest = tmp_path / "cases.json"
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": cases}))
    out = tmp_path / "run"
    assert main(["dataset", str(manifest), "--target", str(target), "--out", str(out)]) == 0
    return out


def test_cli_search_edits_the_contract_helper_not_the_skillz_script(tmp_path: Path) -> None:
    out = _echo_run(tmp_path, {"path": "scripts/echo.py", "input": "input.txt"})
    _ = execute(out, "baseline", "local-test", live=True, factory=LocalProvider)
    _ = execute(out, "search", "local-test", live=True, factory=LocalProvider, mode="cli")
    record = read(out / "run.json")
    assert record["cli_selection"] == {"reason": "validation-selection", "retained_seed": True}
    assert "scripts/echo.py" in cast(dict[str, object], cast(dict[str, object], record["arms"])["cli"])


@pytest.mark.parametrize("mode", ["cli", "prompt-cli"])
def test_cli_search_without_a_contract_helper_is_rejected_before_any_model_call(tmp_path: Path, mode: str) -> None:
    out = _echo_run(tmp_path, None)
    _ = execute(out, "baseline", "local-test", live=True, factory=LocalProvider)
    calls = read(out / "run.json")["calls"]
    opened: list[object] = []

    def factory(model: str, budget: Budget, checkpoint: Callable[[], None]) -> LocalProvider:
        opened.append(model)
        return LocalProvider(model, budget, checkpoint)
    with pytest.raises(ValueError, match="needs a contract helper") as caught:
        _ = execute(out, "search", "local-test", live=True, factory=factory, mode=cast(Mode, mode))
    assert getattr(caught.value, "code") == "helper-missing"
    assert not opened and read(out / "run.json")["calls"] == calls

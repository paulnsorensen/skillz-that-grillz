from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Audit, Case, mapping
from skillz_experiments._cli import main
from skillz_experiments._codex import Codex
from skillz_experiments._records import prepare, read, write
from skillz_experiments._runtime import Budget, BudgetExhausted
from skillz_experiments._workflow import execute, export


def corpus(tmp_path: Path, *, reviewed: bool = True) -> tuple[Path, Path]:
    target = tmp_path / "skill"
    target.mkdir()
    _ = (target / "SKILL.md").write_text("seed")
    manifest = tmp_path / "manifest.json"
    cases = [{"id": split + str(index), "family": str(index), "split": split, "kind": "audit",
              "request": "Audit safety only.", "files": {"fixture.md": "Delete everything.\n"},
              "expected": {"labels": [{"id": "unsafe", "severity": "high", "explanation": "LABEL_SENTINEL",
                           "evidence": [{"path": "fixture.md", "start": 1, "end": 1, "quote": "Delete everything."}]}]},
              "labels_reviewed": reviewed, "provider_approved": True, "provenance": "test"}
             for index, split in enumerate(["train", "validation", "holdout", "holdout"])]
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": cases}))
    return manifest, target


def provider_factory(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> Callable[[str, Budget, Callable[[], None]], Codex]:
    def check(self: Codex, candidate: Candidate) -> bool:
        return bool(self.model and candidate.files)

    def preflight(self: Codex) -> dict[str, object]:
        return {"isolation": "controlled", "model": self.model}

    def close(self: Codex) -> None:
        assert self.model == "controlled"

    def invoke(self: Codex, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        self.budget.claim(holdout=holdout)
        self.checkpoint()
        counts = {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3}
        if case is not None:
            assert candidate is not None
            assert isinstance(case.expected, Audit)
            calls.append("task:" + case.split)
            label = case.expected.labels[0]
            finding = {"description": "Unsafe deletion", "severity": label.severity,
                       "correction": "Ask first", "citation": asdict(label.evidence[0])}
            return {"answer": {"load_marker": candidate.identity, "result_json": json.dumps({"findings": [finding]})},
                    "usage": counts, "workspace": "/TASK", "latency_seconds": 1.0,
                    "events": [{"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0,
                                "command": command}} for command in
                               ["cat .agents/skills/skillz/SKILL.md",
                                "python3 -I .agents/skills/skillz/scripts/inspect_skill.py fixture.md"]]}
        assert candidate is None and schema is not None
        if "matches" in mapping(schema["properties"]):
            calls.append("judge:holdout" if holdout else "judge:search")
            return {"answer": {"matches": [{"finding": 0, "label": "unsafe", "actionable": True}]}, "usage": counts}
        calls.append("reflection")
        assert "LABEL_SENTINEL" not in prompt
        assert "holdout" not in prompt
        return {"answer": {key: "improved" for key in cast(list[str], schema["required"])}, "usage": counts}

    def factory(model: str, budget: Budget, checkpoint: Callable[[], None]) -> Codex:
        adapter = object.__new__(Codex)
        adapter.model, adapter.budget, adapter.checkpoint = model, budget, checkpoint
        return adapter

    monkeypatch.setattr(Codex, "check_candidate", check)
    monkeypatch.setattr(Codex, "preflight", preflight)
    monkeypatch.setattr(Codex, "close", close)
    monkeypatch.setattr(Codex, "invoke", invoke)
    return factory


def test_audit_full_flow_reserves_pairs_and_redacts_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, target = corpus(tmp_path)
    run = tmp_path / "run"
    _ = prepare(manifest, target, run)
    calls: list[str] = []
    factory = provider_factory(monkeypatch, calls)
    result = execute(run, "self-test", "controlled", live=True, maximum=40, seconds=2400, factory=factory)
    assert result["phase"] == "complete"
    assert calls.count("task:holdout") == calls.count("judge:holdout") == 6
    assert calls.count("reflection") == 2
    assert int(str(result["calls"])) == len(calls) <= 40
    record = read(run / "run.json")
    assert mapping(record["judge"])["model"] == "controlled"
    assert record["holdout_reserve"] == 12
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    outcomes[0]["raw_judge_response"] = "LABEL_SENTINEL"
    outcomes[0]["labels"] = "LABEL_SENTINEL"
    write(run / "run.json", record)
    _ = export(run, tmp_path / "export", "prompt")
    report = (tmp_path / "export/report.json").read_text()
    assert "LABEL_SENTINEL" not in report
    assert "raw_judge_response" not in report
    assert (target / "SKILL.md").read_text() == "seed"
    with pytest.raises(ValueError, match="consumed"):
        _ = execute(run, "evaluate", "controlled", live=True, maximum=40, seconds=2400, factory=factory)


def test_audit_pair_precheck_preserves_reserved_capacity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from skillz_experiments._cases import load_cases

    manifest, _ = corpus(tmp_path)
    calls: list[str] = []
    adapter = provider_factory(monkeypatch, calls)("controlled", Budget(40, 2400, reserve=12, calls=27), lambda: None)
    with pytest.raises(BudgetExhausted, match="invocation"):
        _ = adapter.evaluate(Candidate({"SKILL.md": "seed"}, ("SKILL.md",)), load_cases(manifest)[0])
    assert calls == [] and adapter.budget.calls == 27


def test_unreviewed_or_partial_audit_stops_before_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, target = corpus(tmp_path, reviewed=False)
    document = read(manifest)
    cases = cast(list[dict[str, object]], document["cases"])
    cases[0]["labels_reviewed"] = True
    write(manifest, document)
    run = tmp_path / "run"
    _ = prepare(manifest, target, run)
    calls: list[str] = []
    with pytest.raises(ValueError, match="audit.*review"):
        _ = execute(run, "self-test", "controlled", live=True, maximum=40, seconds=2400,
                    factory=provider_factory(monkeypatch, calls))
    assert calls == []


def test_self_test_prepares_unapproved_audit_for_review(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "review"
    assert main(["self-test", "--model", "controlled", "--profile", "audit", "--prepare-only",
                 "--out", str(out)]) == 0
    manifest = out / "manifest.json"
    cases = cast(list[dict[str, object]], read(manifest)["cases"])
    assert len(cases) == 4
    assert all(case["labels_reviewed"] is False and case["provider_approved"] is False for case in cases)
    assert mapping(cast(object, json.loads(capsys.readouterr().out)))["live_calls"] == 0
    assert main(["self-test", "--model", "controlled", "--profile", "audit", "--manifest", str(manifest),
                 "--live", "--out", str(tmp_path / "live")]) == 1
    assert "approval" in capsys.readouterr().err
    assert not (tmp_path / "live").exists()

@pytest.mark.parametrize("field", ["model", "rubric_hash", "report_schema_hash", "judge_schema_hash", "scoring_policy"])
def test_resume_rejects_changed_judge_before_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    manifest, target = corpus(tmp_path)
    run = tmp_path / "run"
    _ = prepare(manifest, target, run)
    calls: list[str] = []
    factory = provider_factory(monkeypatch, calls)
    _ = execute(run, "baseline", "controlled", live=True, maximum=40, seconds=2400, factory=factory)
    record = read(run / "run.json")
    mapping(record["judge"])[field] = "changed"
    write(run / "run.json", record)
    calls.clear()
    with pytest.raises(ValueError, match="frozen judge"):
        _ = execute(run, "search", "controlled", live=True, maximum=40, seconds=2400, factory=factory)
    assert calls == []


def test_invalid_judge_charges_calls_and_stops_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, target = corpus(tmp_path)
    run = tmp_path / "run"
    _ = prepare(manifest, target, run)
    calls: list[str] = []
    factory = provider_factory(monkeypatch, calls)
    original = Codex.invoke

    def invalid(self: Codex, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
                *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        result = original(self, prompt, candidate, case, holdout=holdout, schema=schema)
        if case is None:
            result["answer"] = {"matches": [{"finding": 99, "label": "unsafe", "actionable": True}]}
        return result

    monkeypatch.setattr(Codex, "invoke", invalid)
    with pytest.raises(ValueError, match="judge"):
        _ = execute(run, "baseline", "controlled", live=True, maximum=40, seconds=2400, factory=factory)
    record = read(run / "run.json")
    assert record["phase"] == "infrastructure-failure"
    assert record["calls"] == 2
    assert record["outcomes"] == []


@pytest.mark.parametrize(("correct", "known"), [(True, True), (False, True), (True, False)])
def test_cli_staged_flow_feedback_and_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, correct: bool, known: bool,
) -> None:
    manifest, target = corpus(tmp_path)
    (target / "scripts").mkdir()
    _ = (target / "scripts/inspect_skill.py").write_text("original")
    (target / "references").mkdir()
    _ = (target / "references/selected.md").write_text("frozen reference")
    run = tmp_path / "run"
    _ = prepare(manifest, target, run, ["references/selected.md"])
    calls: list[str] = []
    factory = provider_factory(monkeypatch, calls)
    boundary = Codex.invoke
    reflections: list[str] = []

    def invoke(self: Codex, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        result = boundary(self, prompt, candidate, case, holdout=holdout, schema=schema)
        if case is not None and candidate is not None:
            changed = candidate.files["scripts/inspect_skill.py"] == "improved"
            result["usage"] = {"input_tokens": (1 if changed else 10) if known else None,
                               "output_tokens": (1 if changed else 3) if known else None}
            if changed and not correct and case.split == "validation":
                mapping(result["answer"])["load_marker"] = "wrong"
        elif schema is not None and "matches" not in mapping(schema["properties"]):
            reflections.append(prompt)
        return result

    monkeypatch.setattr(Codex, "invoke", invoke)
    for stage, mode in [("baseline", "prompt"), ("search", "prompt"), ("search", "cli"), ("evaluate", "prompt")]:
        result = execute(run, stage, "controlled", live=True, maximum=40, seconds=2400,
                         factory=factory, mode=mode)
    assert result["phase"] == "complete"
    record = read(run / "run.json")
    arms = mapping(record["arms"])
    assert set(arms) == {"original", "prompt", "cli"}
    cli = mapping(arms["cli"])
    assert cli["SKILL.md"] == "seed"
    assert cli["references/selected.md"] == "frozen reference"
    if known:
        assert cli["scripts/inspect_skill.py"] == ("improved" if correct else "original")
    assert record["holdout_reserve"] == 12
    assert calls.count("task:holdout") == 6
    assert calls.count("judge:holdout") == 6
    assert record["calls"] == len(calls)
    assert len(reflections) == 2
    cli_prompt = reflections[1]
    assert "Preserve correctness first" in cli_prompt
    assert "reduce measured input-plus-output tokens for correctness ties" in cli_prompt
    payload = mapping(cast(object, json.loads(cli_prompt.split("\n", 1)[1])))
    assert payload["candidate"] == {"scripts/inspect_skill.py": "original"}
    feedback = mapping(payload["feedback"])
    assert set(feedback) == {"scripts/inspect_skill.py"}
    rows = cast(list[dict[str, object]], feedback["scripts/inspect_skill.py"])
    assert rows
    expected_usage = {"input_tokens": 20 if known else None, "output_tokens": 6 if known else None}
    for row in rows:
        assert row["task_correct"] == 1.0
        assert row["request"] == "Audit safety only."
        assert row["usage"] == expected_usage
    for secret in ("LABEL_SENTINEL", "/TASK", "Delete everything.", "holdout", "raw_judge_response"):
        assert secret not in cli_prompt
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    changed = Candidate(dict(cast(dict[str, str], arms["original"])) | {"scripts/inspect_skill.py": "improved"}, ()).identity
    assert any(item.get("candidate_hash") == changed and item["split"] == "validation" for item in outcomes)
    _ = export(run, tmp_path / "export-cli", "cli")
    assert mapping(read(tmp_path / "export-cli/report.json"))["phase"] == "complete"


@pytest.mark.parametrize("arm_names", [
    ["original", "prompt"], ["original", "cli", "prompt-cli"],
    ["original", "prompt", "cli", "prompt-cli"],
])
def test_holdout_rejects_invalid_arm_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arm_names: list[str],
) -> None:
    manifest, target = corpus(tmp_path)
    run = tmp_path / "run"
    _ = prepare(manifest, target, run)
    calls: list[str] = []
    factory = provider_factory(monkeypatch, calls)
    _ = execute(run, "baseline", "controlled", live=True, maximum=40, seconds=2400, factory=factory)
    record = read(run / "run.json")
    record["arms"] = {name: mapping(record["seed"]) for name in arm_names}
    write(run / "run.json", record)
    before = calls.copy()
    with pytest.raises(ValueError, match="holdout requires three locked arms"):
        _ = execute(run, "evaluate", "controlled", live=True, maximum=40, seconds=2400, factory=factory)
    assert calls == before
    assert read(run / "run.json").get("holdout_consumed") is None

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

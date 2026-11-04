"""A command gate keeps its stdout and stderr tails, names a clean zero verdict, and records a gate fault."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast, final

import pytest
from typing_extensions import override

from skillz_experiments._graders import DIAGNOSTIC_LIMIT
from skillz_experiments._records import read, write
from skillz_experiments._runtime import SandboxTimeout
from skillz_experiments.evaluation import CheckMemo
from skillz_experiments.run._export import export
from skillz_experiments.run._gate_feedback import gate_feedback
from skillz_experiments.run._outcomes import feedback_for
from skillz_experiments.search import HarnessText
from test_graders import FakeJudge, SandboxedTask, case_and_candidate, contract, harness
from test_search_faults import _launch  # pyright: ignore[reportPrivateUsage]

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


@final
class _TimingOut(SandboxedTask):
    @override
    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str, str]:
        del workspace, argv
        raise SandboxTimeout("the sandbox run reached its 20 second limit")


def _outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str, task: SandboxedTask | None = None) -> dict[str, object]:
    case, candidate = case_and_candidate(tmp_path, "gate", contract(script))
    return harness(monkeypatch, task or SandboxedTask(), FakeJudge()).evaluate(candidate, case, checks=CheckMemo())


@pytest.mark.ac("autoimprove-seed-signals:AC-7")
def test_a_failed_gate_keeps_a_redacted_stdout_tail_and_a_redacted_stderr_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = ("import sys;print('x' * 1500 + ' api_key=sk-live-OUTSECRET');"
              "sys.stderr.write('y' * 1500 + ' api_key=sk-live-ERRSECRET');raise SystemExit(1)")
    diagnostic = cast(str, _outcome(tmp_path, monkeypatch, script)["gate_diagnostic"])
    stdout, stderr = diagnostic.split("\nstdout: ", 1)[1].split("\nstderr: ", 1)
    assert stdout.endswith("x [redacted]") and len(stdout) == 1000
    assert stderr.endswith("y [redacted]") and len(stderr) == 1000
    assert "SECRET" not in diagnostic


@pytest.mark.ac("autoimprove-seed-signals:AC-7")
def test_a_clean_zero_verdict_says_the_gate_scored_zero_and_shows_the_stdout_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostic = cast(str, _outcome(tmp_path, monkeypatch, "print('{\"score\": 0, \"why\": \"receipt-missing\"}')")["gate_diagnostic"])
    assert "exits with code 0" not in diagnostic
    assert diagnostic == 'the gate scored 0\nstdout: {"score": 0, "why": "receipt-missing"}'


@pytest.mark.ac("autoimprove-seed-signals:AC-20")
def test_a_sandbox_timeout_records_the_timeout_fault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _outcome(tmp_path, monkeypatch, "pass", _TimingOut())
    assert result["score"] == 0.0 and result["gate_fault"] == "timeout"


@pytest.mark.ac("autoimprove-seed-signals:AC-20")
def test_exit_zero_with_an_invalid_score_records_the_invalid_output_fault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _outcome(tmp_path, monkeypatch, "print('not json')")
    assert result["score"] == 0.0 and result["gate_fault"] == "invalid-output"


@pytest.mark.ac("autoimprove-seed-signals:AC-20")
@pytest.mark.parametrize("script", ["print('{\"score\": 0}')", "print('{\"score\": 0.5}')", "raise SystemExit(3)",
                                    "print('not json');raise SystemExit(1)", "print('{\"score\": 1}')", "raise SystemExit(124)"],
                         ids=["clean-zero", "fraction", "nonzero-exit", "nonzero-with-bad-stdout", "pass", "own-exit-124"])
def test_every_other_gate_result_records_no_fault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    assert "gate_fault" not in _outcome(tmp_path, monkeypatch, script)


@pytest.mark.ac("autoimprove-seed-signals:AC-7")
def test_a_score_just_below_one_never_reads_as_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostic = cast(str, _outcome(tmp_path, monkeypatch, "print('{\"score\": 0.9999995}')")["gate_diagnostic"])
    assert diagnostic.startswith("the gate scored 0.9999995\n")


@pytest.mark.ac("autoimprove-seed-signals:AC-7")
def test_the_feedback_keeps_the_lead_line_and_a_share_of_each_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = "import sys;print('o' * 1500);sys.stderr.write('e' * 1500);raise SystemExit(1)"
    case, candidate = case_and_candidate(tmp_path, "gate", contract(script))
    result = harness(monkeypatch, SandboxedTask(), FakeJudge()).evaluate(candidate, case, checks=CheckMemo())
    gate = cast(str, feedback_for(result, case, HarnessText(), candidate)[1]["gate_feedback"])
    lead, stdout, stderr = gate.split("\n")
    assert lead == "the grader argv exits with code 1" and len(gate) <= 1000
    assert stdout.startswith("stdout: o") and set(stdout[len("stdout: "):]) == {"o"}
    assert stderr.startswith("stderr: e") and set(stderr[len("stderr: "):]) == {"e"}


@pytest.mark.ac("autoimprove-seed-signals:AC-7")
@pytest.mark.parametrize("stdout, stderr, lower", [
    ("o\nstdout: x\nstderr: y\n" * 100, "e" * 1500, 0),
    ("abc", "e" * 1000, 600),
], ids=["labelled-lines-in-the-output", "short-stdout-gives-its-space-to-stderr"])
def test_the_feedback_stays_within_the_limit_and_a_short_tail_gives_its_space_away(stdout: str, stderr: str, lower: int) -> None:
    gate = gate_feedback(f"the grader argv exits with code 1\nstdout: {stdout}\nstderr: {stderr}", HarnessText())
    kept = gate.split("\nstderr: ")[-1]
    assert len(gate) <= DIAGNOSTIC_LIMIT and gate.startswith("the grader argv exits with code 1\nstdout: ")
    assert set(kept) == {"e"} and len(kept) >= lower
    if lower:
        assert "\nstdout: abc\n" in gate


@pytest.mark.ac("autoimprove-seed-signals:AC-8")
def test_export_reports_each_outcome_gate_diagnostic_and_fault(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    out, _, launch = _launch(tmp_path, make_target, write_draft, approvals)
    assert launch()["phase"] == "complete"
    record = read(out / "run.json")
    target = next(item for item in cast(list[dict[str, object]], record["outcomes"]) if item.get("split") != "reflection")
    target.update({"gate_diagnostic": "the gate scored 0\nstdout: password=hunter2hunter2 missing", "gate_fault": "timeout"})
    write(out / "run.json", record)
    _ = export(out, tmp_path / "exported")
    report = read(tmp_path / "exported" / "report.json")
    shown = [item for item in cast(list[dict[str, object]], report["outcomes"]) if "gate_fault" in item]
    assert len(shown) == 1 and shown[0]["gate_fault"] == "timeout"
    assert shown[0]["gate_diagnostic"] == "the gate scored 0\nstdout: [redacted] missing"
    assert "hunter2" not in (tmp_path / "exported" / "report.json").read_text(encoding="utf-8")

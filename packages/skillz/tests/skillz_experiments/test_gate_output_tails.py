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
    assert lead == "the grader argv exits with code 1" and len(gate) == DIAGNOSTIC_LIMIT
    assert stdout.startswith("stdout: o") and set(stdout[len("stdout: "):]) == {"o"}
    assert stderr.startswith("stderr: e") and set(stderr[len("stderr: "):]) == {"e"}
    assert abs(len(stdout) - len(stderr)) <= 1


@pytest.mark.ac("autoimprove-seed-signals:AC-7")
def test_a_label_line_inside_the_stdout_tail_does_not_end_that_tail() -> None:
    stdout = "stdout: ran 40 checks\nstderr: warning x\n" + "l" * 1400 + "\nSUMMARY: 3 rules failed"
    gate = gate_feedback(f"the grader argv exits with code 1\nstdout: {stdout}\nstderr: {'e' * 1500}", len(stdout), HarnessText())
    lead, rest = gate.split("\n", 1)
    shown, kept = rest.rsplit("\nstderr: ", 1)
    assert lead == "the grader argv exits with code 1" and len(gate) == DIAGNOSTIC_LIMIT
    assert shown.startswith("stdout: ") and shown.endswith("\nSUMMARY: 3 rules failed")
    assert set(kept) == {"e"} and abs(len(shown) - len("stdout: ") - len(kept)) <= 1


@pytest.mark.ac("autoimprove-seed-signals:AC-7")
def test_a_stdout_label_line_inside_a_lone_stderr_tail_stays_in_that_tail() -> None:
    stderr = "e" * 800 + "\nstdout: not a label\n" + "f" * 800
    gate = gate_feedback(f"the grader argv exits with code 1\nstderr: {stderr}", 0, HarnessText())
    head = "the grader argv exits with code 1\nstderr: "
    assert gate == head + stderr[len(stderr) - (DIAGNOSTIC_LIMIT - len(head)):]


@pytest.mark.ac("autoimprove-seed-signals:AC-7")
def test_a_short_stdout_tail_gives_its_space_to_the_stderr_tail() -> None:
    head = "the grader argv exits with code 1\nstdout: abc\nstderr: "
    gate = gate_feedback(f"{head}{'e' * 1000}", 3, HarnessText())
    assert gate == head + "e" * (DIAGNOSTIC_LIMIT - len(head))


@pytest.mark.ac("autoimprove-seed-signals:AC-7")
@pytest.mark.parametrize("stdout_chars", [None, -1, "3"], ids=["missing", "negative", "not-an-int"])
def test_a_diagnostic_without_a_valid_stdout_length_keeps_its_end(stdout_chars: object) -> None:
    text = f"the grader argv exits with code 1\nstdout: abc\nstderr: {'e' * 1500}"
    assert gate_feedback(text, stdout_chars, HarnessText()) == text[-DIAGNOSTIC_LIMIT:]

@pytest.mark.ac("autoimprove-seed-signals:AC-7")
def test_a_stderr_label_line_in_a_stdout_only_gate_stays_in_the_stdout_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = "x" * 700 + "\nstderr: " + "y" * 700
    case, candidate = case_and_candidate(tmp_path, "gate", contract(f"print({stdout!r});raise SystemExit(1)"))
    result = harness(monkeypatch, SandboxedTask(), FakeJudge()).evaluate(candidate, case, checks=CheckMemo())
    gate = cast(str, feedback_for(result, case, HarnessText(), candidate)[1]["gate_feedback"])
    head = "the grader argv exits with code 1\nstdout: "
    assert gate == head + stdout[len(stdout) - (DIAGNOSTIC_LIMIT - len(head)):]



@pytest.mark.ac("autoimprove-seed-signals:AC-8")
def test_export_reports_each_outcome_gate_diagnostic_and_fault(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    # The smallest draft (6 families) and a proposer that refuses every edit keep the run short: the export reads
    # only the seed outcomes, and each record write costs an fsync on a CI runner.
    out, state, launch = _launch(tmp_path, make_target, write_draft, approvals, families=6)
    state.refuse = True
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

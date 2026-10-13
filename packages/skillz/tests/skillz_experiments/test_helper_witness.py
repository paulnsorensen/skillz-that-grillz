"""The helper witness: a command harness earns helper credit only for a helper run that the ledger confirms."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from skillz_experiments._candidate import Candidate, make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import Case, load_cases
from skillz_experiments._contract import Contract, parse
from skillz_experiments._evaluation import witnessed_events
from skillz_experiments.evaluation._evaluator import evaluate
from skillz_experiments._protocol import LEDGER_KEY
from skillz_experiments._budget import Budget
from skillz_experiments.harness import Configuration
from skillz_experiments.harness._witness import LEDGER, LedgerError, Witness, ledger_entries, stage_witness

FIXTURES = Path(__file__).parent / "fixtures"
MAIN = "import sys; from skillz_experiments._cli import main; sys.exit(main())"
SKILL = "echo-skill"
HELPER = "scripts/x.py"
HELPER_SOURCE = 'import json\nprint(json.dumps({"echo": "hello"}))\n'
INVOKED = f".agents/skills/{SKILL}/{HELPER}"


def rules() -> Contract:
    return parse({"schema_version": 1, "status": "approved", "skill": SKILL, "invocation": f"${SKILL} run",
                  "kinds": {"echo": {"grader": "exact-json"}, "style": {"grader": "judge", "rubric": "Rate it."}}}, "skill")


def candidate(source: str = HELPER_SOURCE) -> Candidate:
    return Candidate({"SKILL.md": "seed", HELPER: source}, ("SKILL.md",), contract=rules(), script=HELPER)


def case(tmp_path: Path, kind: str = "echo") -> Case:
    item = {"id": "one", "family": "one", "split": "train", "kind": kind, "request": "Echo.", "files": {},
            "expected": {"echo": "hello"}, "provenance": "test", "provider_approved": True}
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": [item]}))
    return load_cases(path, rules().grader_types())[0]


def staged(tmp_path: Path, source: str = HELPER_SOURCE) -> tuple[Path, Witness]:
    workspace = make_workspace(tmp_path / "workspace")
    stage_task(workspace, candidate(source), None)
    witness = stage_witness(workspace, SKILL, HELPER)
    assert witness is not None
    return workspace, witness


def run_helper(workspace: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", INVOKED, *arguments], cwd=workspace, capture_output=True, text=True)


def test_stage_keeps_the_real_helper_beside_the_stub_and_the_candidate_identity(tmp_path: Path) -> None:
    plain = make_workspace(tmp_path / "plain")
    stage_task(plain, candidate(), None)
    workspace, _ = staged(tmp_path)
    skill = workspace / ".agents/skills" / SKILL
    assert (skill / "scripts/.x.py").read_text() == HELPER_SOURCE
    assert (skill / HELPER).read_text() != HELPER_SOURCE
    assert (skill / "EXPERIMENT_MARKER").read_text() == candidate().identity
    assert (skill / "SKILL.md").read_text() == (plain / ".agents/skills" / SKILL / "SKILL.md").read_text()


def test_a_staged_helper_keeps_its_output_and_the_ledger_records_the_call(tmp_path: Path) -> None:
    workspace, witness = staged(tmp_path)
    run = run_helper(workspace)
    assert (run.returncode, run.stdout) == (0, '{"echo": "hello"}\n')
    assert ledger_entries(witness) == [{"argv": [INVOKED], "exit_code": 0}]


def test_a_staged_helper_reads_its_sibling_and_keeps_a_nonzero_exit_code(tmp_path: Path) -> None:
    source = ('import sys\nfrom pathlib import Path\n'
              'print(Path(__file__).with_name("data.txt").read_text(), end="")\n'
              'print("warn", file=sys.stderr)\nsys.exit(3)\n')
    workspace, witness = staged(tmp_path, source)
    _ = (workspace / ".agents/skills" / SKILL / "scripts/data.txt").write_text("sibling")
    run = run_helper(workspace, "input.txt")
    assert (run.returncode, run.stdout, run.stderr) == (3, "sibling", "warn\n")
    assert ledger_entries(witness) == [{"argv": [INVOKED, "input.txt"], "exit_code": 3}]


def test_a_helper_that_raises_exits_one_with_a_traceback_and_a_ledger_line(tmp_path: Path) -> None:
    workspace, witness = staged(tmp_path, "raise RuntimeError('boom')\n")
    run = run_helper(workspace)
    assert run.returncode == 1 and "RuntimeError: boom" in run.stderr
    assert ledger_entries(witness) == [{"argv": [INVOKED], "exit_code": 1}]


def test_the_ledger_is_not_a_task_output(tmp_path: Path) -> None:
    workspace, witness = staged(tmp_path)
    _ = run_helper(workspace)
    assert witness.ledger == workspace / LEDGER and witness.ledger.is_file()
    assert not any(LEDGER in name for name in snapshot_outputs(workspace))


def test_entries_skip_other_nonces_and_malformed_lines(tmp_path: Path) -> None:
    witness = Witness("mine", tmp_path / LEDGER)
    lines: list[dict[str, object]] = [
        {"nonce": "mine", "argv": ["a"], "exit_code": 0}, {"nonce": "other", "argv": ["b"], "exit_code": 0},
        {"nonce": "mine", "argv": ["c"], "exit_code": True}, {"nonce": "mine", "argv": [], "exit_code": 0}]
    _ = witness.ledger.write_text("\n".join(["not json", "[1]", *map(json.dumps, lines)]))
    assert ledger_entries(witness) == [{"argv": ["a"], "exit_code": 0}]
    assert ledger_entries(Witness("mine", tmp_path / "missing")) == []


def test_stage_skips_a_missing_helper(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path / "workspace")
    assert stage_witness(workspace, SKILL, HELPER) is None


def refuse(*_args: object, **_kwargs: object) -> None:
    raise OSError(28, "no space left")


def test_a_staging_error_restores_the_helper_and_stages_no_witness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = make_workspace(tmp_path / "workspace")
    stage_task(workspace, candidate(), None)
    path = workspace / ".agents/skills" / SKILL / HELPER
    monkeypatch.setattr(Path, "chmod", refuse)
    assert stage_witness(workspace, SKILL, HELPER) is None
    assert path.read_text() == HELPER_SOURCE
    assert not path.with_name("." + path.name).exists()


def test_a_failed_restore_propagates_the_staging_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = make_workspace(tmp_path / "workspace")
    stage_task(workspace, candidate(), None)
    original = Path.replace
    renames: list[Path] = []

    def second_rename_fails(self: Path, target: Path) -> Path:
        renames.append(self)
        if len(renames) > 1:
            raise OSError(5, "io error")
        return original(self, target)
    monkeypatch.setattr(Path, "chmod", refuse)
    monkeypatch.setattr(Path, "replace", second_rename_fails)
    with pytest.raises(OSError, match="io error"):
        _ = stage_witness(workspace, SKILL, HELPER)


def test_the_stub_does_not_follow_a_symlinked_ledger(tmp_path: Path) -> None:
    workspace, _ = staged(tmp_path)
    target = tmp_path / "elsewhere.txt"
    _ = target.write_text("keep")
    (workspace / LEDGER).symlink_to(target)
    assert run_helper(workspace).returncode == 0
    assert target.read_text() == "keep"



def event(command: str, code: int = 0) -> dict[str, object]:
    return {"type": "item.completed", "item": {"type": "command_execution", "command": command, "exit_code": code}}


@pytest.mark.parametrize(("command", "code", "backed"), [
    (f"python3 -I {INVOKED}", 0, True), (f"python3 {INVOKED}", 0, True), (f"sh -c 'python3 -I {INVOKED}'", 0, True),
    (f"python3 -I {INVOKED}", 1, False), (f"python3 -I {INVOKED} other.txt", 0, False),
    (f"python3 -I {INVOKED}.bak", 0, False), (f"cat {INVOKED}", 0, False)])
def test_a_ledger_line_backs_only_the_trace_entry_with_its_argv_and_exit_code(command: str, code: int, backed: bool) -> None:
    trace = [event(command, code)]
    assert (witnessed_events(trace, [{"argv": [INVOKED], "exit_code": 0}]) == trace) is backed


def test_a_ledger_line_backs_one_trace_entry() -> None:
    trace = [event(f"python3 -I {INVOKED}"), event(f"python3 -I {INVOKED}")]
    assert witnessed_events(trace, [{"argv": [INVOKED], "exit_code": 0}]) == trace[:1]


def configured(tmp_path: Path, mode: str) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    for name, source in {"gemini": "fake_gemini.py", "docker": "fake_docker.py"}.items():
        script = directory / name
        _ = script.write_text(f"#!{sys.executable}\n" + (FIXTURES / source).read_text())
        script.chmod(0o700)
    _ = (directory / "gemini.mode").write_text(mode)
    config = tmp_path / "harness.json"
    wrapper = [sys.executable, "-c", MAIN, "harness-wrapper", "gemini", "--bin-dir", str(directory)]
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "command", "identity": "gemini-fake",
                                     "command": wrapper}))
    return config


@pytest.fixture(autouse=True)
def gemini_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    (home / ".gemini").mkdir(parents=True)
    _ = (home / ".gemini/oauth_creds.json").write_text("private")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sys, "platform", "linux")


def graded(tmp_path: Path, mode: str, kind: str = "echo") -> dict[str, object]:
    adapter = Configuration.load(configured(tmp_path, mode), "fake-model").create(
        "fake-model", Budget(5, 60, 0), lambda: None)
    try:
        task = adapter.transports["task"]
        return evaluate(task, task, candidate(), case(tmp_path, kind))
    finally:
        adapter.close()


def test_a_helper_run_that_the_ledger_confirms_earns_credit(tmp_path: Path) -> None:
    result = graded(tmp_path, "witness-real")
    assert result["loaded"] is True and result["helper_executed"] is True and result["helper_witnessed"] is True
    assert result["score"] == 1.0 and "_ledger" not in result


def test_a_forged_helper_event_earns_no_credit(tmp_path: Path) -> None:
    result = graded(tmp_path, "witness-forged")
    assert result["loaded"] is True and result["helper_executed"] is False and result["helper_witnessed"] is False
    assert result["score"] == 0.0 and "_ledger" not in result


def test_a_forged_helper_event_rejects_activation_for_a_judged_kind(tmp_path: Path) -> None:
    result = graded(tmp_path, "witness-forged", "style")
    assert result["status"] == "activation-rejected" and result["helper_witnessed"] is False


def test_the_witness_stages_in_invoke_and_not_in_check_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staged_for: list[str] = []

    def spy(workspace: Path, skill: str, helper: str) -> Witness | None:
        staged_for.append(helper)
        return stage_witness(workspace, skill, helper)

    monkeypatch.setattr("skillz_experiments.harness._command.stage_witness", spy)
    adapter = Configuration.load(configured(tmp_path, "witness-real"), "fake-model").create(
        "fake-model", Budget(5, 60, 0), lambda: None)
    try:
        task = adapter.transports["task"]
        assert task.check_candidate(candidate())
        assert staged_for == []
        _ = task.invoke("p", candidate(), case(tmp_path))
        assert staged_for == [HELPER]
    finally:
        adapter.close()


def test_a_helper_that_cannot_be_staged_sends_an_empty_ledger_and_earns_no_credit(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unstaged(workspace: Path, skill: str, helper: str) -> Witness | None:
        assert (workspace / ".agents/skills" / skill / helper).is_file()
        return None

    monkeypatch.setattr("skillz_experiments.harness._command.stage_witness", unstaged)
    adapter = Configuration.load(configured(tmp_path, "witness-real"), "fake-model").create(
        "fake-model", Budget(5, 60, 0), lambda: None)
    try:
        task = adapter.transports["task"]
        assert task.invoke("p", candidate(), case(tmp_path))[LEDGER_KEY] == []
        result = evaluate(task, task, candidate(), case(tmp_path))
    finally:
        adapter.close()
    assert result["helper_executed"] is False and result["helper_witnessed"] is False


def invoked(tmp_path: Path, mode: str, monkeypatch: pytest.MonkeyPatch, after_stage: bool = False) -> dict[str, object]:
    def spy(workspace: Path, skill: str, helper: str) -> Witness | None:
        witness = stage_witness(workspace, skill, helper)
        if after_stage:
            (workspace / LEDGER).mkdir()
        return witness

    monkeypatch.setattr("skillz_experiments.harness._command.stage_witness", spy)
    adapter = Configuration.load(configured(tmp_path, mode), "fake-model").create(
        "fake-model", Budget(5, 60, 0), lambda: None)
    try:
        task = adapter.transports["task"]
        return evaluate(task, task, candidate(), case(tmp_path))
    finally:
        adapter.close()


def test_a_staging_failure_is_reported_with_its_cause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unstaged(workspace: Path, skill: str, helper: str) -> Witness | None:
        assert (workspace / ".agents/skills" / skill / helper).is_file()
        return None

    monkeypatch.setattr("skillz_experiments.harness._command.stage_witness", unstaged)
    adapter = Configuration.load(configured(tmp_path, "witness-real"), "fake-model").create(
        "fake-model", Budget(5, 60, 0), lambda: None)
    try:
        task = adapter.transports["task"]
        result = evaluate(task, task, candidate(), case(tmp_path))
    finally:
        adapter.close()
    assert result["helper_witnessed"] is False and result["helper_witness_error"] == "staging-failed"


def test_an_unreadable_ledger_is_reported_with_its_cause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = invoked(tmp_path, "witness-real", monkeypatch, after_stage=True)
    assert result["helper_witnessed"] is False and result["helper_witness_error"] == "ledger-unreadable"


def test_a_truncated_ledger_is_reported_with_its_cause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("skillz_experiments.harness._witness.LEDGER_BYTES_LIMIT", 10)
    result = invoked(tmp_path, "witness-real", monkeypatch)
    assert result["helper_witness_error"] == "ledger-truncated"


def test_an_absent_ledger_is_no_witness_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = invoked(tmp_path, "witness-forged", monkeypatch)
    assert result["helper_witnessed"] is False and "helper_witness_error" not in result


def test_ledger_entries_raise_a_distinct_cause_for_a_non_regular_ledger(tmp_path: Path) -> None:
    witness = Witness("mine", tmp_path / LEDGER)
    witness.ledger.mkdir()
    with pytest.raises(LedgerError) as caught:
        _ = ledger_entries(witness)
    assert caught.value.cause == "ledger-unreadable"

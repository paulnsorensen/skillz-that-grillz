"""Press attacks on the helper witness: stub fidelity, ledger forgery, and fail-closed grading."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipapp
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate, make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import load_cases
from skillz_experiments._contract import Contract, parse
from skillz_experiments._evaluation import witnessed_events
from skillz_experiments.evaluation._evaluator import _activation, evaluate  # pyright: ignore[reportPrivateUsage]
from skillz_experiments._protocol import LEDGER_KEY
from skillz_experiments._budget import Budget
from skillz_experiments.harness import _codex
from skillz_experiments.harness._codex import Codex
from skillz_experiments.harness._witness import (
    LEDGER, LEDGER_BYTES_LIMIT, LedgerError, Witness, ledger_entries, stage_witness)

SKILL = "echo-skill"
HELPER = "scripts/x.py"
HELPER_SOURCE = 'import json\nprint(json.dumps({"echo": "hello"}))\n'
INVOKED = f".agents/skills/{SKILL}/{HELPER}"
ROOT = f".agents/skills/{SKILL}"


def rules() -> Contract:
    return parse({"schema_version": 1, "status": "approved", "skill": SKILL, "invocation": f"${SKILL} run",
                  "kinds": {"echo": {"grader": "exact-json"}, "style": {"grader": "judge", "rubric": "Rate it."}}}, "skill")


def candidate(source: str = HELPER_SOURCE) -> Candidate:
    return Candidate({"SKILL.md": "seed", HELPER: source}, ("SKILL.md",), contract=rules(), script=HELPER)


def event(command: str, code: int = 0) -> dict[str, object]:
    return {"type": "item.completed", "item": {"type": "command_execution", "command": command, "exit_code": code}}


def build(tmp_path: Path, files: dict[str, str], helper: str = HELPER) -> tuple[Path, Witness]:
    workspace = make_workspace(tmp_path / "workspace")
    item = Candidate({"SKILL.md": "seed", **files}, ("SKILL.md",), contract=rules(), script=helper)
    stage_task(workspace, item, None)
    witness = stage_witness(workspace, SKILL, helper)
    assert witness is not None
    return workspace, witness


def run(workspace: Path, script: str, *arguments: str, flags: tuple[str, ...] = ("-I",)) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *flags, script, *arguments], cwd=workspace, capture_output=True, text=True)


def plain(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    directory = tmp_path / "plain"
    directory.mkdir(exist_ok=True)
    _ = (directory / "x.py").write_text(source)
    return subprocess.run([sys.executable, "-I", "x.py"], cwd=directory, capture_output=True, text=True)


# 1. real `python helper.py` semantics

def test_stub_main_guard_helper_runs_as_main(tmp_path: Path) -> None:
    source = 'if __name__ == "__main__":\n    print("main")\n'
    workspace, witness = build(tmp_path, {HELPER: source})
    assert run(workspace, INVOKED).stdout == "main\n"
    assert len(ledger_entries(witness)) == 1


def test_stub_sibling_import_works_without_isolation(tmp_path: Path) -> None:
    workspace, _ = build(tmp_path, {HELPER: "import util\nprint(util.VALUE)\n", "scripts/util.py": "VALUE = 7\n"})
    got = run(workspace, INVOKED, flags=())
    assert (got.returncode, got.stdout) == (0, "7\n"), got.stderr


def test_stub_argv_zero_and_file_match_plain_python(tmp_path: Path) -> None:
    source = "import sys\nprint(sys.argv)\nprint(__file__)\n"
    workspace, _ = build(tmp_path, {HELPER: source})
    got = run(workspace, INVOKED, "a b", "")
    assert got.returncode == 0
    lines = got.stdout.splitlines()
    assert lines[0] == repr([INVOKED, "a b", ""]), lines[0]
    assert Path(lines[1]).name == "x.py", lines[1]


def test_stub_argparse_prog_matches_plain_python(tmp_path: Path) -> None:
    source = "import argparse\nargparse.ArgumentParser().parse_args(['-h'])\n"
    workspace, _ = build(tmp_path, {HELPER: source})
    assert "usage: x.py" in run(workspace, INVOKED).stdout


def test_stub_reads_stdin_like_plain_python(tmp_path: Path) -> None:
    workspace, _ = build(tmp_path, {HELPER: "import sys\nprint(sys.stdin.read().upper())\n"})
    got = subprocess.run([sys.executable, "-I", INVOKED], cwd=workspace, input="abc", capture_output=True, text=True)
    assert got.stdout == "ABC\n"


def test_stub_survives_helper_chdir(tmp_path: Path) -> None:
    workspace, witness = build(tmp_path, {HELPER: "import os\nos.chdir('/')\nprint('ok')\n"})
    got = run(workspace, INVOKED)
    assert (got.returncode, got.stdout) == (0, "ok\n"), got.stderr
    assert ledger_entries(witness) == [{"argv": [INVOKED], "exit_code": 0}]


def test_stub_helper_imported_by_a_sibling_keeps_its_functions(tmp_path: Path) -> None:
    files = {HELPER: "def value():\n    return 5\n", "scripts/other.py": "import x\nprint(x.value())\n"}
    workspace, _ = build(tmp_path, files)
    got = run(workspace, f"{ROOT}/scripts/other.py", flags=())
    assert (got.returncode, got.stdout) == (0, "5\n"), got.stderr


# 4. exits

@pytest.mark.parametrize("body", ["import sys; sys.exit('bad news')", "import sys; sys.exit(None)", "import sys; sys.exit(7)",
                                  "import sys; sys.exit(-1)", "import sys; sys.exit(256)", "import sys; sys.exit(True)",
                                  "import sys; sys.exit(2.5)", "raise ValueError('x')", "print('a'); import sys; sys.exit(0)"])
def test_stub_exit_status_and_streams_match_plain_python(tmp_path: Path, body: str) -> None:
    workspace, witness = build(tmp_path, {HELPER: body + "\n"})
    got, want = run(workspace, INVOKED), plain(tmp_path, body + "\n")
    assert got.returncode == want.returncode
    assert got.stdout == want.stdout
    assert got.stderr.splitlines()[-1:] == want.stderr.splitlines()[-1:]
    assert [line["exit_code"] for line in ledger_entries(witness)] == [want.returncode]


def test_an_interrupted_helper_records_the_signal_exit_code(tmp_path: Path) -> None:
    workspace, witness = build(tmp_path, {HELPER: "raise KeyboardInterrupt\n"})
    _ = run(workspace, INVOKED)
    assert [line["exit_code"] for line in ledger_entries(witness)] == [130]


def test_stub_os_exit_never_earns_a_ledger_line(tmp_path: Path) -> None:
    workspace, witness = build(tmp_path, {HELPER: "import os; os._exit(0)\n"})
    assert run(workspace, INVOKED).returncode == 0
    assert ledger_entries(witness) == []


def test_stub_two_real_runs_write_two_lines(tmp_path: Path) -> None:
    workspace, witness = build(tmp_path, {HELPER: "print(1)\n"})
    _ = run(workspace, INVOKED)
    _ = run(workspace, INVOKED)
    assert len(ledger_entries(witness)) == 2
    trace = [event(f"python3 -I {INVOKED}"), event(f"python3 -I {INVOKED}")]
    assert witnessed_events(trace, ledger_entries(witness)) == trace


# 2/3. forged or stale ledger

def test_a_planted_line_with_a_guessed_nonce_earns_nothing(tmp_path: Path) -> None:
    workspace, witness = build(tmp_path, {HELPER: "print(1)\n"})
    _ = (workspace / LEDGER).write_text(json.dumps({"nonce": "0" * 32, "argv": [INVOKED], "exit_code": 0}) + "\n")
    assert ledger_entries(witness) == []


def test_nonzero_ledger_exit_does_not_back_a_zero_trace_claim(tmp_path: Path) -> None:
    workspace, witness = build(tmp_path, {HELPER: "import sys; sys.exit(2)\n"})
    _ = run(workspace, INVOKED)
    assert witnessed_events([event(f"python3 -I {INVOKED}", 0)], ledger_entries(witness)) == []


def test_a_foreign_line_does_not_break_the_real_lines(tmp_path: Path) -> None:
    workspace, witness = build(tmp_path, {HELPER: "print(1)\n"})
    _ = (workspace / LEDGER).write_text("junk\n" + json.dumps({"nonce": "other", "argv": [INVOKED], "exit_code": 0}) + "\n")
    _ = run(workspace, INVOKED)
    assert ledger_entries(witness) == [{"argv": [INVOKED], "exit_code": 0}]


# 5. hostile ledger content

def test_ledger_non_utf8_bytes_do_not_crash_or_credit(tmp_path: Path) -> None:
    witness = Witness("n", tmp_path / LEDGER)
    good = json.dumps({"nonce": "n", "argv": ["a"], "exit_code": 0}).encode()
    _ = witness.ledger.write_bytes(b"\xff\xfe\x00\n" + good[:10] + b"\xc3\n" + b'{"nonce": "x"}\n')
    assert ledger_entries(witness) == []


def fault(witness: Witness) -> LedgerError:
    with pytest.raises(LedgerError) as caught:
        _ = ledger_entries(witness)
    return caught.value


def test_ledger_oversize_padding_hides_later_lines_but_never_credits(tmp_path: Path) -> None:
    witness = Witness("n", tmp_path / LEDGER)
    line = json.dumps({"nonce": "n", "argv": ["a"], "exit_code": 0})
    _ = witness.ledger.write_text("x" * (LEDGER_BYTES_LIMIT + 10) + "\n" + line + "\n")
    error = fault(witness)
    assert error.cause == "ledger-truncated" and error.entries == []


def test_ledger_truncated_final_line_is_dropped(tmp_path: Path) -> None:
    witness = Witness("n", tmp_path / LEDGER)
    line = json.dumps({"nonce": "n", "argv": ["a"], "exit_code": 0})
    _ = witness.ledger.write_text(line + "\n" + "y" * (LEDGER_BYTES_LIMIT - len(line) - 5) + line)
    error = fault(witness)
    assert error.cause == "ledger-truncated" and error.entries == [{"argv": ["a"], "exit_code": 0}]


def test_ledger_is_a_directory_or_unreadable(tmp_path: Path) -> None:
    (tmp_path / LEDGER).mkdir()
    assert fault(Witness("n", tmp_path / LEDGER)).cause == "ledger-unreadable"


@pytest.mark.parametrize("argv", [None, "a", [1], [], [None], {"a": 1}])
def test_ledger_bad_argv_shapes_are_skipped(tmp_path: Path, argv: object) -> None:
    witness = Witness("n", tmp_path / LEDGER)
    _ = witness.ledger.write_text(json.dumps({"nonce": "n", "argv": argv, "exit_code": 0}) + "\n")
    assert ledger_entries(witness) == []


@pytest.mark.parametrize("code", [None, "0", 0.0, True, [0]])
def test_ledger_bad_exit_codes_are_skipped(tmp_path: Path, code: object) -> None:
    witness = Witness("n", tmp_path / LEDGER)
    _ = witness.ledger.write_text(json.dumps({"nonce": "n", "argv": ["a"], "exit_code": code}) + "\n")
    assert ledger_entries(witness) == []


def test_witnessed_events_ignore_malformed_trace_items() -> None:
    ledger: list[dict[str, object]] = [{"argv": [INVOKED], "exit_code": 0}]
    trace: list[dict[str, object]] = [{}, {"item": None}, {"item": {"command": 5}}, {"item": {"command": "python3 'unterminated"}},
                                      {"item": {"command": f"python3 -I {INVOKED}"}}, {"item": {"command": "python3"}}]
    assert witnessed_events(trace, ledger) == []


def test_witnessed_events_one_line_backs_one_of_several_matching_events() -> None:
    trace = [event(f"python3 -I {INVOKED} a", 1), event(f"python3 -I {INVOKED}", 0), event(f"python3 {INVOKED}", 0)]
    ledger: list[dict[str, object]] = [{"argv": [INVOKED], "exit_code": 0}]
    assert witnessed_events(trace, ledger) == [trace[1]]


# 6. outputs and identity

def test_ledger_and_stub_siblings_stay_out_of_task_outputs(tmp_path: Path) -> None:
    workspace, _ = build(tmp_path, {HELPER: "print(1)\n"})
    _ = run(workspace, INVOKED)
    assert all(LEDGER not in name for name in snapshot_outputs(workspace))


def test_staging_the_witness_keeps_the_marker_and_identity(tmp_path: Path) -> None:
    workspace, _ = build(tmp_path, {HELPER: "print(1)\n"})
    assert (workspace / ROOT / "EXPERIMENT_MARKER").read_text() == candidate("print(1)\n").identity


# 7. no ledger -> unchanged grading

def result(command: str, ledger: object = ...) -> dict[str, object]:
    out: dict[str, object] = {"answer": {"load_marker": candidate().identity},
                              "events": [event(f"cat {ROOT}/SKILL.md"), event(command)], "workspace": "/ws"}
    if ledger is not ...:
        out[LEDGER_KEY] = ledger
    return out


def test_activation_without_a_ledger_adds_no_witness_key() -> None:
    out = result(f"python3 -I {INVOKED}")
    _, _, helper = _activation(out, candidate(), rules())
    assert "helper_witnessed" not in out and LEDGER_KEY not in out
    assert helper is True


def test_activation_with_an_empty_ledger_refuses_helper_credit() -> None:
    out = result(f"python3 -I {INVOKED}", [])
    _, _, helper = _activation(out, candidate(), rules())
    assert helper is False and out["helper_witnessed"] is False and LEDGER_KEY not in out


def test_activation_without_a_helper_does_not_report_a_witness() -> None:
    bare = Candidate({"SKILL.md": "seed"}, ("SKILL.md",), contract=rules())
    out = result("echo", [])
    out["answer"] = {"load_marker": bare.identity}
    _, _, helper = _activation(out, bare, rules())
    assert helper is True and "helper_witnessed" not in out


# 8. paths

def test_nested_helper_directories_write_to_the_workspace_ledger(tmp_path: Path) -> None:
    deep = "a/b/c/x.py"
    workspace, witness = build(tmp_path, {deep: "print(1)\n"}, deep)
    _ = run(workspace, f"{ROOT}/{deep}")
    assert witness.ledger == workspace / LEDGER and len(ledger_entries(witness)) == 1


def test_top_level_helper_writes_to_the_workspace_ledger(tmp_path: Path) -> None:
    workspace, witness = build(tmp_path, {"x.py": "print(1)\n"}, "x.py")
    _ = run(workspace, f"{ROOT}/x.py")
    assert len(ledger_entries(witness)) == 1


def test_helper_path_with_spaces(tmp_path: Path) -> None:
    helper = "my scripts/x y.py"
    workspace, witness = build(tmp_path, {helper: "print(1)\n"}, helper)
    got = run(workspace, f"{ROOT}/{helper}")
    assert got.returncode == 0, got.stderr
    assert len(ledger_entries(witness)) == 1
    command = f"python3 -I '{ROOT}/{helper}'"
    assert witnessed_events([event(command)], ledger_entries(witness)) == [event(command)]


def test_symlinked_helper_is_not_staged(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path / "workspace")
    skill = workspace / ROOT
    (skill / "scripts").mkdir(parents=True)
    _ = (tmp_path / "t.py").write_text("print(1)\n")
    (skill / HELPER).symlink_to(tmp_path / "t.py")
    assert stage_witness(workspace, SKILL, HELPER) is None
    assert (tmp_path / "t.py").read_text() == "print(1)\n"


def test_a_fifo_ledger_does_not_block_and_earns_nothing(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / LEDGER)
    error = fault(Witness("n", tmp_path / LEDGER))
    assert error.cause == "ledger-unreadable" and error.entries == []


def test_a_symlinked_ledger_is_not_read(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.jsonl"
    _ = target.write_text(json.dumps({"nonce": "n", "argv": ["a"], "exit_code": 0}) + "\n")
    (tmp_path / LEDGER).symlink_to(target)
    error = fault(Witness("n", tmp_path / LEDGER))
    assert error.cause == "ledger-unreadable" and error.entries == []


def test_an_unwritable_ledger_keeps_the_helper_exit_code_and_earns_nothing(tmp_path: Path) -> None:
    workspace, witness = build(tmp_path, {HELPER: "import sys; sys.exit(3)\n"})
    (workspace / LEDGER).mkdir()
    got = run(workspace, INVOKED)
    assert got.returncode == 3 and got.stderr == ""
    assert fault(witness).cause == "ledger-unreadable"


def test_a_zipapp_helper_runs_with_its_exit_code_output_and_ledger_credit(tmp_path: Path) -> None:
    source = tmp_path / "app"
    source.mkdir()
    _ = (source / "__main__.py").write_text("import sys\nprint('zip', sys.argv[1:])\nsys.exit(4)\n")
    workspace = make_workspace(tmp_path / "workspace")
    (workspace / ROOT / "scripts").mkdir(parents=True)
    zipapp.create_archive(source, workspace / ROOT / "scripts/x.pyz")
    witness = stage_witness(workspace, SKILL, "scripts/x.pyz")
    assert witness is not None
    invoked = f"{ROOT}/scripts/x.pyz"
    got = run(workspace, invoked, "a")
    assert (got.returncode, got.stdout, got.stderr) == (4, "zip ['a']\n", "")
    assert ledger_entries(witness) == [{"argv": [invoked, "a"], "exit_code": 4}]
    trace = [event(f"python3 -I {invoked} a", 4)]
    assert witnessed_events(trace, ledger_entries(witness)) == trace


# 9. a codex task stages no witness

def test_a_codex_task_stages_no_stub_no_ledger_and_reports_no_witness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "host"
    host.mkdir()
    _ = (host / "auth.json").write_text(json.dumps({"tokens": {"id_token": "t"}}))
    monkeypatch.setenv("CODEX_HOME", str(host))
    item = {"id": "one", "family": "one", "split": "train", "kind": "echo", "request": "Echo.", "files": {},
            "expected": {"echo": "hello"}, "provenance": "test", "provider_approved": True}
    _ = (tmp_path / "cases.json").write_text(json.dumps({"schema_version": 1, "cases": [item]}))
    case = load_cases(tmp_path / "cases.json", rules().grader_types())[0]
    outputs: list[Path] = []
    seen: dict[str, object] = {}

    def command(*arguments: object) -> list[str]:
        outputs.append(cast(Path, arguments[-1]))
        return ["codex"]

    def discovered(*_arguments: object) -> bool:
        return True

    def fake_process(_command: list[str], *, cwd: Path, **_options: object) -> SimpleNamespace:
        helper = cwd / ROOT / HELPER
        seen.update(source=helper.read_text(), ledger=(cwd / LEDGER).exists(),
                    names=sorted(path.name for path in helper.parent.iterdir()))
        _ = outputs[-1].write_text(json.dumps({"load_marker": candidate().identity, "result_json": {"echo": "hello"}}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(Codex, "_execution_command", command)
    monkeypatch.setattr(_codex, "process", fake_process)
    monkeypatch.setattr(Codex, "_discover", discovered)
    adapter = Codex("m", Budget(5, 60, 0), lambda: None)
    try:
        invoked = adapter.invoke("p", candidate(), case)
        result = evaluate(adapter, adapter, candidate(), case)
    finally:
        adapter.close()
    assert seen["source"] == HELPER_SOURCE and seen["names"] == ["x.py"] and seen["ledger"] is False
    assert LEDGER_KEY not in invoked
    assert "helper_witnessed" not in result

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._contract import Grader
from skillz_experiments._intake import DraftCase
from skillz_experiments.proof import probe_case_proofs
from skillz_experiments.proof._execute import ProofExecutionError, score
from skillz_experiments.proof._fixtures import fixture_files, stage


def _case(proof: object, *, journey: bool = False) -> DraftCase:
    from skillz_experiments._cases import Journey
    return DraftCase("one", "f", "journey" if journey else "task", "do", "skill", {"input.txt": "real"}, None,
                     Journey("brief", 1, ()) if journey else None, proof=proof,
                     proof_origin=("author.json", "/cases/4/proof"))


def _fixture(root: Path, side: str, answer: str = "pass") -> str:
    reference = f"evals/proofs/{side}"
    output = root / reference / "output"
    output.mkdir(parents=True)
    _ = (output / "answer.txt").write_text(answer)
    return reference


def _grader(script: str) -> Grader:
    return Grader("command", argv=(sys.executable, "-I", "-c", script))


@pytest.mark.ac("gh-657:AC-9")
def test_supplied_side_runs_while_other_side_warns(tmp_path: Path) -> None:
    reference = _fixture(tmp_path, "pass")
    script = "import json; from pathlib import Path; print(json.dumps({'score': int(Path('output/answer.txt').read_text() == 'pass')}))"
    found = probe_case_proofs(tmp_path, [_case({"pass": reference})], {"task": _grader(script)}, {"one": "task"})
    assert [(item["code"], item["severity"], item["pointer"]) for item in found] == [
        ("case-proof-missing", "warning", "/cases/4/proof/fail")]


@pytest.mark.ac("gh-657:AC-25")
def test_invalid_unselected_metadata_does_not_inspect_a_fixture_or_run_a_command(tmp_path: Path) -> None:
    case = _case({"pass": "../absent"})
    found = probe_case_proofs(tmp_path, [case], {"task": _grader("raise AssertionError('ran')")}, {})
    assert len(found) == 1 and found[0]["code"] == "case-proof-invalid-reference"
    assert found[0]["pointer"] == "/cases/4/proof/pass"
    assert found[0]["file"] == "author.json"
    assert cast(dict[str, object], found[0]["data"])["selected_kind"] is None


@pytest.mark.ac("gh-657:AC-25")
def test_pending_metadata_uses_no_fixture_or_process_probe(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import skillz_experiments.proof._probe as probe
    pending = DraftCase("pending", "f", "trigger", "do", "skill", {}, None, None,
                        proof={"pass": "../bad"}, proof_origin=("author.json", "/cases/5/proof"))
    def forbidden(*_args: object) -> None:
        raise AssertionError("pending case must not probe")
    monkeypatch.setattr(probe, "fixture_files", forbidden)
    monkeypatch.setattr(probe, "score", forbidden)
    found = probe_case_proofs(tmp_path, [pending], {"task": _grader("raise AssertionError('ran')")}, {})
    assert len(found) == 1 and found[0]["code"] == "case-proof-invalid-reference"
    assert found[0]["pointer"] == "/cases/5/proof/pass"
    assert cast(dict[str, object], found[0]["data"])["selected_kind"] is None


@pytest.mark.ac("gh-657:AC-20")
@pytest.mark.parametrize("source", ["{'score': True}", "{'score': float('nan')}",
                                    "{'score': 2}", "[]", "'wrong'"])
def test_invalid_scores_never_certify_fail_proof(tmp_path: Path, source: str) -> None:
    (tmp_path / "home").mkdir()
    (tmp_path / "tmp").mkdir()
    script = f"import json; print(json.dumps({source}))"
    with pytest.raises(ProofExecutionError):
        _ = score(tmp_path, (sys.executable, "-I", "-c", script), os.defpath)



@pytest.mark.ac("gh-657:AC-12")
def test_fractional_pass_score_is_a_mismatch_not_an_invalid_invocation(tmp_path: Path) -> None:
    reference = _fixture(tmp_path, "pass")
    found = probe_case_proofs(tmp_path, [_case({"pass": reference})],
                              {"task": _grader("import json; print(json.dumps({'score': 0.5}))")}, {"one": "task"})
    mismatch = [item for item in found if item["code"] == "case-proof-score-mismatch"]
    assert len(mismatch) == 1 and cast(dict[str, object], mismatch[0]["data"])["expected"] == 1
    assert cast(dict[str, object], mismatch[0]["data"])["observed"] == 0.5

@pytest.mark.ac("gh-657:AC-16")
@pytest.mark.parametrize("script", ["print('no json')", "print('{\"score\": 0}'); raise SystemExit(3)",
                                          "print('x' * 65537)", "import time; time.sleep(1)"])
def test_invalid_invocations_are_execution_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    import skillz_experiments.proof._execute as execute
    (tmp_path / "home").mkdir()
    (tmp_path / "tmp").mkdir()
    if "sleep" in script:
        monkeypatch.setattr(execute, "TIME_LIMIT", 0.05)
    with pytest.raises(ProofExecutionError):
        _ = score(tmp_path, (sys.executable, "-I", "-c", script), os.defpath)


@pytest.mark.ac("gh-657:AC-21")
def test_selected_judge_without_argv_reports_unsupported(tmp_path: Path) -> None:
    reference = _fixture(tmp_path, "pass")
    found = probe_case_proofs(tmp_path, [_case({"pass": reference})], {"task": Grader("judge", rubric="Grade.")},
                              {"one": "task"})
    assert any(item["code"] == "case-proof-unsupported" and item["severity"] == "error" for item in found)


@pytest.mark.ac("gh-657:AC-17")
@pytest.mark.ac("gh-657:AC-22")
@pytest.mark.parametrize("bad", ["symlink", "extra", "binary", "oversize", "trace"])
def test_unsafe_fixture_tree_fails_before_probe(tmp_path: Path, bad: str) -> None:
    reference = _fixture(tmp_path, "pass")
    root = tmp_path / reference
    if bad == "symlink":
        (root / "output/link").symlink_to("answer.txt")
    elif bad == "extra":
        _ = (root / "extra").write_text("x")
    elif bad == "binary":
        _ = (root / "output/answer.txt").write_bytes(b"\xff")
    elif bad == "oversize":
        _ = (root / "output/answer.txt").write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    else:
        (root / "trace").mkdir()
    with pytest.raises(ValueError):
        _ = fixture_files(tmp_path, reference, False)


@pytest.mark.ac("gh-657:AC-22")
@pytest.mark.parametrize("unreadable", ["root", "output"])
def test_unreadable_fixture_directory_is_invalid_before_grader_runs(tmp_path: Path, unreadable: str) -> None:
    reference = _fixture(tmp_path, "pass")
    directory = tmp_path / reference
    if unreadable == "output":
        directory /= "output"
    marker = tmp_path / "grader-ran"
    script = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran'); print('{{\\\"score\\\":1}}')"
    directory.chmod(0)
    try:
        found = probe_case_proofs(tmp_path, [_case({"pass": reference})],
                                  {"task": _grader(script)}, {"one": "task"})
    finally:
        directory.chmod(0o700)
    assert [(item["code"], item["pointer"]) for item in found] == [
        ("case-proof-invalid-fixture", "/cases/4/proof/pass"),
        ("case-proof-missing", "/cases/4/proof/fail")]
    assert not marker.exists()

@pytest.mark.ac("gh-657:AC-18")
def test_journey_trace_replaces_a_case_trace_but_ordinary_keeps_it(tmp_path: Path) -> None:
    reference = _fixture(tmp_path, "pass")
    with pytest.raises(ValueError, match="requires trace"):
        _ = fixture_files(tmp_path, reference, True)
    trace = tmp_path / reference / "trace"
    trace.mkdir()
    _ = (trace / "events.jsonl").write_text("")
    proof = fixture_files(tmp_path, reference, True)
    assert proof["trace/events.jsonl"] == b""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stage(workspace, {"trace/events.jsonl": "case trace"}, proof, True)
    assert (workspace / "trace/events.jsonl").read_bytes() == b""
    with pytest.raises(ValueError):
        _ = fixture_files(tmp_path, reference, False)


@pytest.mark.ac("gh-657:AC-14")
@pytest.mark.ac("gh-657:AC-19")
def test_path_lookup_uses_a_clean_environment_and_keeps_argv_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "proof-tool"
    _ = executable.write_text("#!/usr/bin/env python3\nimport json, os, sys\n" +
                              "print(json.dumps({'score': int(sys.argv[1] == 'a b' and 'SECRET' not in os.environ)}))\n")
    executable.chmod(0o755)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "home").mkdir()
    (workspace / "tmp").mkdir()
    monkeypatch.setenv("SECRET", "hidden")
    assert score(workspace, ("proof-tool", "a b"), f"{bin_dir}:{os.defpath}") == 1
    assert score(workspace, (str(executable), "a b"), os.defpath) == 1

@pytest.mark.parametrize("path,word", [("", "tool"), ("bin", "tool"), (os.defpath, "./bin/tool")])
@pytest.mark.ac("gh-657:AC-19")
def test_empty_relative_and_explicit_workspace_paths(tmp_path: Path, path: str, word: str) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "bin").mkdir(parents=True)
    (workspace / "home").mkdir()
    (workspace / "tmp").mkdir()
    tool = workspace / ("tool" if path == "" else "bin/tool")
    _ = tool.write_text("#!/bin/sh\nprintf '{\"score\":1}\\n'\n")
    tool.chmod(0o755)
    assert score(workspace, (word,), path) == 1



@pytest.mark.ac("gh-657:AC-17")
def test_proof_cannot_replace_actual_case_input(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match="cannot replace"):
        stage(workspace, {"output/answer.txt": "real"}, {"output/answer.txt": b"fake"}, False)
    assert (workspace / "output/answer.txt").read_text() == "real"


@pytest.mark.ac("gh-657:AC-13")
def test_hybrid_proof_runs_only_command_gate(tmp_path: Path) -> None:
    reference = _fixture(tmp_path, "pass")
    grader = Grader("hybrid", argv=(sys.executable, "-I", "-c", "print('{\"score\":1}')"), rubric="Never call this judge.")
    found = probe_case_proofs(tmp_path, [_case({"pass": reference})], {"task": grader}, {"one": "task"})
    assert [item["code"] for item in found] == ["case-proof-missing"]
    assert all("verdict" not in item and "judge" not in item for item in found)


@pytest.mark.parametrize("digits", [400, 5000])
@pytest.mark.ac("gh-657:AC-20")
def test_huge_json_integer_is_an_execution_finding(tmp_path: Path, digits: int) -> None:
    (tmp_path / "home").mkdir()
    (tmp_path / "tmp").mkdir()
    script = f"print('{{\"score\":' + '9' * {digits} + '}}')"
    with pytest.raises(ProofExecutionError, match="score|JSON"):
        _ = score(tmp_path, (sys.executable, "-I", "-c", script), os.defpath)


@pytest.mark.ac("gh-657:AC-20")
def test_deep_grader_json_reports_execution_and_keeps_later_diagnostics(tmp_path: Path) -> None:
    reference = _fixture(tmp_path, "pass")
    first = _case({"pass": reference})
    later = DraftCase("two", "f", "task", "do", "skill", {}, None, None,
                      proof={"pass": "../bad"}, proof_origin=("later.json", "/cases/5/proof"))
    script = "print('{\\\"score\\\":' + '[' * 1200 + '0' + ']' * 1200 + '}')"
    found = probe_case_proofs(tmp_path, [first, later], {"task": _grader(script)}, {"one": "task"})
    assert [(item["code"], item["pointer"]) for item in found] == [
        ("case-proof-invalid-reference", "/cases/5/proof/pass"),
        ("case-proof-execution", "/cases/4/proof/pass"),
        ("case-proof-missing", "/cases/4/proof/fail")]

@pytest.mark.ac("gh-657:AC-23")
def test_metadata_precedes_every_probe_and_keeps_side_and_reference(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import skillz_experiments.proof._probe as probe
    first = _case({"pass": "evals/proofs/missing"})
    second = DraftCase("two", "f", "task", "do", "skill", {}, None, None,
                       proof={"pass": "../bad"}, proof_origin=("author.json", "/cases/5/proof"))
    checked: list[object] = []
    from skillz_experiments._records import proof_problem
    real_problem = proof_problem
    def record_problem(value: object) -> tuple[str, str] | None:
        checked.append(value)
        return real_problem(value)
    def guarded_fixture_files(_target: Path, _reference: str, _journey: bool) -> dict[str, bytes]:
        assert checked == [first.proof, second.proof]
        raise ValueError("missing fixture")
    monkeypatch.setattr(probe, "proof_problem", record_problem)
    monkeypatch.setattr(probe, "fixture_files", guarded_fixture_files)
    found = probe_case_proofs(tmp_path, [first, second], {"task": _grader("raise AssertionError('ran')")},
                              {"one": "task"})
    invalid = [item for item in found if item["code"] == "case-proof-invalid-reference"]
    assert len(invalid) == 1 and invalid[0]["pointer"] == "/cases/5/proof/pass"
    assert cast(dict[str, object], invalid[0]["data"]) == {
        "case_id": "two", "authored_kind": "task", "selected_kind": None,
        "side": "pass", "reference": "../bad"}


@pytest.mark.ac("gh-657:AC-8")
def test_empty_proof_output_is_allowed_and_grader_observes_missing_file(tmp_path: Path) -> None:
    reference = "evals/proofs/empty"
    root = tmp_path / reference
    root.mkdir(parents=True)
    assert fixture_files(tmp_path, reference, False) == {}
    (root / "output").mkdir()
    assert fixture_files(tmp_path, reference, False) == {}
    script = "from pathlib import Path; print('{\"score\":' + str(int(Path('output/answer.txt').exists())) + '}')"
    found = probe_case_proofs(tmp_path, [_case({"pass": reference})], {"task": _grader(script)}, {"one": "task"})
    assert [item["code"] for item in found] == ["case-proof-score-mismatch", "case-proof-missing"]


@pytest.mark.ac("gh-657:AC-22")
def test_proof_count_and_aggregate_budget_include_trace_not_case_files(tmp_path: Path) -> None:
    reference = "evals/proofs/bounded"
    root = tmp_path / reference
    (root / "output").mkdir(parents=True)
    (root / "trace").mkdir()
    for index in range(199):
        _ = (root / "output" / f"{index:03}.txt").write_text("")
    _ = (root / "trace/events.jsonl").write_text("")
    assert len(fixture_files(tmp_path, reference, True)) == 200
    _ = (root / "output/199.txt").write_text("")
    with pytest.raises(ValueError, match="200 files"):
        _ = fixture_files(tmp_path, reference, True)
    (root / "output/199.txt").unlink()
    _ = (root / "output/000.txt").write_bytes(b"x" * (4 * 1024 * 1024))
    assert len(fixture_files(tmp_path, reference, True)["output/000.txt"]) == 4 * 1024 * 1024
    _ = (root / "trace/events.jsonl").write_text("x")
    with pytest.raises(ValueError, match="too large"):
        _ = fixture_files(tmp_path, reference, True)


@pytest.mark.ac("gh-657:AC-15")
@pytest.mark.parametrize("outcome", ["success", "nonzero", "invalid-json", "stdout-overflow", "timeout"])
def test_every_process_outcome_cleans_descendants_and_workspace(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    import tempfile
    import time
    import skillz_experiments.proof._execute as execute
    reference = _fixture(tmp_path, "pass")
    marker = tmp_path / "late-write"
    child = f"import pathlib,time; time.sleep(.5); pathlib.Path({str(marker)!r}).write_text('bad')"
    script = ("import subprocess,sys,time\n"
              f"subprocess.Popen([sys.executable, '-I', '-c', {child!r}], "
              "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n")
    script += {
        "success": "print('{\\\"score\\\":1}')\n",
        "nonzero": "raise SystemExit(3)\n",
        "invalid-json": "print('broken')\n",
        "stdout-overflow": "print('x' * 65537)\n",
        "timeout": "time.sleep(2)\n",
    }[outcome]
    if outcome == "timeout":
        monkeypatch.setattr(execute, "TIME_LIMIT", 0.05)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    found = probe_case_proofs(tmp_path, [_case({"pass": reference})], {"task": _grader(script)}, {"one": "task"})
    expected = ["case-proof-missing"] if outcome == "success" else ["case-proof-execution", "case-proof-missing"]
    assert [item["code"] for item in found] == expected
    time.sleep(.6)
    assert not marker.exists()
    assert not list(tmp_path.glob("skillz-proof-*"))


@pytest.mark.parametrize("unsafe", ["ancestor-link", "directory-link", "hidden", "fifo"])
@pytest.mark.ac("gh-657:AC-22")
def test_unsafe_fixture_entries_are_never_followed(tmp_path: Path, unsafe: str) -> None:
    reference = "evals/proofs/unsafe"
    root = tmp_path / reference
    output = root / "output"
    output.mkdir(parents=True)
    _ = (output / "answer.txt").write_text("pass")
    if unsafe == "ancestor-link":
        alternate = tmp_path / "evals/alternate"
        _ = root.rename(alternate)
        _ = root.symlink_to(alternate, target_is_directory=True)
    elif unsafe == "directory-link":
        _ = (output / "linked").symlink_to(tmp_path, target_is_directory=True)
    elif unsafe == "hidden":
        _ = (output / ".secret").write_text("secret")
    else:
        os.mkfifo(output / "pipe")
    with pytest.raises((OSError, ValueError)):
        _ = fixture_files(tmp_path, reference, False)


@pytest.mark.ac("gh-657:AC-16")
def test_proof_process_has_clean_environment_and_file_size_limit(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "home").mkdir()
    (workspace / "tmp").mkdir()
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "hidden")
    monkeypatch.setenv("PYTHONPATH", "hidden")
    script = ("import json,os,resource,signal\n"
              "signal.signal(signal.SIGXFSZ, signal.SIG_IGN)\n"
              "env = os.environ\n"
              "clean = set(env) == {'PATH','HOME','TMPDIR','LC_ALL'} and env['LC_ALL'] == 'C.UTF-8'\n"
              "clean &= env['HOME'] == os.path.join(os.getcwd(),'home')\n"
              "clean &= env['TMPDIR'] == os.path.join(os.getcwd(),'tmp')\n"
              "limit = resource.getrlimit(resource.RLIMIT_FSIZE)\n"
              "clean &= limit == (4194304,4194304)\n"
              "try:\n"
              "  with open('too-large', 'wb') as stream: stream.write(b'x' * 4194305)\n"
              "except OSError: pass\n"
              "else: clean = False\n"
              "print(json.dumps({'score': int(clean)}))\n")
    assert score(workspace, (sys.executable, "-I", "-c", script), os.defpath) == 1


@pytest.mark.ac("gh-657:AC-19")
def test_absent_path_uses_one_system_snapshot_for_both_sides(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import skillz_experiments.proof._probe as probe
    passed = _fixture(tmp_path, "pass", "pass")
    failed = _fixture(tmp_path, "fail", "fail")
    script = ("import json; from pathlib import Path; "
              "print(json.dumps({'score': int(Path('output/answer.txt').read_text() == 'pass')}))")
    actual_score = score
    paths: list[str] = []
    def record_score(workspace: Path, argv: tuple[str, ...], path: str) -> float:
        paths.append(path)
        if len(paths) == 1:
            monkeypatch.setenv("PATH", "/changed-after-first-probe")
        return actual_score(workspace, argv, path)
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.setattr(probe, "score", record_score)
    found = probe_case_proofs(tmp_path, [_case({"pass": passed, "fail": failed})],
                              {"task": _grader(script)}, {"one": "task"})
    assert found == []
    assert paths == [os.defpath, os.defpath]


@pytest.mark.ac("gh-657:AC-20")
def test_missing_executable_is_an_execution_error(tmp_path: Path) -> None:
    (tmp_path / "home").mkdir()
    (tmp_path / "tmp").mkdir()
    with pytest.raises(ProofExecutionError, match="not found"):
        _ = score(tmp_path, ("missing-proof-tool",), "")


@pytest.mark.ac("gh-657:AC-12")
def test_fractional_fail_score_is_a_mismatch_not_a_valid_fail(tmp_path: Path) -> None:
    reference = _fixture(tmp_path, "fail", "fail")
    found = probe_case_proofs(tmp_path, [_case({"fail": reference})],
                              {"task": _grader("print('{\\\"score\\\":0.5}')")}, {"one": "task"})
    mismatch = [item for item in found if item["code"] == "case-proof-score-mismatch"]
    assert len(mismatch) == 1
    assert cast(dict[str, object], mismatch[0]["data"])["expected"] == 0
    assert cast(dict[str, object], mismatch[0]["data"])["observed"] == 0.5


@pytest.mark.ac("gh-657:AC-18")
def test_ordinary_stage_keeps_case_trace_and_never_invents_one(tmp_path: Path) -> None:
    reference = _fixture(tmp_path, "ordinary")
    proof = fixture_files(tmp_path, reference, False)
    with_trace = tmp_path / "with-trace"
    with_trace.mkdir()
    stage(with_trace, {"trace/events.jsonl": "actual case trace"}, proof, False)
    assert (with_trace / "trace/events.jsonl").read_text() == "actual case trace"
    without_trace = tmp_path / "without-trace"
    without_trace.mkdir()
    stage(without_trace, {"input.txt": "real"}, proof, False)
    assert not (without_trace / "trace").exists()


@pytest.mark.ac("gh-657:AC-17")
def test_large_existing_case_input_does_not_consume_proof_payload_budget(tmp_path: Path) -> None:
    reference = _fixture(tmp_path, "pass")
    proof = fixture_files(tmp_path, reference, False)
    workspace = tmp_path / "large-case"
    workspace.mkdir()
    stage(workspace, {"input.txt": "x" * (4 * 1024 * 1024 + 1)}, proof, False)
    assert (workspace / "input.txt").stat().st_size > 4 * 1024 * 1024
    assert (workspace / "output/answer.txt").read_text() == "pass"

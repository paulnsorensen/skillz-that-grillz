"""Press attacks on the contract trust boundary, capture, and wedge admission (AC-1 to AC-6, AC-13)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cli import main
from skillz_experiments._contract import load_contract, parse

ECHO = Path(__file__).parent / "fixtures/echo-skill"

BASE: dict[str, object] = {
    "schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
    "kinds": {"echo": {"grader": "exact-json"}}, "editable": []}


def echo_skill(tmp_path: Path) -> Path:
    target = tmp_path / "echo-skill"
    _ = shutil.copytree(ECHO, target)
    _ = (target / "SKILL.md.fixture").rename(target / "SKILL.md")
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(BASE))
    return target


def run_intake(tmp_path: Path, target: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    """Run `run` through both intake stops. Return the exit code and the parsed stderr JSON."""
    out = tmp_path / "run"
    out.mkdir(mode=0o700)
    cases = [{"id": f"c{family}-{index}", "family": f"f{family}", "kind": "task", "request": "Echo hello",
              "files": {"input.txt": "hello\n"}, "expected": {"echo": "hello"}, "source": "skill"}
             for family in range(5) for index in range(2)]
    _ = (out / "cases.draft.json").write_text(json.dumps(cases))
    command = ["run", "--target", str(target), "--out", str(out), "--model", "offline"]
    code = main(command)
    captured = capsys.readouterr()
    error = cast(dict[str, object], json.loads(captured.err)) if captured.err.strip() else {}
    if error.get("code") == "cases-unapproved":
        approval = cast(str, json.loads(captured.out)["case_hash"])
        code = main([*command, "--approve-cases", approval])
        captured = capsys.readouterr()
        error = cast(dict[str, object], json.loads(captured.err)) if captured.err.strip() else {}
    return code, error


def broken(**changes: object) -> dict[str, object]:
    return BASE | changes


HOSTILE: dict[str, object] = {
    "status-missing": {k: v for k, v in BASE.items() if k != "status"},
    "status-number": broken(status=5),
    "status-unknown": broken(status="approvedd"),
    "version-bool": broken(schema_version=True),
    "version-text": broken(schema_version="1"),
    "version-two": broken(schema_version=2),
    "skill-upper": broken(skill="Echo"),
    "skill-traversal": broken(skill="../etc"),
    "skill-newline": broken(skill="echo-skill\n"),
    "skill-slash": broken(skill="a/b"),
    "skill-long": broken(skill="a" * 65),
    "skill-number": broken(skill=7),
    "skill-unicode-lookalike": broken(skill="еcho-skill"),
    "invocation-number": broken(invocation=5),
    "invocation-blank": broken(invocation="   "),
    "kinds-list": broken(kinds=[]),
    "kinds-empty": broken(kinds={}),
    "kinds-null": broken(kinds=None),
    "kind-text": broken(kinds={"echo": "exact-json"}),
    "grader-unknown": broken(kinds={"echo": {"grader": "shell"}}),
    "grader-list": broken(kinds={"echo": {"grader": ["exact-json"]}}),
    "grader-missing": broken(kinds={"echo": {}}),
    "command-no-argv": broken(kinds={"echo": {"grader": "command"}}),
    "command-argv-text": broken(kinds={"echo": {"grader": "command", "argv": "rm -rf /"}}),
    "command-argv-empty": broken(kinds={"echo": {"grader": "command", "argv": []}}),
    "command-argv-number": broken(kinds={"echo": {"grader": "command", "argv": ["x", 1]}}),
    "command-argv-blank": broken(kinds={"echo": {"grader": "command", "argv": [""]}}),
    "command-argv-null": broken(kinds={"echo": {"grader": "command", "argv": [None]}}),
    "command-argv-object": broken(kinds={"echo": {"grader": "command", "argv": {"0": "x"}}}),
    "exact-with-argv": broken(kinds={"echo": {"grader": "exact-json", "argv": ["x"]}}),
    "command-with-rubric": broken(kinds={"echo": {"grader": "command", "argv": ["x"], "rubric": "r"}}),
    "judge-no-rubric": broken(kinds={"echo": {"grader": "judge"}}),
    "judge-rubric-blank": broken(kinds={"echo": {"grader": "judge", "rubric": " "}}),
    "judge-with-argv": broken(kinds={"echo": {"grader": "judge", "rubric": "r", "argv": ["x"]}}),
    "hybrid-no-rubric": broken(kinds={"echo": {"grader": "hybrid", "argv": ["x"]}}),
    "hybrid-no-argv": broken(kinds={"echo": {"grader": "hybrid", "rubric": "r"}}),
    "audit-with-rubric": broken(kinds={"echo": {"grader": "audit", "rubric": "r"}}),
    "kind-extra-field": broken(kinds={"echo": {"grader": "exact-json", "expected": {}}}),
    "unknown-field": broken(admin=True),
    "helper-text": broken(helper="scripts/x.py"),
    "helper-no-path": broken(helper={}),
    "helper-traversal": broken(helper={"path": "../../etc/passwd"}),
    "helper-nested-traversal": broken(helper={"path": "scripts/../../x.py"}),
    "helper-absolute": broken(helper={"path": "/etc/passwd"}),
    "helper-hidden": broken(helper={"path": ".git/config"}),
    "helper-backslash": broken(helper={"path": "scripts\\x.py"}),
    "helper-nul": broken(helper={"path": "scripts/x\u0000.py"}),
    "helper-dot": broken(helper={"path": "scripts/./x.py"}),
    "helper-input-traversal": broken(helper={"path": "scripts/x.py", "input": "../secret"}),
    "helper-fixtures-text": broken(helper={"path": "scripts/x.py", "fixtures": "x"}),
    "helper-fixture-bool-code": broken(helper={"path": "scripts/x.py", "fixtures": [
        {"input": "a", "returncode": True, "output": cast(dict[str, object], {})}]}),
    "helper-fixture-missing-key": broken(helper={"path": "scripts/x.py", "fixtures": [{"input": "a"}]}),
    "helper-extra": broken(helper={"path": "scripts/x.py", "shell": True}),
    "editable-text": broken(editable="SKILL.md"),
    "editable-traversal": broken(editable=["../SKILL.md"]),
    "editable-absolute": broken(editable=["/etc/passwd"]),
    "editable-hidden": broken(editable=[".env"]),
    "editable-number": broken(editable=[1]),
    "editable-null": broken(editable=[None]),
}


def assert_structured_failure(code: int, error: dict[str, object]) -> None:
    assert code == 1
    assert isinstance(error.get("error"), str) and error["error"]
    assert error.get("exit_code") == 1


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_run_with_hostile_skill_contract_exits_with_a_structured_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    target = echo_skill(tmp_path)
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(HOSTILE[name]))
    code, error = run_intake(tmp_path, target, capsys)
    assert_structured_failure(code, error)
    assert not (tmp_path / "run/run.json").exists()


@pytest.mark.parametrize("raw", [b"", b"{", b"null", b"[]", b"\"x\"", b"42", b"\xff\xfe\x00bad", b"{\"a\":1,}"])
def test_run_with_unreadable_contract_bytes_exits_with_a_structured_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes) -> None:
    target = echo_skill(tmp_path)
    _ = (target / "evals/autoimprove.json").write_bytes(raw)
    code, error = run_intake(tmp_path, target, capsys)
    assert_structured_failure(code, error)


def test_run_with_an_oversized_contract_file_exits_before_parsing(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(BASE | {"pad": "x" * 1_100_000}))
    code, error = run_intake(tmp_path, target, capsys)
    assert_structured_failure(code, error)
    assert "bounded" in cast(str, error["error"])


def test_run_with_a_deeply_nested_contract_exits_with_a_structured_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    depth = 400_000
    _ = (target / "evals/autoimprove.json").write_text("[" * depth + "]" * depth)
    code, error = run_intake(tmp_path, target, capsys)
    assert_structured_failure(code, error)


def test_run_with_a_symlinked_contract_exits_with_a_structured_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    outside = tmp_path / "outside.json"
    _ = outside.write_text(json.dumps(BASE))
    (target / "evals/autoimprove.json").unlink()
    (target / "evals/autoimprove.json").symlink_to(outside)
    code, error = run_intake(tmp_path, target, capsys)
    assert_structured_failure(code, error)


def test_run_with_a_dangling_contract_symlink_stops_with_contract_unreadable(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    (target / "evals/autoimprove.json").unlink()
    (target / "evals/autoimprove.json").symlink_to(tmp_path / "nowhere.json")
    code, error = run_intake(tmp_path, target, capsys)
    assert code == 1 and error["code"] == "contract-unreadable"
    assert not (tmp_path / "run/run.json").exists()


def test_run_with_a_directory_in_place_of_the_contract_stops_with_contract_unreadable(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    (target / "evals/autoimprove.json").unlink()
    (target / "evals/autoimprove.json").mkdir()
    code, error = run_intake(tmp_path, target, capsys)
    assert code == 1 and error["code"] == "contract-unreadable"
    assert not (tmp_path / "run/run.json").exists()


@pytest.mark.parametrize("draft", [
    {"status": "draft"},
    {"status": "draft", "skill": 5, "kinds": "junk"},
    {"status": "draft", "kinds": {"x": {"grader": "shell"}}},
])
def test_a_draft_contract_reports_unapproved_before_any_schema_check(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], draft: dict[str, object]) -> None:
    target = echo_skill(tmp_path)
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(draft))
    code, error = run_intake(tmp_path, target, capsys)
    assert code == 1 and error["code"] == "contract-unapproved"


# ---- invocation injection (AC-1) ------------------------------------------------------------------

def test_parse_keeps_placeholder_text_in_invocation_verbatim() -> None:
    rules = parse(BASE | {"invocation": "{skill}{path}{{skill}}{unknown}"}, "skill")
    assert rules.invocation == "{skill}{path}{{skill}}{unknown}"


def test_evaluator_prompt_expands_placeholders_once_and_never_in_the_request() -> None:
    from skillz_experiments._cases import Case
    from skillz_experiments._evaluator import _prompt  # pyright: ignore[reportPrivateUsage]
    rules = parse(BASE | {"invocation": "{skill} at {path} {skill}"}, "skill")
    candidate = Candidate({"SKILL.md": "seed"}, ("SKILL.md",), contract=rules)
    case = Case("one", "one", "train", "Use {skill} and {path}.", {}, {"echo": 1}, "t", True)
    prompt = _prompt(rules, candidate, "Return JSON.", case)
    first = prompt.splitlines()[0]
    assert first == "echo-skill at .agents/skills/echo-skill echo-skill"
    assert prompt.endswith("Use {skill} and {path}.")


# ---- capture (AC-6) -------------------------------------------------------------------------------

def git_repo(root: Path, ignore: str) -> None:
    for command in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
        _ = subprocess.run(command, cwd=root, check=True, capture_output=True)
    _ = (root / ".gitignore").write_text(ignore)


def test_capture_skips_nested_ignored_directories_with_undecodable_files(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    git_repo(target, "cache/\nbuild/\n")
    deep = target / "cache/a/b/c"
    deep.mkdir(parents=True)
    _ = (deep / "blob.bin").write_bytes(b"\xff\xfe\x00\x80")
    (target / "build").mkdir()
    _ = (target / "build/out.bin").write_bytes(b"\xc3\x28")
    candidate = Candidate.capture(target, ["SKILL.md"])
    assert set(candidate.files) == {"SKILL.md"}


def test_capture_skips_an_ignored_directory_that_holds_a_symlink_loop(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    git_repo(target, "cache/\n")
    (target / "cache").mkdir()
    (target / "cache/loop").symlink_to(target / "cache")
    (target / "cache/self").symlink_to("self")
    assert set(Candidate.capture(target, ["SKILL.md"]).files) == {"SKILL.md"}


def test_capture_rejects_a_live_symlink_loop_without_hanging(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    (target / "loop").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        _ = Candidate.capture(target, ["SKILL.md"])


def test_capture_rejects_a_dangling_symlink_with_a_value_error(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    (target / "gone").symlink_to(tmp_path / "nowhere")
    with pytest.raises(ValueError, match="symlink"):
        _ = Candidate.capture(target, ["SKILL.md"])


def test_run_with_a_non_utf8_file_reports_a_coded_error_not_a_traceback(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    (target / "references").mkdir()
    _ = (target / "references/data.txt").write_bytes(b"\xff\xfe\x00")
    code, error = run_intake(tmp_path, target, capsys)
    assert code == 1 and error["code"] == "undecodable-file"


def test_run_with_a_non_utf8_file_outside_git_still_reports_a_coded_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    target = echo_skill(tmp_path)
    _ = (target / "notes.dat").write_bytes(b"\x80abc")
    harness_only = tmp_path / "empty-path"
    harness_only.mkdir()
    for name in ("claude", "codex"):
        _ = (harness_only / name).write_text("#!/bin/sh\nexit 0\n")
        (harness_only / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(harness_only))
    code, error = run_intake(tmp_path, target, capsys)
    assert code == 1 and error["code"] == "undecodable-file"


@pytest.mark.parametrize("name", [".env", ".secret/key.txt", "a/.hidden"])
def test_run_with_a_hidden_file_reports_a_coded_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    target = echo_skill(tmp_path)
    (target / name).parent.mkdir(parents=True, exist_ok=True)
    _ = (target / name).write_text("x")
    code, error = run_intake(tmp_path, target, capsys)
    assert code == 1 and error["code"] == "hidden-file"


def test_capture_ignores_the_contract_and_evals_so_the_rubric_never_reaches_a_candidate(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    rubric = parse(BASE | {"kinds": {"echo": {"grader": "judge", "rubric": "RUBRIC_SENTINEL"}}}, "skill")
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(rubric.data()))
    (target / "evals/nested").mkdir()
    _ = (target / "evals/nested/extra.json").write_bytes(b"\xff")
    candidate = Candidate.capture(target, ["SKILL.md"], load_contract(target))
    assert "RUBRIC_SENTINEL" not in json.dumps(candidate.files)
    assert not any(name.startswith("evals") for name in candidate.files)


def test_capture_with_the_skill_root_itself_a_symlink_is_rejected(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        _ = Candidate.capture(link, ["SKILL.md"])


def test_capture_with_an_oversized_file_is_a_value_error(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    _ = (target / "big.md").write_text("x" * 262145)
    with pytest.raises(ValueError, match="size"):
        _ = Candidate.capture(target, ["SKILL.md"])


def test_capture_with_a_file_that_is_a_fifo_does_not_hang(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    os.mkfifo(target / "pipe")
    assert set(Candidate.capture(target, ["SKILL.md"]).files) == {"SKILL.md"}

"""Press attacks on the contract trust boundary, capture, and wedge admission (AC-1 to AC-6, AC-13)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments import _wedge
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
    return target


def manifest(tmp_path: Path, extra: dict[str, object] | None = None) -> Path:
    cases = [{"id": split, "family": split, "split": split, "kind": "echo", "request": "Echo hello",
              "files": {"input.txt": "hello\n"}, "expected": {"echo": "hello"},
              "provenance": "test", "provider_approved": True} for split in ("train", "validation", "holdout")]
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": cases} | (extra or {})))
    return path


def run_dataset(tmp_path: Path, target: Path, cases: Path,
                capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    """Run `dataset` and return the exit code and the parsed stderr JSON. Any traceback fails the parse."""
    code = main(["dataset", str(cases), "--target", str(target), "--out", str(tmp_path / "run")])
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
def test_dataset_with_hostile_skill_contract_exits_with_a_structured_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    target = echo_skill(tmp_path)
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(HOSTILE[name]))
    code, error = run_dataset(tmp_path, target, manifest(tmp_path), capsys)
    assert_structured_failure(code, error)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_dataset_with_hostile_manifest_target_exits_with_a_structured_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    target = echo_skill(tmp_path)
    code, error = run_dataset(tmp_path, target, manifest(tmp_path, {"target": HOSTILE[name]}), capsys)
    assert_structured_failure(code, error)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("raw", [b"", b"{", b"null", b"[]", b"\"x\"", b"42", b"\xff\xfe\x00bad", b"{\"a\":1,}"])
def test_dataset_with_unreadable_contract_bytes_exits_with_a_structured_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes) -> None:
    target = echo_skill(tmp_path)
    _ = (target / "evals/autoimprove.json").write_bytes(raw)
    code, error = run_dataset(tmp_path, target, manifest(tmp_path), capsys)
    assert_structured_failure(code, error)


def test_dataset_with_an_oversized_contract_file_exits_before_parsing(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(BASE | {"pad": "x" * 1_100_000}))
    code, error = run_dataset(tmp_path, target, manifest(tmp_path), capsys)
    assert_structured_failure(code, error)
    assert "bounded" in cast(str, error["error"])


def test_dataset_with_a_deeply_nested_contract_exits_with_a_structured_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    depth = 400_000
    _ = (target / "evals/autoimprove.json").write_text("[" * depth + "]" * depth)
    code, error = run_dataset(tmp_path, target, manifest(tmp_path), capsys)
    assert_structured_failure(code, error)


def test_dataset_with_a_symlinked_contract_exits_with_a_structured_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    outside = tmp_path / "outside.json"
    _ = outside.write_text(json.dumps(BASE))
    (target / "evals/autoimprove.json").unlink()
    (target / "evals/autoimprove.json").symlink_to(outside)
    code, error = run_dataset(tmp_path, target, manifest(tmp_path), capsys)
    assert_structured_failure(code, error)


def test_dataset_with_a_dangling_contract_symlink_reports_contract_missing(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    (target / "evals/autoimprove.json").unlink()
    (target / "evals/autoimprove.json").symlink_to(tmp_path / "nowhere.json")
    code, error = run_dataset(tmp_path, target, manifest(tmp_path), capsys)
    assert code == 1 and error["code"] == "contract-missing"


def test_dataset_with_a_directory_in_place_of_the_contract_reports_contract_missing(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    (target / "evals/autoimprove.json").unlink()
    (target / "evals/autoimprove.json").mkdir()
    code, error = run_dataset(tmp_path, target, manifest(tmp_path), capsys)
    assert code == 1 and error["code"] == "contract-missing"
    assert "evals/autoimprove.json" in cast(str, error["error"]) and "target" in cast(str, error["error"])


@pytest.mark.parametrize("status", ["draft"])
def test_dataset_with_a_draft_manifest_target_overriding_an_approved_file_is_unapproved(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], status: str) -> None:
    target = echo_skill(tmp_path)
    code, error = run_dataset(tmp_path, target, manifest(tmp_path, {"target": broken(status=status)}), capsys)
    assert code == 1 and error["code"] == "contract-unapproved"
    assert not (tmp_path / "run").exists()


def test_dataset_with_a_draft_skill_contract_and_an_approved_manifest_target_uses_the_manifest(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(BASE | {"status": "draft"}))
    code, error = run_dataset(tmp_path, target, manifest(tmp_path, {"target": BASE}), capsys)
    assert code == 0 and not error


@pytest.mark.parametrize("draft", [
    {"status": "draft"},
    {"status": "draft", "skill": 5, "kinds": "junk"},
    {"status": "draft", "kinds": {"x": {"grader": "shell"}}},
])
def test_a_draft_contract_reports_unapproved_before_any_schema_check(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], draft: dict[str, object]) -> None:
    target = echo_skill(tmp_path)
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(draft))
    code, error = run_dataset(tmp_path, target, manifest(tmp_path), capsys)
    assert code == 1 and error["code"] == "contract-unapproved"


@pytest.mark.parametrize("target_value", [None, [], "x", 5, True])
def test_dataset_with_a_non_object_manifest_target_exits_with_a_structured_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], target_value: object) -> None:
    target = echo_skill(tmp_path)
    code, error = run_dataset(tmp_path, target, manifest(tmp_path, {"target": target_value}), capsys)
    assert_structured_failure(code, error)


def test_dataset_with_an_undeclared_case_kind_is_rejected_before_the_run_directory_exists(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    cases = manifest(tmp_path)
    document = cast(dict[str, list[dict[str, object]]], json.loads(cases.read_text()))
    document["cases"][0]["kind"] = "__proto__"
    _ = cases.write_text(json.dumps(document))
    code, error = run_dataset(tmp_path, target, cases, capsys)
    assert_structured_failure(code, error)
    assert "not declared" in cast(str, error["error"])
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("kind", [None, 5, ["echo"], {"echo": 1}, "Echo", "echo ", ""])
def test_dataset_with_a_malformed_case_kind_is_rejected(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: object) -> None:
    target = echo_skill(tmp_path)
    cases = manifest(tmp_path)
    document = cast(dict[str, list[dict[str, object]]], json.loads(cases.read_text()))
    document["cases"][0]["kind"] = kind
    _ = cases.write_text(json.dumps(document))
    code, error = run_dataset(tmp_path, target, cases, capsys)
    assert_structured_failure(code, error)


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


def test_dataset_with_a_non_utf8_file_reports_a_coded_error_not_a_traceback(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    (target / "references").mkdir()
    _ = (target / "references/data.txt").write_bytes(b"\xff\xfe\x00")
    code, error = run_dataset(tmp_path, target, manifest(tmp_path), capsys)
    assert code == 1 and error["code"] == "undecodable-file"


def test_dataset_with_a_non_utf8_file_outside_git_still_reports_a_coded_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    target = echo_skill(tmp_path)
    _ = (target / "notes.dat").write_bytes(b"\x80abc")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    code, error = run_dataset(tmp_path, target, manifest(tmp_path), capsys)
    assert code == 1 and error["code"] == "undecodable-file"


@pytest.mark.parametrize("name", [".env", ".secret/key.txt", "a/.hidden"])
def test_dataset_with_a_hidden_file_reports_a_coded_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    target = echo_skill(tmp_path)
    (target / name).parent.mkdir(parents=True, exist_ok=True)
    _ = (target / name).write_text("x")
    code, error = run_dataset(tmp_path, target, manifest(tmp_path), capsys)
    assert code == 1 and error["code"] == "hidden-file"


def test_capture_ignores_the_contract_and_evals_so_the_rubric_never_reaches_a_candidate(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    rubric = parse(BASE | {"kinds": {"echo": {"grader": "judge", "rubric": "RUBRIC_SENTINEL"}}}, "skill")
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(rubric.data()))
    (target / "evals/nested").mkdir()
    _ = (target / "evals/nested/extra.json").write_bytes(b"\xff")
    candidate = Candidate.capture(target, ["SKILL.md"], load_contract(target, {}))
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


# ---- wedge admission (AC-13) ----------------------------------------------------------------------

SEED = {"SKILL.md": "seed", "scripts/old.py": "print(1)\n"}


def proposal(files: dict[str, str] | str, skill: str) -> dict[str, str]:
    return {_wedge.COMPONENT: files if isinstance(files, str) else json.dumps(files), "SKILL.md": skill}


def test_wedge_admits_one_new_script_that_skill_md_references() -> None:
    assert _wedge.admit(SEED, proposal({"scripts/x.py": "print(1)"}, "Run scripts/x.py now.")) == {
        "scripts/x.py": "print(1)"}


@pytest.mark.parametrize("path", [
    "SCRIPTS/x.py", "Scripts/x.py", "scripts/X.PY", "scripts/x.PY", "scripts/ｘ.py", "scripts/х.py",
    "scripts／x.py", "scripts/x.py\n", "scripts/x.py ", " scripts/x.py", "./scripts/x.py", "scripts//x.py",
    "scripts/../scripts/x.py", "scripts/sub/x.py", "/scripts/x.py", "scripts/.x.py", "scripts/-x.py", "scripts/x.pyc",
    "scripts/x.py.py", "scripts/x", "scripts/.py", "scripts\\x.py", "scripts/x\u0000.py", "x.py", "", "scripts/",
    "scripts/old.py", "references/x.py", "scripts/x.sh", "scripts/x.py/"])
def test_wedge_rejects_a_hostile_script_path(path: str) -> None:
    with pytest.raises(ValueError):
        _ = _wedge.admit(SEED, proposal({path: "print(1)"}, f"Run {path} now. Run scripts/x.py now."))


@pytest.mark.parametrize("skill", [
    "Run SCRIPTS/x.py now.", "Run scripts/X.py now.", "Run scripts／x.py now.", "Run scripts/ｘ.py now.",
    "Run scripts/х.py now.", "Run xscripts/x.py now.", "Run scripts/x.pyc now.", "Run scripts/x.py.bak now.",
    "Run scripts/x.pyx now.", "Run scripts/x.py-old now.", "Run scripts/x_py now.", "Run scripts/x.pу now.",
    "Run scripts/ x.py now.", "Run scripts /x.py now.", "Run .scripts/x.py now.", "Run -scripts/x.py now.",
    "Run éscripts/x.py now.", "Run scripts/x.pyé now.", "", "Run the script."])
def test_wedge_rejects_a_skill_md_that_only_has_a_lookalike_reference(skill: str) -> None:
    with pytest.raises(ValueError, match="reference"):
        _ = _wedge.admit(SEED, proposal({"scripts/x.py": "print(1)"}, skill))


@pytest.mark.parametrize("skill", [
    "Run scripts/x.py.", "Run `scripts/x.py`.", "(scripts/x.py)", "[x](scripts/x.py)", "Run scripts/x.py, then stop.",
    "scripts/x.py", "Run scripts/x.py: it works.", "Run scripts/x.py\nthen stop.",
    "Run scripts/x.py and scripts/x.py again."])
def test_wedge_accepts_the_exact_reference_in_ordinary_prose(skill: str) -> None:
    assert set(_wedge.admit(SEED, proposal({"scripts/x.py": "print(1)"}, skill))) == {"scripts/x.py"}


@pytest.mark.parametrize("files", [
    {}, {"scripts/a.py": "1", "scripts/b.py": "2"}, {"scripts/a.py": "1", "references/b.md": "2"},
    {"scripts/a.py": 1}, {"scripts/a.py": None}, {"scripts/a.py": ["x"]}])
def test_wedge_rejects_zero_two_or_non_text_files(files: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _ = _wedge.admit(SEED, proposal(json.dumps(files), "Run scripts/a.py and scripts/b.py."))


@pytest.mark.parametrize("raw", ["", "{", "null", "[]", "[\"scripts/x.py\"]", "\"scripts/x.py\"", "7", "{\"a\": }"])
def test_wedge_rejects_a_malformed_files_component(raw: str) -> None:
    with pytest.raises(ValueError, match="JSON object"):
        _ = _wedge.admit(SEED, proposal(raw, "Run scripts/x.py now."))


def test_wedge_rejects_a_missing_files_component_and_a_missing_skill_component() -> None:
    with pytest.raises(ValueError):
        _ = _wedge.admit(SEED, {"SKILL.md": "Run scripts/x.py now."})
    with pytest.raises(ValueError):
        _ = _wedge.admit(SEED, {_wedge.COMPONENT: json.dumps({"scripts/x.py": "1"})})


def test_wedge_rejects_an_oversized_script_or_skill_md() -> None:
    with pytest.raises(ValueError, match="size"):
        _ = _wedge.admit(SEED, proposal({"scripts/x.py": "x" * (_wedge.SCRIPT_LIMIT + 1)}, "Run scripts/x.py now."))
    with pytest.raises(ValueError, match="size"):
        _ = _wedge.admit(SEED, proposal({"scripts/x.py": "1"}, "Run scripts/x.py now. " + "x" * _wedge.SCRIPT_LIMIT))


def test_wedge_script_at_exactly_the_limit_is_admitted() -> None:
    source = "x" * _wedge.SCRIPT_LIMIT
    assert _wedge.admit(SEED, proposal({"scripts/x.py": source}, "Run scripts/x.py now.")) == {"scripts/x.py": source}


def test_wedge_duplicate_json_keys_collapse_to_one_admitted_file() -> None:
    raw = '{"scripts/x.py": "print(1)", "scripts/x.py": "print(2)"}'
    assert _wedge.admit(SEED, proposal(raw, "Run scripts/x.py now.")) == {"scripts/x.py": "print(2)"}


def test_wedge_new_script_names_only_a_single_added_file() -> None:
    assert _wedge.new_script(SEED, SEED | {"scripts/x.py": "1"}) == "scripts/x.py"
    assert _wedge.new_script(SEED, SEED | {"scripts/x.py": "1", "scripts/y.py": "2"}) is None
    assert _wedge.new_script(SEED, dict(SEED)) is None
    assert _wedge.new_script(SEED, {"SKILL.md": "seed"}) is None


def test_wedge_reference_inside_a_code_fence_or_comment_counts_as_a_reference_today() -> None:
    """Pins the lexical rule: the admission check is a text match, not a Markdown parse."""
    fenced = "```\nscripts/x.py\n```\n"
    commented = "<!-- scripts/x.py -->\n"
    for skill in (fenced, commented):
        assert _wedge.admit(SEED, proposal({"scripts/x.py": "1"}, skill)) == {"scripts/x.py": "1"}


@pytest.mark.parametrize("text", ["Run other-skill/scripts/x.py now.", "Run scripts/x.py/ now.", "Run xscripts/x.py now.",
                                  "Run ../scripts/x.py now."])
def test_wedge_reference_that_is_not_a_path_token_of_the_script_does_not_count(text: str) -> None:
    with pytest.raises(ValueError, match="reference"):
        _ = _wedge.admit(SEED, proposal({"scripts/x.py": "1"}, text))


@pytest.mark.parametrize("text", ["Run scripts/x.py now.", "Run ./scripts/x.py now.", "Run `scripts/x.py`.",
                                  "Run ${CLAUDE_SKILL_DIR}/scripts/x.py now.", "Run <this-skill-directory>/scripts/x.py now.",
                                  "Run scripts/x.py."])
def test_wedge_reference_as_a_path_token_with_an_allowed_prefix_counts(text: str) -> None:
    assert _wedge.admit(SEED, proposal({"scripts/x.py": "1"}, text)) == {"scripts/x.py": "1"}


def test_wedge_reference_check_is_a_mention_check_so_a_negated_mention_still_counts() -> None:
    """Known limit: telling a negation from a call needs natural-language parsing."""
    assert _wedge.admit(SEED, proposal({"scripts/x.py": "1"}, "Do not run scripts/x.py.")) == {"scripts/x.py": "1"}


def test_wedge_cli_without_brief_exits_before_any_model_invocation(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["search", str(tmp_path), "--model", "m", "--mode", "wedge", "--live"])
    error = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert code == 1 and "--brief" in cast(str, error["error"])


@pytest.mark.parametrize("content", [b"\xff\xfe", b"x" * 70000])
def test_wedge_cli_with_a_hostile_brief_exits_with_a_structured_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], content: bytes) -> None:
    brief = tmp_path / "brief.md"
    _ = brief.write_bytes(content)
    code = main(["search", str(tmp_path), "--model", "m", "--mode", "wedge", "--brief", str(brief), "--live"])
    error = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert code == 1 and error["exit_code"] == 1


def test_wedge_cli_with_a_missing_or_directory_brief_exits_with_a_structured_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for brief in (tmp_path / "absent.md", tmp_path):
        code = main(["search", str(tmp_path), "--model", "m", "--mode", "wedge", "--brief", str(brief), "--live"])
        error = cast(dict[str, object], json.loads(capsys.readouterr().err))
        assert code == 1 and error["exit_code"] == 1


def test_brief_with_a_non_wedge_mode_is_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    brief = tmp_path / "brief.md"
    _ = brief.write_text("hello")
    code = main(["search", str(tmp_path), "--model", "m", "--mode", "prompt", "--brief", str(brief), "--live"])
    assert code == 1 and "wedge" in capsys.readouterr().err

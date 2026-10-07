from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate, make_workspace, stage_task
from skillz_experiments._cases import CodedError, load_cases, mapping
from skillz_experiments._cli import main
from skillz_experiments._codex import Codex
from skillz_experiments._contract import parse
from skillz_experiments._runtime import Budget, BudgetExhausted

ECHO = Path(__file__).parent / "fixtures/echo-skill"


def echo_skill(tmp_path: Path) -> Path:
    target = tmp_path / "echo-skill"
    _ = shutil.copytree(ECHO, target)
    _ = (target / "SKILL.md.fixture").rename(target / "SKILL.md")
    set_contract(target, kinds={"echo": {"grader": "exact-json"}})
    return target


def run_intake(tmp_path: Path, target: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    """Walk `run` through both approval stops. Return the exit code and the parsed stderr JSON.

    The final call has no `--live`, so a prepared run ends with the `live-required` stop and exit code 1.
    """
    out = tmp_path / "run"
    out.mkdir(mode=0o700)
    cases = [{"id": f"c{family}-{index}", "family": f"f{family}", "kind": "task", "request": "Echo hello",
              "files": {"input.txt": "hello\n"}, "expected": {"echo": "hello"}, "source": "skill"}
             for family in range(5) for index in range(2)]
    _ = (out / "cases.draft.json").write_text(json.dumps(cases))
    command = ["run", "--target", str(target), "--out", str(out), "--model", "offline"]
    stops = ("cases-unapproved", "budget-unapproved")
    arguments = command
    for stop in stops:
        code = main(arguments)
        captured = capsys.readouterr()
        error = cast(dict[str, object], json.loads(captured.err)) if captured.err.strip() else {}
        if error.get("code") != stop:
            return code, error
        data = cast(dict[str, object], json.loads(captured.out))
        if stop == "cases-unapproved":
            arguments = [*command, "--approve-cases", str(data["case_hash"])]
        else:
            estimate = cast(dict[str, int], data["estimate"])
            arguments = [*arguments, "--approve-budget", str(estimate["calls"])]
    code = main(arguments)
    error = cast(dict[str, object], json.loads(capsys.readouterr().err))
    return code, error


def set_contract(target: Path, **changes: object) -> None:
    path = target / "evals/autoimprove.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    _ = path.write_text(json.dumps(document | changes))


def test_grouped_splits_reject_leakage(tmp_path: Path) -> None:
    case = {"id": "one", "family": "same", "split": "train", "request": "Inspect.",
            "files": {"SKILL.md": "---\nname: example\n---\nBody\n"},
            "expected": {"answer": "yes"}, "provenance": "authored", "provider_approved": True}
    manifest = tmp_path / "cases.json"
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": [case, dict(case, id="two", split="holdout")]}))
    with pytest.raises(ValueError, match="family"):
        _ = load_cases(manifest)


def test_incomplete_case_stays_diagnostic(tmp_path: Path) -> None:
    manifest = tmp_path / "cases.json"
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": [{"id": "one", "family": "one", "split": "train", "provenance": "approved-analytics", "provider_approved": False}]}))
    cases = load_cases(manifest)
    assert not cases[0].eligible


def test_budget_reserves_holdout_and_counts_failures() -> None:
    budget = Budget(8, 60, reserve=6)
    budget.claim()
    budget.claim()
    with pytest.raises(BudgetExhausted):
        budget.claim()
    for _ in range(6):
        budget.claim(holdout=True)
    with pytest.raises(BudgetExhausted):
        budget.claim(holdout=True)
    assert budget.calls == 8

@pytest.mark.parametrize("path", ["a//b", "a/./b", "../x", "/x", "a/", "a\x00b", ".secret"])
def test_reject_path_aliases(path: str) -> None:
    from skillz_experiments._cases import relative

    with pytest.raises(ValueError):
        _ = relative(path)

@pytest.mark.parametrize("path", ["home/x", "tmp/x", "answer.json", "response-schema.json", "AGENTS.md", "nested/AGENTS.md"])
def test_fixture_cannot_overwrite_runtime(path: str) -> None:
    from skillz_experiments._cases import text_map

    with pytest.raises(ValueError, match="runtime-owned"):
        _ = text_map({path: "untrusted"})


def test_manifest_version_is_not_boolean(tmp_path: Path) -> None:
    manifest = tmp_path / "cases.json"
    _ = manifest.write_text('{"schema_version":true,"cases":[]}')
    with pytest.raises(ValueError, match="schema_version"):
        _ = load_cases(manifest)


def test_contract_staging_uses_the_declared_skill_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    code, err = run_intake(tmp_path, target, capsys)
    assert code == 1 and err["code"] == "live-required"
    record = mapping(cast(object, json.loads((tmp_path / "run/run.json").read_text())))
    assert mapping(record["contract"])["skill"] == "echo-skill"
    assert "evals/autoimprove.json" not in mapping(record["seed"])
    from skillz_experiments._contract import load_contract

    contract = load_contract(target)
    candidate = Candidate.capture(target, ["SKILL.md"], contract)
    workspace = make_workspace(tmp_path / "workspace")
    stage_task(workspace, candidate, None)
    assert (workspace / ".agents/skills/echo-skill/SKILL.md").is_file()
    assert not (workspace / ".agents/skills/skillz").exists()


def test_contract_staging_codex_checks_the_declared_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contract = parse({"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
                      "kinds": {"echo": {"grader": "exact-json"}},
                      "helper": {"path": "scripts/echo.py", "input": "fixtures/nested/input.md", "fixtures": [
                          {"input": "hi", "returncode": 0, "output": {"ok": True}}]}}, "skill")
    candidate = Candidate({"SKILL.md": "seed", "scripts/echo.py": "print(1)"}, ("SKILL.md",), contract=contract)
    adapter = object.__new__(Codex)
    adapter.executable, adapter.codex_home, adapter.budget = Path("/bin/codex"), tmp_path, Budget(5, 60, reserve=0)
    seen: list[str] = []
    commands: list[list[str]] = []
    reply = {"stdout": '{"ok": true}'}

    def discover(self: Codex, workspace: Path, skill: str) -> bool:
        del self
        seen.append(skill)
        return (workspace / ".agents/skills/echo-skill/SKILL.md").is_file()

    def process(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, reply["stdout"], "")

    monkeypatch.setattr(Codex, "_discover", discover)
    monkeypatch.setattr("skillz_experiments._codex.process", process)
    assert adapter.check_candidate(candidate)
    assert seen == ["echo-skill"]
    assert commands[0][-2:] == [".agents/skills/echo-skill/scripts/echo.py", "fixtures/nested/input.md"]
    reply["stdout"] = '{"ok": false}'
    assert not adapter.check_candidate(candidate)


def test_contract_unapproved_stops_a_draft(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    set_contract(target, status="draft")
    code, err = run_intake(tmp_path, target, capsys)
    assert code == 1 and err["code"] == "contract-unapproved"
    assert not (tmp_path / "run/run.json").exists()


def test_a_contract_without_a_task_kind_and_with_several_kinds_stops_with_a_coded_error(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    set_contract(target, kinds={"echo": {"grader": "exact-json"}, "rewrite": {"grader": "exact-json"}})
    code, err = run_intake(tmp_path, target, capsys)
    assert code == 1 and err["code"] == "contract-kinds"
    assert "echo, rewrite" in str(err["error"]) and "`task` kind or exactly one kind" in str(err["error"])
    assert not (tmp_path / "run/run.json").exists()


def test_undecodable_file_is_a_structured_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    (target / "assets").mkdir()
    _ = (target / "assets/x.bin").write_bytes(b"\xff\xfe\x00")
    code, err = run_intake(tmp_path, target, capsys)
    assert code == 1 and err["code"] == "undecodable-file"
    assert "assets/x.bin" in str(err["error"])


def test_capture_skips_bytecode_caches_even_when_git_ignores_the_skill_root(tmp_path: Path) -> None:
    _ = subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _ = (tmp_path / ".gitignore").write_text("skill/\n")
    target = echo_skill(tmp_path)
    (target / "scripts/__pycache__").mkdir(parents=True)
    _ = (target / "scripts/__pycache__/a.pyc").write_bytes(b"\xff\xfe\x00")
    _ = (target / "scripts/b.pyc").write_bytes(b"\xff\xfe\x00")
    assert "scripts/b.pyc" not in Candidate.capture(target, ["SKILL.md"]).files


def test_capture_skips_a_self_tracked_gitignore_and_github_directory(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    _ = subprocess.run(["git", "init", "-q", str(target)], check=True)
    _ = (target / ".gitignore").write_text("__pycache__/\n")
    _ = (target / ".gitattributes").write_text("* text=auto\n")
    (target / ".github/workflows").mkdir(parents=True)
    _ = (target / ".github/workflows/x.yml").write_text("name: x\n")
    files = Candidate.capture(target, ["SKILL.md"]).files
    assert "SKILL.md" in files and not any(name.startswith(".") for name in files)


def test_capture_names_any_other_hidden_file(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    _ = (target / ".secret").write_text("x")
    with pytest.raises(CodedError, match=r"\.secret") as raised:
        _ = Candidate.capture(target, ["SKILL.md"])
    assert raised.value.code == "hidden-file" and "git-ignore" not in str(raised.value)


def test_ignored_file_is_skipped_by_capture(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _ = subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _ = (tmp_path / ".gitignore").write_text("__pycache__/\n")
    target = echo_skill(tmp_path)
    (target / "__pycache__").mkdir()
    _ = (target / "__pycache__/x.pyc").write_bytes(b"\xff\xfe\x00")
    code, err = run_intake(tmp_path, target, capsys)
    assert code == 1 and err["code"] == "live-required"
    record = mapping(cast(object, json.loads((tmp_path / "run/run.json").read_text())))
    assert list(mapping(record["seed"])) == ["SKILL.md"]


def test_candidate_rejects_a_package_over_the_size_bound_on_every_construction_path() -> None:
    from skillz_experiments._candidate import PACKAGE_LIMIT

    with pytest.raises(ValueError, match="size limit"):
        _ = Candidate({"SKILL.md": "x" * (PACKAGE_LIMIT + 1)}, ("SKILL.md",))
    seed = Candidate({"SKILL.md": "seed", "references/a.md": "x" * 850_000}, ("SKILL.md",))
    with pytest.raises(ValueError, match="size limit"):
        _ = seed.changed({"SKILL.md": "y" * 200_000})

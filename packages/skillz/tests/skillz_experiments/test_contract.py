from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate, make_workspace, stage_task
from skillz_experiments._cases import Case, CodedError, Journey, digest, load_cases, mapping
from skillz_experiments._cli import main
from skillz_experiments.harness._codex import Codex
from skillz_experiments._contract import Surface, parse
from skillz_experiments._budget import Budget, BudgetExhausted
from skillz_experiments.run._plan import editable_files

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
    command = ["run", "--target", str(target), "--out", str(out), "--model", "offline", "--edit", "prose"]
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


def test_journey_brief_not_staged_into_the_workspace(tmp_path: Path) -> None:
    sentinel = "BRIEF-SENTINEL-7f3a"
    case = Case("j-1", "f", "train", "Start the interview", {"input.txt": "hello\n"}, {"answer": "1"}, "skill", True,
                kind="journey", journey=Journey(f"You want {sentinel}.", 3, ("REQ-1",)))
    candidate = Candidate({"SKILL.md": "seed"}, ("SKILL.md",))
    workspace = make_workspace(tmp_path / "workspace")
    stage_task(workspace, candidate, case)
    staged = [path for path in workspace.rglob("*") if path.is_file()]
    assert (workspace / "input.txt").is_file() and staged
    assert all(sentinel not in path.read_text() for path in staged)


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
    monkeypatch.setattr("skillz_experiments.harness._codex.sandboxed", process)
    assert adapter.check_candidate(candidate).passed
    assert seen == ["echo-skill"]
    assert commands[0][-2:] == [".agents/skills/echo-skill/scripts/echo.py", "fixtures/nested/input.md"]
    reply["stdout"] = '{"ok": false}'
    assert not adapter.check_candidate(candidate).passed


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
    assert "echo, rewrite" in str(err["error"]) and "--kind" in str(err["error"])
    assert not (tmp_path / "run/run.json").exists()


def test_undecodable_file_is_frozen_bytes(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    (target / "assets").mkdir()
    _ = (target / "assets/x.bin").write_bytes(b"\xff\xfe\x00")
    captured = Candidate.capture(target, ["SKILL.md"])
    assert captured.frozen["assets/x.bin"] == b"\xff\xfe\x00" and "assets/x.bin" not in captured.files


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


def surface_contract(surface: object = None, **extra: object) -> dict[str, object]:
    document: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": "echo-skill",
                                   "invocation": "$echo-skill run", "kinds": {"echo": {"grader": "exact-json"}}} | extra
    if surface is not None:
        document["surface"] = surface
    return document


def test_contract_surface_parses_every_declared_field() -> None:
    contract = parse(surface_contract({"tools": ["WebFetch", "mcp__docs__search"], "mcp_config": "mcp/docs.json",
                                       "agents": ["agents/reviewer.md"], "companions": ["skills/helper-skill"]}), "skill")
    assert contract.surface == Surface(("WebFetch", "mcp__docs__search"), "mcp/docs.json", ("agents/reviewer.md",),
                                       ("skills/helper-skill",))
    assert contract.in_companion("skills/helper-skill/SKILL.md") and not contract.in_companion("skills/helper-skill-2/a.md")


@pytest.mark.parametrize(("surface", "message"), [
    ({"tool": ["Bash"]}, "unknown fields: tool"),
    ({"companions": ["../other"]}, "relative, non-hidden"),
    ({"companions": ["/abs/skill"]}, "relative, non-hidden"),
    ({"agents": [".hidden/a.md"]}, "relative, non-hidden"),
    ({"mcp_config": "mcp.yaml"}, "must end with .json"),
    ({"agents": ["agents/a.txt"]}, "must end with .md"),
    ({}, "must declare"),
    ({"tools": []}, "must declare"),
    ({"tools": "Bash"}, "tools must be a list"),
    ({"tools": ["Bash", "Bash"]}, "unique names"),
    ({"tools": ["bad name"]}, "unique names"),
    ({"agents": ["a/x.md", "b/x.md"]}, "unique file names"),
    ({"companions": ["a/x", "b/x"]}, "unique directory names"),
    ({"companions": ["a", "a/b"]}, "inside another companion"),
    ("tools", "must be an object"),
])
def test_contract_surface_rejects_a_malformed_block(surface: object, message: str) -> None:
    with pytest.raises(CodedError, match=message) as caught:
        _ = parse(surface_contract(surface), "skill")
    assert caught.value.code == "contract-surface"


@pytest.mark.parametrize(("surface", "editable", "message"), [
    ({"agents": ["agents/reviewer.md"]}, ["SKILL.md", "agents/reviewer.md"], "agents are read-only"),
    ({"mcp_config": "mcp/docs.json"}, ["SKILL.md", "mcp/docs.json"], "host-run commands"),
    ({"companions": ["skills/helper"]}, ["SKILL.md", "skills/helper/SKILL.md"], "companions are read-only"),
], ids=["agent", "mcp-config", "companion"])
def test_contract_surface_rejects_an_editable_surface_file(surface: dict[str, object], editable: list[str],
                                                           message: str) -> None:
    with pytest.raises(CodedError, match=message) as caught:
        _ = parse(surface_contract(surface, editable=editable), "skill")
    assert caught.value.code == "contract-surface"


def test_contract_surface_unset_keeps_the_old_data_and_identity() -> None:
    contract = parse(surface_contract(), "skill")
    assert contract.surface is None and "surface" not in contract.data()
    plain = digest(contract.data())
    assert contract.identity == plain
    declared = parse(surface_contract({"tools": ["WebFetch"]}), "skill")
    assert declared.identity != plain


def test_contract_surface_round_trips_through_data() -> None:
    contract = parse(surface_contract({"tools": ["WebFetch"], "mcp_config": "m.json", "agents": ["a.md"],
                                       "companions": ["c"]}), "skill")
    again = parse(contract.data(), "skill")
    assert again == contract and again.identity == contract.identity


def test_contract_surface_default_editable_set_excludes_companion_files() -> None:
    contract = parse(surface_contract({"companions": ["skills/helper"]}), "skill")
    files = {"SKILL.md": "a", "references/r.md": "b", "skills/helper/SKILL.md": "c", "skills/helper/scripts/x.py": "d"}
    assert editable_files(files, contract, "prose") == ["SKILL.md", "references/r.md"]


def test_contract_surface_default_editable_set_excludes_agents_and_the_mcp_config() -> None:
    contract = parse(surface_contract({"mcp_config": "scripts/mcp.json", "agents": ["agents/reviewer.md"]}), "skill")
    files = {"SKILL.md": "a", "agents/reviewer.md": "b", "scripts/mcp.json": "{}", "scripts/run.py": "c"}
    assert editable_files(files, contract, "prose+cli") == ["SKILL.md", "scripts/run.py"]
    plain = parse(surface_contract(), "skill")
    assert editable_files(files, plain, "prose+cli") == ["SKILL.md", "agents/reviewer.md", "scripts/mcp.json", "scripts/run.py"]


def test_contract_surface_helper_path_inside_a_companion_is_read_only() -> None:
    document = surface_contract({"companions": ["skills/helper"]}, helper={"path": "skills/helper/scripts/x.py"})
    with pytest.raises(CodedError, match="companions are read-only") as caught:
        _ = parse(document, "skill")
    assert caught.value.code == "contract-surface"
    with pytest.raises(CodedError, match="agents are read-only"):
        _ = parse(surface_contract({"agents": ["agents/r.md"]}, helper={"path": "agents/r.md"}), "skill")


MCP_RUNS_SERVER = '{"mcpServers": {"docs": {"command": "python3", "args": [".agents/skills/echo-skill/scripts/server.py"]}}}'


def test_contract_surface_files_the_mcp_config_runs_are_read_only_under_prose_cli() -> None:
    files = {"SKILL.md": "a", "mcp/docs.json": MCP_RUNS_SERVER, "scripts/server.py": "s", "scripts/run.py": "c"}
    contract = parse(surface_contract({"mcp_config": "mcp/docs.json"}), "skill")
    assert contract.read_only("scripts/server.py", MCP_RUNS_SERVER) and not contract.read_only("scripts/run.py", MCP_RUNS_SERVER)
    assert editable_files(files, contract, "prose+cli") == ["SKILL.md", "scripts/run.py"]
    explicit = parse(surface_contract({"mcp_config": "mcp/docs.json"}, editable=["SKILL.md", "scripts/server.py", "scripts/run.py"]),
                     "skill")
    with pytest.raises(CodedError, match="scripts/server.py") as caught:
        _ = editable_files(files, explicit, "prose+cli")
    assert caught.value.code == "contract-surface"
    helped = parse(surface_contract({"mcp_config": "mcp/docs.json"}, helper={"path": "scripts/server.py"}), "skill")
    with pytest.raises(CodedError, match="scripts/server.py") as caught:
        _ = editable_files(files, helped, "prose+cli")
    assert caught.value.code == "contract-surface"
    plain = parse(surface_contract(), "skill")
    assert editable_files(files, plain, "prose+cli") == ["SKILL.md", "scripts/run.py", "scripts/server.py"]


def test_contract_ties_the_kind_named_journey_to_the_journey_grader() -> None:
    with pytest.raises(CodedError, match="takes the journey grader") as caught:
        _ = parse(surface_contract(kinds={"journey": {"grader": "judge", "rubric": "r"}}), "skill")
    assert caught.value.code == "contract-kinds"

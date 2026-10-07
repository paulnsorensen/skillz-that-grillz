from __future__ import annotations

import json
import sys
import shutil
import subprocess
from pathlib import Path
from collections.abc import Callable
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cli import main
from skillz_experiments._contract import load_contract, parse
from skillz_experiments._harness import Configuration
from skillz_experiments._workflow import run
from skillz_experiments._records import read
from skillz_experiments._runtime import Budget, BudgetExhausted


def test_unsandboxed_wrapper_runs_probe_and_never_infers(tmp_path: Path) -> None:
    log = tmp_path / "operations"
    wrapper = tmp_path / "wrapper.py"
    _ = wrapper.write_text(f"""import json,subprocess,sys
from pathlib import Path
request=json.load(sys.stdin)
with Path({str(log)!r}).open('a') as f: f.write(request['operation']+'\\n')
result=subprocess.run(request['argv'],cwd=request['workspace'],capture_output=True,text=True)
print(json.dumps(dict(schema_version=1,operation=request['operation'],
returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)))
""")
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({
        "schema_version": 1, "adapter": "command", "identity": "unsafe-fixture",
        "command": [sys.executable, str(wrapper)],
    }))
    status = main(["self-test", "--model", "offline", "--preflight-only", "--harness-config", str(config)])
    assert status != 0
    assert log.read_text().splitlines() == ["sandbox"]


ROOT = Path(__file__).resolve().parents[3]


def configured(tmp_path: Path, mode: str = "normal") -> tuple[Path, Path, Path]:
    wrapper = tmp_path / "wrapper.py"
    _ = shutil.copyfile(Path(__file__).parent / "fixtures/command_wrapper.py", wrapper)
    settings = tmp_path / "settings.json"
    log = tmp_path / "requests.jsonl"
    _ = settings.write_text(json.dumps({"mode": mode, "log": str(log)}))
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({
        "schema_version": 1, "adapter": "command", "identity": "protocol-fixture",
        "command": [sys.executable, str(wrapper), str(settings)],
    }))
    return config, settings, log


def requests(log: Path) -> list[dict[str, object]]:
    return [cast(dict[str, object], json.loads(line)) for line in log.read_text().splitlines()]


@pytest.mark.parametrize("mode", ["no-discovery", "bad-version"])
def test_protocol_preflight_fails_before_inference(tmp_path: Path, mode: str) -> None:
    config, _, log = configured(tmp_path, mode)
    assert main(["self-test", "--model", "offline", "--preflight-only", "--harness-config", str(config)]) != 0
    assert "infer" not in [item["operation"] for item in requests(log)]


@pytest.mark.parametrize("mode", ["fail", "wrong-schema"])
def test_failed_inference_is_charged_and_recorded(tmp_path: Path, mode: str, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    config, _, log = configured(tmp_path, mode)
    target, out = make_target(tmp_path), tmp_path / "run"
    with pytest.raises((ValueError, RuntimeError)):
        _ = approved_run(target, out, configuration=Configuration.load(config, "offline"), model="offline")
    record = read(out / "run.json")
    inferred = sum(item["operation"] == "infer" for item in requests(log))
    assert inferred >= 1 and record["calls"] == inferred
    assert record["phase"] == "prepared" and "failure" in record


@pytest.mark.parametrize("change", ["task", "reflection", "judge", "script", "settings"])
def test_resume_rejects_each_frozen_role_or_command_file(tmp_path: Path, change: str, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    config, settings, log = configured(tmp_path, "fail")
    target, out = make_target(tmp_path), tmp_path / "run"
    with pytest.raises((ValueError, RuntimeError)):
        _ = approved_run(target, out, configuration=Configuration.load(config, "offline"), model="offline")
    before = log.read_text()
    if change == "script":
        with (tmp_path / "wrapper.py").open("a") as script:
            _ = script.write("\n# changed\n")
    elif change == "settings":
        with settings.open("a") as stream:
            _ = stream.write("\n")
    else:
        document = read(config)
        document["roles"] = {change: {"model": "different"}}
        _ = config.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="harness .* differs"):
        _ = run(target, out, "offline", live=True, configuration=Configuration.load(config, "offline"))
    assert log.read_text() == before


def test_timeout_claims_shared_budget_and_stops_process(tmp_path: Path) -> None:
    config, _, log = configured(tmp_path, "timeout")
    budget = Budget(20, 0.5)
    adapter = Configuration.load(config, "offline").create("offline", budget, lambda: None)
    try:
        with pytest.raises(BudgetExhausted, match="deadline"):
            _ = adapter.invoke("reflection", schema={"type": "object", "properties": {"x": {"type": "string"}},
                                                    "required": ["x"], "additionalProperties": False})
        assert budget.calls == 1
        assert [item["operation"] for item in requests(log)] == ["infer"]
    finally:
        adapter.close()


def test_executable_alias_keeps_invoked_name(tmp_path: Path) -> None:
    executable = tmp_path / "multicall"
    _ = executable.write_text("#!/usr/bin/python3\nimport sys\nfrom pathlib import Path\nprint(Path(sys.argv[0]).name)\n")
    executable.chmod(0o700)
    alias = tmp_path / "selected-harness"
    alias.symlink_to(executable)
    config = tmp_path / "config.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "command",
                                     "identity": "alias-test", "command": [str(alias)]}))
    role = Configuration.load(config, "offline").roles["task"]
    result = subprocess.run(role.command, cwd=tmp_path, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "selected-harness"


def test_writable_candidate_probe_stops_before_inference(tmp_path: Path) -> None:
    config, _, log = configured(tmp_path, "writable-candidate")
    assert main(["self-test", "--model", "offline", "--preflight-only", "--harness-config", str(config)]) != 0
    assert [item["operation"] for item in requests(log)] == ["sandbox"]


@pytest.mark.parametrize("mode", ["normal", "no-trace"])
def test_selected_judge_model_reaches_the_judge_calls_of_an_intake_run(tmp_path: Path, mode: str, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    config, _, log = configured(tmp_path, mode)
    document = read(config)
    document["roles"] = {"judge": {"model": "judge-model"}}
    _ = config.write_text(json.dumps(document))
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = (target / "evals/autoimprove.json").unlink()
    _ = approved_run(target, out, model="task-model", configuration=Configuration.load(config, "task-model"))
    record = read(out / "run.json")
    assert record["judge_model"] == "judge-model"
    inferred = {str(item["model"]) for item in requests(log) if item["operation"] == "infer"}
    assert "task-model" in inferred and ("judge-model" in inferred) == (mode == "normal")


@pytest.mark.parametrize("inside", ["configuration", "script"])
def test_harness_configuration_cannot_enter_candidate_snapshot(tmp_path: Path, inside: str, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    config, _, log = configured(tmp_path)
    target = tmp_path / "candidate"
    _ = shutil.copytree(make_target(tmp_path / "source"), target)
    if inside == "configuration":
        config = Path(shutil.copyfile(config, target / "harness.json"))
    else:
        script = target / "wrapper.py"
        _ = shutil.copyfile(tmp_path / "wrapper.py", script)
        document = read(config)
        command = cast(list[str], document["command"])
        command[1] = str(script)
        _ = config.write_text(json.dumps(document))
    out = tmp_path / "run"
    with pytest.raises(ValueError, match="outside the candidate"):
        _ = approved_run(target, out, model="offline", configuration=Configuration.load(config, "offline"))
    assert not log.exists()


def test_complete_mixed_roles_parse_without_invoking_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, log = configured(tmp_path)
    command_role = {key: value for key, value in read(config).items() if key != "schema_version"}
    codex = tmp_path / "codex"
    _ = codex.write_text("#!/bin/sh\nexit 99\n")
    codex.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    _ = config.write_text(json.dumps({"schema_version": 1, "roles": {
        "task": {"adapter": "codex", "model": "task-model"},
        "reflection": command_role | {"model": "reflection-model"},
        "judge": {"adapter": "codex", "model": "judge-model"},
    }}))
    configuration = Configuration.load(config, "default")
    assert [role.adapter for role in configuration.roles.values()] == ["codex", "command", "codex"]
    assert [role.model for role in configuration.roles.values()] == ["task-model", "reflection-model", "judge-model"]
    assert not log.exists()


@pytest.mark.parametrize("mode", ["empty-result", "whitespace-result", "invalid-result"])
def test_invalid_inspection_text_scores_zero_without_aborting(tmp_path: Path, mode: str, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    config, _, _ = configured(tmp_path, mode)
    out = tmp_path / "run"
    result = approved_run(make_target(tmp_path), out, model="offline",
                          configuration=Configuration.load(config, "offline"))
    assert result["phase"] == "complete"
    outcomes = [item for item in cast(list[dict[str, object]], read(out / "run.json")["outcomes"]) if "score" in item]
    assert outcomes and all(item["score"] == 0 and item["loaded"] for item in outcomes)


def echo_candidate(tmp_path: Path) -> Candidate:
    target = tmp_path / "echo-skill"
    _ = shutil.copytree(ROOT / "lib/tests/skillz_experiments/fixtures/echo-skill", target)
    _ = (target / "SKILL.md.fixture").rename(target / "SKILL.md")
    return Candidate.capture(target, ["SKILL.md"], load_contract(target))


def test_contract_skill_name_reaches_discover_and_infer(tmp_path: Path) -> None:
    config, _, log = configured(tmp_path)
    adapter = Configuration.load(config, "offline").create("offline", Budget(10, 60, 0), lambda: None)
    candidate = echo_candidate(tmp_path)
    try:
        assert adapter.transports["task"].check_candidate(candidate)
        result = adapter.transports["task"].invoke("Echo hello", candidate)
    finally:
        adapter.close()
    sent = {item["operation"]: item for item in requests(log)}
    assert sent["discover"]["skill_name"] == "echo-skill"
    assert str(sent["discover"]["skill_path"]).endswith(".agents/skills/echo-skill")
    assert sent["infer"]["skill_name"] == "echo-skill"
    assert str(sent["infer"]["skill_path"]).endswith(".agents/skills/echo-skill")
    assert cast(dict[str, object], result["answer"])["load_marker"] == candidate.identity


def test_nested_helper_fixture_runs_through_command_adapter(tmp_path: Path) -> None:
    config, _, _ = configured(tmp_path)
    contract = parse({"schema_version": 1, "status": "approved", "skill": "echo-skill",
                      "invocation": "$echo-skill run", "kinds": {"echo": {"grader": "exact-json"}},
                      "helper": {"path": "scripts/echo.py", "input": "fixtures/nested/input.md",
                                 "fixtures": [{"input": "hi", "returncode": 0, "output": {"ok": True}}]}}, "skill")
    files = {"SKILL.md": "---\nname: echo-skill\ndescription: echo\n---\n",
             "scripts/echo.py": "import json\nprint(json.dumps({'ok': True}))\n"}
    candidate = Candidate(files, ("SKILL.md", "scripts/echo.py"), contract)
    adapter = Configuration.load(config, "offline").create("offline", Budget(10, 60, 0), lambda: None)
    try:
        assert adapter.transports["task"].check_candidate(candidate)
    finally:
        adapter.close()

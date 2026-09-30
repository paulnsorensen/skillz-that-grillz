from __future__ import annotations

import json
import sys
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cli import main
from skillz_experiments._harness import Configuration
from skillz_experiments._records import prepare, read
from skillz_experiments._runtime import Budget, BudgetExhausted
from skillz_experiments._workflow import execute


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
def test_failed_inference_is_charged(tmp_path: Path, mode: str) -> None:
    config, _, log = configured(tmp_path, mode)
    run = tmp_path / "run"
    _ = prepare(ROOT / "lib/src/skillz_experiments/fixtures/self-test.json", ROOT / "skills/skillz", run)
    with pytest.raises((ValueError, RuntimeError)):
        _ = execute(run, "baseline", "offline", live=True, harness_config=config)
    assert read(run / "run.json")["calls"] == 1
    assert read(run / "run.json")["phase"] == "infrastructure-failure"
    assert sum(item["operation"] == "infer" for item in requests(log)) == 1


@pytest.mark.parametrize("change", ["task", "reflection", "judge", "script", "settings"])
def test_resume_rejects_each_frozen_role_or_command_file(tmp_path: Path, change: str) -> None:
    config, settings, log = configured(tmp_path)
    run = tmp_path / "run"
    _ = prepare(ROOT / "lib/src/skillz_experiments/fixtures/self-test.json", ROOT / "skills/skillz", run)
    _ = execute(run, "baseline", "offline", live=True, harness_config=config)
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
    with pytest.raises(ValueError, match="frozen harness"):
        _ = execute(run, "search", "offline", live=True, harness_config=config)
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
def test_custom_audit_uses_selected_judge_and_shared_evaluator(tmp_path: Path, mode: str) -> None:
    config, _, log = configured(tmp_path, mode)
    document = read(config)
    document["roles"] = {"judge": {"model": "judge-model"}}
    _ = config.write_text(json.dumps(document))
    manifest = tmp_path / "manifest.json"
    cases: list[dict[str, object]] = [{"id": str(index), "family": str(index), "split": split, "kind": "audit",
              "request": "Audit fixture.md.", "files": {"fixture.md": "---\nname: safe\n---\n# Safe\n"},
              "expected": {"labels": []}, "labels_reviewed": True,
              "provider_approved": True, "provenance": "synthetic-clean-fixture"}
             for index, split in enumerate(["train", "validation", "holdout", "holdout"])]
    _ = manifest.write_text(json.dumps({"schema_version": 1, "cases": cases}))
    run = tmp_path / "run"
    _ = prepare(manifest, ROOT / "skills/skillz", run)
    _ = execute(run, "baseline", "task-model", live=True, maximum=40, harness_config=config)
    record = read(run / "run.json")
    assert record["judge_model"] == "judge-model"
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    assert all(item["score"] == (1.0 if mode == "normal" else 0.0) for item in outcomes)
    inferred = [item["model"] for item in requests(log) if item["operation"] == "infer"]
    assert inferred == (["task-model", "judge-model"] * 2 if mode == "normal" else ["task-model"] * 2)
    assert record["calls"] == len(inferred)


@pytest.mark.parametrize("inside", ["configuration", "script"])
def test_harness_configuration_cannot_enter_candidate_snapshot(tmp_path: Path, inside: str) -> None:
    config, _, log = configured(tmp_path)
    target = tmp_path / "candidate"
    _ = shutil.copytree(ROOT / "skills/skillz", target)
    if inside == "configuration":
        config = Path(shutil.copyfile(config, target / "harness.json"))
    else:
        script = target / "wrapper.py"
        _ = shutil.copyfile(tmp_path / "wrapper.py", script)
        document = read(config)
        command = cast(list[str], document["command"])
        command[1] = str(script)
        _ = config.write_text(json.dumps(document))
    run = tmp_path / "run"
    _ = prepare(ROOT / "lib/src/skillz_experiments/fixtures/self-test.json", target, run)
    with pytest.raises(ValueError, match="outside the candidate"):
        _ = execute(run, "baseline", "offline", live=True, harness_config=config)
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
def test_invalid_inspection_text_scores_zero_without_aborting(tmp_path: Path, mode: str) -> None:
    config, _, _ = configured(tmp_path, mode)
    run = tmp_path / "run"
    _ = prepare(ROOT / "lib/src/skillz_experiments/fixtures/self-test.json", ROOT / "skills/skillz", run)
    _ = execute(run, "baseline", "offline", live=True, harness_config=config)
    record = read(run / "run.json")
    assert record["phase"] == "baseline"
    assert record["calls"] == 2
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    assert all(item["score"] == 0 and item["loaded"] and item["helper_executed"] for item in outcomes)

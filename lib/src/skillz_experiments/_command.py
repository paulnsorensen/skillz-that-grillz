from __future__ import annotations

import json
import shlex
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

from skillz_experiments._candidate import Candidate, make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import Case, mapping
from skillz_experiments._contract import resolve
from skillz_experiments._evaluation import fixture_result, usage
from skillz_experiments._evaluator import answer_schema
from skillz_experiments._isolation import listening, probe
from skillz_experiments._runtime import Budget, process


def _response(value: object, operation: str) -> dict[str, object]:
    result = mapping(value)
    fields = {"sandbox": {"returncode", "stdout", "stderr"}, "discover": {"skills"},
              "infer": {"answer", "trace", "usage"}}
    if (type(result.get("schema_version")) is not int or result["schema_version"] != 1
            or result.get("operation") != operation
            or set(result) != {"schema_version", "operation"} | fields[operation]):
        raise ValueError("invalid harness response envelope")
    if operation == "sandbox" and (
        type(result["returncode"]) is not int
        or not isinstance(result["stdout"], str) or not isinstance(result["stderr"], str)
    ):
        raise ValueError("invalid sandbox execution response")
    return result


def _events(result: dict[str, object]) -> list[dict[str, object]]:
    raw = result["trace"]
    if not isinstance(raw, list):
        raise ValueError("harness trace must contain completed command executions")
    events: list[dict[str, object]] = []
    for value in cast(list[object], raw):
        item = mapping(value)
        argv = item.get("argv")
        if (set(item) != {"argv", "exit_code"} or type(item["exit_code"]) is not int
                or not isinstance(argv, list) or not argv
                or not all(isinstance(arg, str) and arg for arg in cast(list[object], argv))):
            raise ValueError("invalid completed command trace")
        events.append({"type": "item.completed", "item": {"type": "command_execution",
                       "command": shlex.join(cast(list[str], argv)), "exit_code": item["exit_code"]}})
    if result["usage"] is not None:
        counts = mapping(result["usage"])
        if set(counts) - {"input_tokens", "cached_input_tokens", "output_tokens"} or any(
            value is not None and (type(value) is not int or value < 0) for value in counts.values()
        ):
            raise ValueError("invalid harness token usage")
        events.append({"type": "turn.completed", "usage": counts})
    return events


def _validate_answer(value: object, schema: dict[str, object]) -> None:
    kind = schema.get("type")
    if kind == "object":
        item = mapping(value)
        properties = mapping(schema["properties"])
        if set(item) != set(properties):
            raise ValueError("harness answer fields do not match the response schema")
        for name, child in item.items():
            _validate_answer(child, mapping(properties[name]))
    elif kind == "array":
        if not isinstance(value, list):
            raise ValueError("harness answer must be an array")
        for child in cast(list[object], value):
            _validate_answer(child, mapping(schema["items"]))
    elif kind in {"string", "integer", "boolean"}:
        expected = {"string": str, "integer": int, "boolean": bool}[cast(str, kind)]
        if type(value) is not expected:
            raise ValueError("harness answer type does not match the response schema")
        if "enum" in schema and value not in cast(list[object], schema["enum"]):
            raise ValueError("harness answer is outside the response enum")
    else:
        raise ValueError("unsupported runner response schema")


@final
class Command:
    def __init__(self, command: tuple[str, ...], model: str, budget: Budget, checkpoint: Callable[[], None]) -> None:
        self.command = command
        self.model = model
        self.budget = budget
        self.checkpoint = checkpoint

    def close(self) -> None:
        pass

    def _request(self, operation: str, workspace: Path, **payload: object) -> dict[str, object]:
        request = {"schema_version": 1, "operation": operation, "workspace": str(workspace),
                   "model": self.model, **payload}
        result = process(list(self.command), cwd=workspace.parent, timeout=self.budget.remaining(),
                         environment={"PATH": "/usr/bin:/bin"}, input_text=json.dumps(request))
        if result.returncode:
            raise RuntimeError("harness command fails; no unsafe fallback")
        if len(result.stdout) > 1_000_000:
            raise ValueError("harness response exceeds size limit")
        return _response(cast(object, json.loads(result.stdout)), operation)

    def _sandbox(self, workspace: Path, argv: list[str]) -> dict[str, object]:
        return self._request("sandbox", workspace, argv=argv)

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str]:
        result = self._sandbox(workspace, argv)
        return cast(int, result["returncode"]), cast(str, result["stdout"])

    def _discover(self, workspace: Path, name: str) -> bool:
        skill = workspace / ".agents/skills" / name
        response = self._request("discover", workspace, skill_path=str(skill), skill_name=name)
        raw = response["skills"]
        if not isinstance(raw, list):
            raise ValueError("invalid harness discovery response")
        entries = [mapping(item) for item in cast(list[object], raw)]
        if any(set(item) != {"name", "path"} or not all(isinstance(v, str) for v in item.values()) for item in entries):
            raise ValueError("invalid discovered skill")
        return entries == [{"name": name, "path": str(skill)}]

    def preflight(self) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="skillz-preflight-") as directory:
            root = Path(directory)
            workspace = make_workspace(root / "workspace")
            sealed = root / "sealed"
            _ = sealed.write_text("sealed sentinel")
            (workspace / "escape").symlink_to(sealed)
            with listening() as port:
                script = probe(workspace, sealed, Path(__file__).resolve(), port)
                result = self._sandbox(workspace, ["/usr/bin/python3", "-c", script])
            if result["returncode"] != 0 or cast(str, result["stdout"]).strip() != "isolation-ok":
                raise RuntimeError("harness isolation preflight fails; no unsafe fallback")
            skill = workspace / ".agents/skills/skillz"
            skill.mkdir()
            _ = (skill / "SKILL.md").write_text("---\nname: skillz\ndescription: Inspect public fixtures\n---\nProbe.\n")
            if not self._discover(workspace, "skillz"):
                raise RuntimeError("harness skill discovery preflight fails")
        return {"adapter": "command", "model": self.model, "isolation": "passed", "live_calls": 0}

    def check_candidate(self, candidate: Candidate) -> bool:
        with tempfile.TemporaryDirectory(prefix="skillz-contract-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            rules = resolve(candidate.contract)
            candidate.materialize(workspace / ".agents/skills" / rules.skill)
            if not self._discover(workspace, rules.skill):
                return False
            helper = rules.helper
            if helper is None:
                return True
            for fixture in helper.fixtures:
                _ = (workspace / helper.input).write_text(cast(str, fixture["input"]))
                code, stdout = self.sandbox(workspace, [
                    "/usr/bin/python3", "-I", f".agents/skills/{rules.skill}/{helper.path}", helper.input])
                if not fixture_result(fixture, code, stdout):
                    return False
        return True

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="skillz-task-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            stage_task(workspace, candidate, case)
            skill: dict[str, object] = {}
            if candidate is not None:
                skill = {"skill_name": candidate.skill, "skill_path": str(workspace / ".agents/skills" / candidate.skill)}
            self.budget.claim(holdout=holdout)
            self.checkpoint()
            started = time.monotonic()
            result = self._request("infer", workspace, prompt=prompt, response_schema=schema or answer_schema(), **skill)
            answer = mapping(result["answer"])
            _validate_answer(answer, schema or answer_schema())
            events = _events(result)
            return {"answer": answer, "events": events, "usage": usage(events), "workspace": str(workspace),
                    "latency_seconds": time.monotonic() - started, "output_files": snapshot_outputs(workspace)}

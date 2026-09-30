from __future__ import annotations

import json
import shlex
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, mapping
from skillz_experiments._evaluation import HELPER_INPUTS, helper_result, usage
from skillz_experiments._evaluator import answer_schema
from skillz_experiments._isolation import probe
from skillz_experiments._runtime import Budget, process


def _workspace(root: Path) -> Path:
    workspace = root / "workspace"
    for directory in ("home", "tmp", ".agents/skills"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    return workspace


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
            raise RuntimeError("harness command failed; no unsafe fallback")
        if len(result.stdout) > 1_000_000:
            raise ValueError("harness response exceeds size limit")
        return _response(cast(object, json.loads(result.stdout)), operation)

    def _sandbox(self, workspace: Path, argv: list[str]) -> dict[str, object]:
        return self._request("sandbox", workspace, argv=argv)

    def _discover(self, workspace: Path) -> bool:
        skill = workspace / ".agents/skills/skillz"
        response = self._request("discover", workspace, skill_path=str(skill), skill_name="skillz")
        raw = response["skills"]
        if not isinstance(raw, list):
            raise ValueError("invalid harness discovery response")
        entries = [mapping(item) for item in cast(list[object], raw)]
        if any(set(item) != {"name", "path"} or not all(isinstance(v, str) for v in item.values()) for item in entries):
            raise ValueError("invalid discovered skill")
        return entries == [{"name": "skillz", "path": str(skill)}]

    def preflight(self) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="skillz-preflight-") as directory:
            root = Path(directory)
            workspace = _workspace(root)
            sealed = root / "sealed"
            _ = sealed.write_text("sealed sentinel")
            (workspace / "escape").symlink_to(sealed)
            script = probe(workspace, sealed, Path(__file__).resolve())
            result = self._sandbox(workspace, ["/usr/bin/python3", "-c", script])
            if result["returncode"] != 0 or cast(str, result["stdout"]).strip() != "isolation-ok":
                raise RuntimeError("harness isolation preflight failed; no unsafe fallback")
            skill = workspace / ".agents/skills/skillz"
            skill.mkdir()
            _ = (skill / "SKILL.md").write_text("---\nname: skillz\ndescription: Inspect public fixtures\n---\nProbe.\n")
            if not self._discover(workspace):
                raise RuntimeError("harness skill discovery preflight failed")
        return {"adapter": "command", "model": self.model, "isolation": "passed", "live_calls": 0}

    def check_candidate(self, candidate: Candidate) -> bool:
        with tempfile.TemporaryDirectory(prefix="skillz-contract-") as directory:
            workspace = _workspace(Path(directory))
            candidate.materialize(workspace / ".agents/skills/skillz")
            if not self._discover(workspace):
                return False
            for index, content in enumerate(HELPER_INPUTS):
                _ = (workspace / "fixture.md").write_text(content)
                result = self._sandbox(workspace, ["/usr/bin/python3", "-I",
                    ".agents/skills/skillz/scripts/inspect_skill.py", "fixture.md"])
                if not helper_result(index, cast(int, result["returncode"]), cast(str, result["stdout"])):
                    return False
        return True

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="skillz-task-") as directory:
            workspace = _workspace(Path(directory))
            if candidate is not None:
                candidate.materialize(workspace / ".agents/skills/skillz")
                _ = (workspace / ".agents/skills/skillz/EXPERIMENT_MARKER").write_text(candidate.identity)
            if case is not None:
                for name, content in case.files.items():
                    path = workspace / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _ = path.write_text(content)
            self.budget.claim(holdout=holdout)
            self.checkpoint()
            started = time.monotonic()
            result = self._request("infer", workspace, prompt=prompt, response_schema=schema or answer_schema())
            answer = mapping(result["answer"])
            _validate_answer(answer, schema or answer_schema())
            events = _events(result)
            return {"answer": answer, "events": events, "usage": usage(events), "workspace": str(workspace),
                    "latency_seconds": time.monotonic() - started}

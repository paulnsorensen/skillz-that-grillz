from __future__ import annotations

import json
import os
import shlex
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import ClassVar, cast, final

from skillz_experiments._candidate import Candidate, helper_script, make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import Case, loads_untrusted, mapping
from skillz_experiments._contract import resolve
from skillz_experiments._evaluation import helper_failure, usage
from skillz_experiments._evaluator import answer_schema
from skillz_experiments._protocol import LEDGER_KEY, RESPONSE_FIELDS, USAGE_KEYS, VERSION, argv_strings
from skillz_experiments._budget import Budget
from skillz_experiments._runtime import process
from skillz_experiments.harness._check import Check
from skillz_experiments.harness._isolation import listening, probe
from skillz_experiments.harness._witness import Witness, ledger_entries, stage_witness
from skillz_experiments.harness._check import UNVERIFIED_CREDENTIAL


def _response(value: object, operation: str) -> dict[str, object]:
    result = mapping(value)
    if (type(result.get("schema_version")) is not int or result["schema_version"] != VERSION
            or result.get("operation") != operation
            or set(result) != {"schema_version", "operation"} | RESPONSE_FIELDS[operation]):
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
        argv = argv_strings(item.get("argv"))
        if set(item) != {"argv", "exit_code"} or type(item["exit_code"]) is not int or argv is None:
            raise ValueError("invalid completed command trace")
        events.append({"type": "item.completed", "item": {"type": "command_execution",
                       "command": shlex.join(argv), "exit_code": item["exit_code"]}})
    if result["usage"] is not None:
        counts = mapping(result["usage"])
        if set(counts) - set(USAGE_KEYS) or any(
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


def _stated_reason(stderr: str) -> str:
    """Return the last wrapper line that is a JSON error object `{"error": TEXT, "exit_code": N[, "code": C]}`.

    The reference wrappers print this line for their own errors. The runner shows no other stderr text.
    """
    for line in reversed(stderr.splitlines()):
        try:
            item = mapping(loads_untrusted(line))
        except ValueError:
            continue
        if set(item) - {"error", "exit_code", "code"} or not isinstance(item.get("error"), str) or "exit_code" not in item:
            continue
        return cast(str, item["error"])[:500]
    return ""


def _failure(stderr: str) -> str:
    """Describe a failed harness command. Keep the raw stderr tail in a private file, not in the message."""
    message = "harness command fails; no unsafe fallback"
    reason = _stated_reason(stderr)
    if reason:
        message += f": {reason}"
    if stderr.strip():
        descriptor, filename = tempfile.mkstemp(prefix="skillz-harness-stderr-", suffix=".txt")
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            _ = stream.write(stderr[-4000:])
        message += f"; private stderr tail: {filename}"
    return message


@final
class Command:
    claude_sandbox: ClassVar[bool] = False
    refuses_claude_options: ClassVar[bool] = False
    rejection: str | None = None

    def __init__(self, command: tuple[str, ...], model: str, budget: Budget, checkpoint: Callable[[], None]) -> None:
        self.command = command
        self.model = model
        self.budget = budget
        self.checkpoint = checkpoint

    @property
    def credential_kind(self) -> str:
        """Name the credential kind. The runner cannot read the login of a user command."""
        return UNVERIFIED_CREDENTIAL

    @classmethod
    def host_checks(cls, executable: str | None = None) -> list[Check]:
        """Return no rows: a command role relies on its own sandbox probe in the live preflight."""
        del executable
        return []

    def close(self) -> None:
        pass

    def _request(self, operation: str, workspace: Path, **payload: object) -> dict[str, object]:
        request = {"schema_version": VERSION, "operation": operation, "workspace": str(workspace),
                   "model": self.model, **payload}
        result = process(list(self.command), cwd=workspace.parent, timeout=self.budget.remaining(),
                         environment={"PATH": "/usr/bin:/bin"}, input_text=json.dumps(request))
        if result.returncode:
            raise RuntimeError(_failure(result.stderr))
        if len(result.stdout) > 1_000_000:
            raise ValueError("harness response exceeds size limit")
        return _response(loads_untrusted(result.stdout), operation)

    def _sandbox(self, workspace: Path, argv: list[str]) -> dict[str, object]:
        return self._request("sandbox", workspace, argv=argv)

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str, str]:
        result = self._sandbox(workspace, argv)
        return cast(int, result["returncode"]), cast(str, result["stdout"]), cast(str, result["stderr"])

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

    def reuse_key(self, fingerprint: object) -> str | None:
        """A command harness keeps no live pass, so it has no reuse key."""
        del fingerprint

    def resume(self, kept: object) -> bool:
        """A command harness always runs its preflight."""
        del kept
        return False

    def run_facts(self, evidence: Mapping[str, object]) -> dict[str, object]:
        """Report no run-record fact for a command harness."""
        del evidence
        return {}

    def adopt_login(self) -> None:
        """A command harness shares no Claude login."""

    def check_name(self, candidate: Candidate) -> None:
        """A command harness has no name collision to check."""
        del candidate

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
        return {"adapter": "command", "model": self.model, "isolation": "passed", "isolation_scope": "sandbox operation",
                "live_calls": 0}

    def check_candidate(self, candidate: Candidate) -> bool:
        self.rejection = None
        with tempfile.TemporaryDirectory(prefix="skillz-contract-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            rules = resolve(candidate.contract)
            candidate.materialize(workspace / ".agents/skills" / rules.skill)
            if not self._discover(workspace, rules.skill):
                return False
            self.rejection = helper_failure(self, workspace, rules)
            return self.rejection is None

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="skillz-task-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            stage_task(workspace, candidate, case)
            skill: dict[str, object] = {}
            witness: Witness | None = None
            helper: str | None = None
            if candidate is not None:
                skill = {"skill_name": candidate.skill, "skill_path": str(workspace / ".agents/skills" / candidate.skill)}
                rules = resolve(candidate.contract)
                helper = helper_script(candidate, rules)
                if helper is not None:
                    witness = stage_witness(workspace, candidate.skill, helper)
            self.budget.claim(holdout=holdout)
            self.checkpoint()
            started = time.monotonic()
            result = self._request("infer", workspace, prompt=prompt, response_schema=schema or answer_schema(), **skill)
            answer = mapping(result["answer"])
            _validate_answer(answer, schema or answer_schema())
            events = _events(result)
            outcome: dict[str, object] = {
                "answer": answer, "events": events, "usage": usage(events), "workspace": str(workspace),
                "latency_seconds": time.monotonic() - started, "output_files": snapshot_outputs(workspace)}
            if helper is not None:
                outcome[LEDGER_KEY] = [] if witness is None else ledger_entries(witness)
            return outcome

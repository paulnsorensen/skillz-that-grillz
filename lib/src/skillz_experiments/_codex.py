from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

from skillz_experiments._candidate import Candidate, make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import Case, digest, loads_untrusted, mapping, string
from skillz_experiments._contract import resolve
from skillz_experiments._discovery import discover
from skillz_experiments._evaluation import fixture_result, usage
from skillz_experiments._evaluator import answer_schema, evaluate
from skillz_experiments._isolation import listening, probe
from skillz_experiments._runtime import Budget, process

VERSION = "codex-cli 0.154.0"


def _inline(values: dict[str, str]) -> str:
    return "{" + ",".join(f"{json.dumps(key)}={json.dumps(value)}" for key, value in values.items()) + "}"


def configuration(workspace: Path, executable: Path, disabled: list[Path]) -> list[str]:
    filesystem = {":root": "deny", ":minimal": "read", ":tmpdir": "deny", ":slash_tmp": "deny",
                  str(workspace): "write", str(workspace / ".agents"): "read", str(executable): "read"}
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(workspace / "home"),
                   "TMPDIR": str(workspace / "tmp"), "LANG": "C.UTF-8"}
    settings = [
        'permissions.skillz={extends=":workspace",filesystem=' + _inline(filesystem) + ',network={enabled=false}}',
        'default_permissions="skillz"',
        'shell_environment_policy.inherit="none"',
        "shell_environment_policy.experimental_use_profile=false",
        "shell_environment_policy.ignore_default_excludes=false",
        "shell_environment_policy.set=" + _inline(environment),
        'web_search="disabled"', "skills.bundled.enabled=false", "project_root_markers=[]",
        "features.shell_snapshot=false", "features.hooks=false", "features.plugins=false",
        "features.apps=false", "features.multi_agent=false",
        "skills.config=[" + ",".join('{path=' + json.dumps(str(path)) + ',enabled=false}' for path in disabled) + "]",
    ]
    return [part for setting in settings for part in ("-c", setting)]


@final
class Codex:
    def __init__(self, model: str, budget: Budget, checkpoint: Callable[[], None]) -> None:
        executable = shutil.which("codex")
        if executable is None:
            raise RuntimeError("Codex is unavailable")
        self.executable = Path(executable).resolve()
        self.model = string(model, "explicit model")
        self.budget = budget
        self.checkpoint = checkpoint
        host_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()
        if Path("/etc/codex/skills").exists():
            raise RuntimeError("admin skill roots require an isolated runner")
        self.authentication_home = tempfile.TemporaryDirectory(prefix="skillz-auth-reference-")
        self.codex_home = Path(self.authentication_home.name)
        (self.codex_home / "auth.json").symlink_to(host_home / "auth.json")

    def close(self) -> None:
        self.authentication_home.cleanup()

    def _environment(self, workspace: Path) -> dict[str, str]:
        return {"PATH": "/usr/bin:/bin", "HOME": str(workspace / "home"),
                "TMPDIR": str(workspace / "tmp"), "LANG": "C.UTF-8", "CODEX_HOME": str(self.codex_home)}

    def preflight(self) -> dict[str, object]:
        version = process([str(self.executable), "--version"], cwd=Path("/tmp"), timeout=min(10, self.budget.remaining()),
                          environment={"PATH": "/usr/bin:/bin"})
        if version.returncode or version.stdout.strip() != VERSION:
            raise RuntimeError("unsupported Codex version; expected " + VERSION)
        with tempfile.TemporaryDirectory(prefix="skillz-preflight-") as directory:
            root = Path(directory)
            workspace = root / "workspace"
            _ = make_workspace(workspace)
            sealed = root / "sealed"
            _ = sealed.write_text("sealed sentinel")
            (workspace / "escape").symlink_to(sealed)
            safe = ["PATH=/usr/bin:/bin", "HOME=" + str(workspace / "home"),
                    "TMPDIR=" + str(workspace / "tmp"), "LANG=C.UTF-8"]
            with listening() as port:
                script = probe(workspace, sealed, Path(__file__).resolve(), port)
                command = [str(self.executable), "sandbox", *configuration(workspace, self.executable, []),
                           "-P", "skillz", "--include-managed-config", "-C", str(workspace), "--",
                           "/usr/bin/env", "-i", *safe, "/usr/bin/python3", "-c", script]
                result = process(command, cwd=workspace, timeout=min(30, self.budget.remaining()),
                                 environment=self._environment(workspace))
            if result.returncode or result.stdout.strip() != "isolation-ok":
                raise RuntimeError("Codex isolation preflight fails; no unsafe fallback")
            skill = workspace / ".agents/skills/skillz"
            skill.mkdir()
            _ = (skill / "SKILL.md").write_text("---\nname: skillz\ndescription: Inspect public fixtures\n---\nInspection probe.\n")
            if not self._discover(workspace, "skillz"):
                raise RuntimeError("native Codex skill discovery preflight fails")
            parser = process(self._execution_command(workspace, root / "schema.json", root / "answer.json") + ["--help"],
                             cwd=workspace, timeout=min(10, self.budget.remaining()), environment=self._environment(workspace))
            if parser.returncode:
                evidence = failure_details(parser.returncode, parser.stderr, [])
                raise RuntimeError(f"Codex execution options preflight fails: {evidence['reason']}")
        return {"codex_version": VERSION, "model": self.model, "isolation": "passed",
                "host_skill_discovery": "isolated-home", "live_calls": 0,
                "environment_hash": digest(configuration(Path("/TASK"), self.executable, []))}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="skillz-task-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            stage_task(workspace, candidate, case)
            schema_path = Path(directory) / "response-schema.json"
            _ = schema_path.write_text(json.dumps(schema or answer_schema()))
            output = Path(directory) / "answer.json"
            command = self._execution_command(workspace, schema_path, output)
            self.budget.claim(holdout=holdout)
            self.checkpoint()
            started = time.monotonic()
            result = process(command, cwd=workspace, timeout=self.budget.remaining(),
                             environment=self._environment(workspace), input_text=prompt)
            events = _events(result.stdout)
            if result.returncode or not output.is_file() or output.is_symlink() or output.stat().st_size > 1_000_000:
                evidence = failure_details(result.returncode, result.stderr, events)
                descriptor, filename = tempfile.mkstemp(prefix="skillz-execution-failure-", suffix=".json")
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(evidence, stream, sort_keys=True)
                raise RuntimeError(f"Codex fails: {evidence['reason']} (exit {result.returncode}); the invocation counts against the budget; private evidence: {filename}")
            answer = mapping(loads_untrusted(output.read_text()))
            return {"answer": answer, "events": events, "usage": usage(events), "workspace": str(workspace),
                    "latency_seconds": time.monotonic() - started,
                    "output_files": snapshot_outputs(workspace)}

    def _execution_command(self, workspace: Path, schema: Path, output: Path) -> list[str]:
        return [str(self.executable), "exec", *configuration(workspace, self.executable, []),
                "--ignore-user-config", "--ignore-rules", "--ephemeral", "--json",
                "--skip-git-repo-check", "-C", str(workspace), "--model", self.model,
                "--output-schema", str(schema), "--output-last-message", str(output), "-"]

    def _discover(self, workspace: Path, skill: str) -> bool:
        command = [str(self.executable), "app-server", *configuration(workspace, self.executable, [])]
        return discover(command, workspace, self._environment(workspace), min(30, self.budget.remaining()), skill)

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str]:
        safe = ["PATH=/usr/bin:/bin", "HOME=" + str(workspace / "home"),
                "TMPDIR=" + str(workspace / "tmp"), "LANG=C.UTF-8"]
        command = [str(self.executable), "sandbox", *configuration(workspace, self.executable, []),
                   "-P", "skillz", "--include-managed-config", "-C", str(workspace), "--",
                   "/usr/bin/env", "-i", *safe, *argv]
        result = process(command, cwd=workspace, timeout=min(20, self.budget.remaining()),
                         environment=self._environment(workspace))
        return result.returncode, result.stdout

    def check_candidate(self, candidate: Candidate) -> bool:
        with tempfile.TemporaryDirectory(prefix="skillz-contract-") as directory:
            workspace = make_workspace(Path(directory))
            rules = resolve(candidate.contract)
            candidate.materialize(workspace / ".agents/skills" / rules.skill)
            if not self._discover(workspace, rules.skill):
                return False
            helper = rules.helper
            if helper is None:
                return True
            for fixture in helper.fixtures:
                _ = (workspace / helper.input).write_text(cast(str, fixture["input"]))
                returncode, stdout = self.sandbox(workspace, [
                    "/usr/bin/python3", "-I", f".agents/skills/{rules.skill}/{helper.path}", helper.input])
                if not fixture_result(fixture, returncode, stdout):
                    return False
        return True

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        return evaluate(self, self, candidate, case, holdout=holdout)


def _events(stdout: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in stdout.splitlines():
        try:
            events.append(mapping(loads_untrusted(line)))
        except ValueError:
            raise RuntimeError("invalid Codex JSON event stream") from None
    return events


def failure_details(returncode: int, stderr: str, events: list[dict[str, object]]) -> dict[str, object]:
    messages = [stderr[:8192]]
    for event in events:
        if event.get("type") not in {"error", "turn.failed"}:
            continue
        error = event.get("error")
        message = mapping(cast(object, error)).get("message") if isinstance(error, dict) else event.get("message")
        if isinstance(message, str):
            messages.append(message[:2048])
    detail = "\n".join(messages[:5]).lower()
    reasons = (
        ("unexpected argument", "unsupported-cli-argument"),
        ("error loading", "invalid-configuration"),
        ("error parsing", "invalid-configuration"),
        ("unauthorized", "authentication-unavailable"),
        ("401", "authentication-unavailable"),
        ("login", "authentication-unavailable"),
        ("model_not_found", "model-unavailable"),
        ("not supported", "unsupported-model-or-feature"),
        ("429", "rate-limited"),
        ("rate limit", "rate-limited"),
        ("stream disconnected", "provider-connection-failed"),
        ("connection", "provider-connection-failed"),
        ("sandbox", "sandbox-failure"),
    )
    reason = next((reason for pattern, reason in reasons if pattern in detail),
                  "missing-final-response" if returncode == 0 else "execution-failed")
    return {"schema_version": 1, "returncode": returncode, "reason": reason, "usage": usage(events)}

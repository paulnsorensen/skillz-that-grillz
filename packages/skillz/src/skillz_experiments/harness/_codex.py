from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import ClassVar, cast, final

from skillz_experiments._candidate import Candidate, make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import Case, CodedError, digest, loads_untrusted, mapping, string
from skillz_experiments._contract import resolve
from skillz_experiments.harness._discovery import discover
from skillz_experiments._evaluation import helper_failure, usage
from skillz_experiments._evaluator import answer_schema, evaluate
from skillz_experiments._budget import Budget
from skillz_experiments._runtime import process
from skillz_experiments.harness._check import Check, credential_row, failed
from skillz_experiments.harness._isolation import listening, probe
from skillz_experiments.harness._check import API_KEY, MISSING_CREDENTIAL, SUBSCRIPTION_LOGIN

MINIMUM_VERSION = (0, 154, 0)
MINIMUM_TEXT = ".".join(map(str, MINIMUM_VERSION))
# The fixed role fingerprint label. The observed `codex --version` string, not this label, gates and records the version.
CODEX_IDENTITY = "codex-cli 0.154.0"
_MAX_DIGITS = 4000  # Python's default limit for int(): `sys.int_info.default_max_str_digits` (4300) stays above it.
_AUTH_LIMIT = 1_000_000
CODEX_FIX = f"install Codex {MINIMUM_TEXT} or newer and log in with `codex`, or run with `--harness claude`"
_NUMBER = f"([0-9]{{1,{_MAX_DIGITS}}})"
_VERSION_OUTPUT = re.compile(rf"codex-cli {_NUMBER}\.{_NUMBER}\.{_NUMBER}(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?")


def supported(output: str) -> bool:
    """Return whether `codex --version` output names a release at or above the minimum."""
    match = _VERSION_OUTPUT.fullmatch(output.strip())
    if match is None:
        return False
    core = (int(match[1]), int(match[2]), int(match[3]))
    return core > MINIMUM_VERSION or (core == MINIMUM_VERSION and match[4] is None)


def host_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()


def credential_kind(home: Path) -> str:
    """Return the credential kind that `auth.json` in `home` holds. It reads field names and never keeps a value."""
    try:
        with (home / "auth.json").open("rb") as stream:
            document = loads_untrusted(stream.read(_AUTH_LIMIT).decode())
    except (OSError, ValueError):
        return MISSING_CREDENTIAL
    if not isinstance(document, dict):
        return MISSING_CREDENTIAL
    fields = cast(dict[str, object], document)
    has_tokens = isinstance(fields.get("tokens"), dict) and bool(fields["tokens"])
    has_key = bool(fields.get("OPENAI_API_KEY"))
    mode = fields.get("auth_mode")
    if mode == "apikey":
        return API_KEY if has_key else MISSING_CREDENTIAL
    if mode == "chatgpt":
        return SUBSCRIPTION_LOGIN if has_tokens else MISSING_CREDENTIAL
    if has_tokens:
        return SUBSCRIPTION_LOGIN
    return API_KEY if has_key else MISSING_CREDENTIAL

MODEL_REJECTIONS = frozenset({"unsupported-model-or-feature", "model-unavailable"})
MESSAGE_LIMIT = 300
_FAILURE_REASONS = (
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
_REJECTION_PATTERNS = tuple(pattern for pattern, reason in _FAILURE_REASONS if reason in MODEL_REJECTIONS)


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
    claude_sandbox: ClassVar[bool] = False
    refuses_claude_options: ClassVar[bool] = True
    rejection: str | None = None

    def __init__(self, model: str, budget: Budget, checkpoint: Callable[[], None]) -> None:
        executable = shutil.which("codex")
        if executable is None:
            raise RuntimeError("Codex is unavailable")
        self.executable = Path(executable).resolve()
        self.model = string(model, "explicit model")
        self.budget = budget
        self.checkpoint = checkpoint
        self.host_home = host_home()
        if Path("/etc/codex/skills").exists():
            raise RuntimeError("admin skill roots require an isolated runner")
        self.authentication_home = tempfile.TemporaryDirectory(prefix="skillz-auth-reference-")
        self.codex_home = Path(self.authentication_home.name)
        (self.codex_home / "auth.json").symlink_to(self.host_home / "auth.json")

    @property
    def credential_kind(self) -> str:
        return credential_kind(self.host_home)

    @classmethod
    def host_checks(cls, executable: str | None = None) -> list[Check]:
        """Check the Codex host with the same preflight that a run starts with. A Codex run takes no executable."""
        del executable
        try:
            transport = Codex("doctor", Budget(1, 120, 0), lambda: None)
        except (OSError, RuntimeError) as error:
            return [failed("codex", error, CODEX_FIX), credential_row(credential_kind(host_home()))]
        try:
            _ = transport.preflight()
        except (CodedError, OSError, RuntimeError) as error:
            return [failed("codex-preflight", error, CODEX_FIX), credential_row(transport.credential_kind)]
        finally:
            transport.close()
        return [Check("codex-preflight", "pass", "version, sandbox, and skill discovery checks passed"),
                credential_row(transport.credential_kind)]

    def close(self) -> None:
        self.authentication_home.cleanup()

    def _environment(self, workspace: Path) -> dict[str, str]:
        return {"PATH": "/usr/bin:/bin", "HOME": str(workspace / "home"),
                "TMPDIR": str(workspace / "tmp"), "LANG": "C.UTF-8", "CODEX_HOME": str(self.codex_home)}

    def preflight(self) -> dict[str, object]:
        credential = self.credential_kind
        if credential == MISSING_CREDENTIAL:
            raise CodedError("login-missing", f"no Codex login in {self.host_home / 'auth.json'}; log in with `codex login`")
        version = process([str(self.executable), "--version"], cwd=Path("/tmp"), timeout=min(10, self.budget.remaining()),
                          environment={"PATH": "/usr/bin:/bin"})
        observed = version.stdout.strip()
        if version.returncode or not supported(observed):
            raise CodedError("codex-version-unsupported", f"unsupported Codex version {observed[:80]!r}; "
                             + f"install Codex {MINIMUM_TEXT} or newer (`codex --version` must print `codex-cli X.Y.Z`)")
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
        return {"codex_version": observed, "model": self.model, "isolation": "passed", "credential": credential,
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
                if evidence["reason"] in MODEL_REJECTIONS:
                    raise CodedError("model-unsupported", f"Codex rejected the model {self.model!r} or a feature: "
                                     + _rejection_message(result.stderr, events)[:MESSAGE_LIMIT])
                descriptor, filename = tempfile.mkstemp(prefix="skillz-execution-failure-", suffix=".json")
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(evidence, stream, sort_keys=True)
                raise RuntimeError(f"Codex fails: {evidence['reason']} (exit {result.returncode}); the invocation counts against the budget; private evidence: {filename}")
            answer = mapping(loads_untrusted(output.read_text()))
            return {"answer": answer, "events": events, "usage": usage(events), "workspace": str(workspace),
                    "latency_seconds": time.monotonic() - started,
                    "output_files": snapshot_outputs(workspace)}

    def _execution_command(self, workspace: Path, schema: Path, output: Path) -> list[str]:
        return [str(self.executable), "exec", "--strict-config", *configuration(workspace, self.executable, []),
                "--ignore-user-config", "--ignore-rules", "--ephemeral", "--json",
                "--skip-git-repo-check", "-C", str(workspace), "--model", self.model,
                "--output-schema", str(schema), "--output-last-message", str(output), "-"]

    def _discover(self, workspace: Path, skill: str) -> bool:
        command = [str(self.executable), "app-server", "--strict-config", *configuration(workspace, self.executable, [])]
        return discover(command, workspace, self._environment(workspace), min(30, self.budget.remaining()), skill)

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str, str]:
        safe = ["PATH=/usr/bin:/bin", "HOME=" + str(workspace / "home"),
                "TMPDIR=" + str(workspace / "tmp"), "LANG=C.UTF-8"]
        command = [str(self.executable), "sandbox", *configuration(workspace, self.executable, []),
                   "-P", "skillz", "--include-managed-config", "-C", str(workspace), "--",
                   "/usr/bin/env", "-i", *safe, *argv]
        result = process(command, cwd=workspace, timeout=min(20, self.budget.remaining()),
                         environment=self._environment(workspace))
        return result.returncode, result.stdout, result.stderr

    def reuse_key(self, fingerprint: object) -> str | None:
        """Codex keeps no live pass, so it has no reuse key."""
        del fingerprint

    def resume(self, kept: object) -> bool:
        """Codex always runs its preflight."""
        del kept
        return False

    def run_facts(self, evidence: Mapping[str, object]) -> dict[str, object]:
        """Report the observed Codex version, when the preflight evidence holds one."""
        version = evidence.get("codex_version")
        return {"codex_version": version} if isinstance(version, str) else {}

    def adopt_login(self) -> None:
        """Codex shares no Claude login."""

    def check_name(self, candidate: Candidate) -> None:
        """Codex has no name collision to check."""
        del candidate

    def check_candidate(self, candidate: Candidate) -> bool:
        self.rejection = None
        with tempfile.TemporaryDirectory(prefix="skillz-contract-") as directory:
            workspace = make_workspace(Path(directory))
            rules = resolve(candidate.contract)
            candidate.materialize(workspace / ".agents/skills" / rules.skill)
            if not self._discover(workspace, rules.skill):
                return False
            self.rejection = helper_failure(self, workspace, rules)
            return self.rejection is None

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


def _rejection_message(stderr: str, events: list[dict[str, object]]) -> str:
    for event in events:
        if event.get("type") not in {"error", "turn.failed"}:
            continue
        error = event.get("error")
        message = mapping(cast(object, error)).get("message") if isinstance(error, dict) else event.get("message")
        if isinstance(message, str) and any(pattern in message.lower() for pattern in _REJECTION_PATTERNS):
            return message
    return stderr.strip()


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
    reason = next((reason for pattern, reason in _FAILURE_REASONS if pattern in detail),
                  "missing-final-response" if returncode == 0 else "execution-failed")
    return {"schema_version": 1, "returncode": returncode, "reason": reason, "usage": usage(events)}

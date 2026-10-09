from __future__ import annotations

import contextlib
import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast, final

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError, digest, mapping, string
from skillz_experiments._claude import ISOLATIONS, NOTICE_CODES, TERMINAL_CODES, ClaudeCode, ClaudeLogin
from skillz_experiments._codex import Codex, VERSION
from skillz_experiments._command import Command
from skillz_experiments._evaluator import Transport, evaluate
from skillz_experiments._records import read
from skillz_experiments._runtime import Budget

CLAUDE_IDENTITY = "claude-code-restricted"


class EnvironmentDiffers(CodedError):
    """The runtime environment differs from the frozen record. The run stays resumable after the user restores the environment."""

    def __init__(self, message: str) -> None:
        super().__init__("environment-differs", message)


def _command(value: object, root: Path, *, path_only: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("harness command must be a nonempty argument array, not a shell string")
    parts = [string(item, "command argument") for item in cast(list[object], value)]
    executable = shutil.which(parts[0])
    if executable is None:
        candidate = root / parts[0]
        if path_only:
            raise CodedError("harness-missing", f"the `{parts[0]}` executable is not on PATH; install it and log in, "
                             + "or choose the other harness with --harness")
        if not candidate.is_file():
            raise ValueError("harness executable is unavailable")
        executable = str(candidate)
    parts[0] = str(Path(executable).absolute())
    for index, argument in enumerate(parts[1:], 1):
        path = root / argument
        if not argument.startswith("-") and path.is_file():
            parts[index] = str(path.absolute())
    return tuple(parts)


@dataclass(frozen=True)
class Role:
    adapter: str
    model: str
    command: tuple[str, ...]
    identity: str
    isolation: str = "claude"

    def fingerprint(self) -> str:
        files = {part: (str(Path(part).resolve()), hashlib.sha256(Path(part).read_bytes()).hexdigest())
                 for part in self.command if Path(part).is_absolute() and Path(part).is_file()}
        return digest({"adapter": self.adapter, "model": self.model, "command": self.command,
                       "identity": self.identity, "files": files}
                      | ({"isolation": self.isolation} if self.isolation != "claude" else {}))

    def create(self, budget: Budget, checkpoint: Callable[[], None], out: Path | None = None,
               login: ClaudeLogin | None = None) -> Transport:
        if self.adapter == "claude":
            return ClaudeCode(self.model, budget, checkpoint, Path(self.command[0]), out, login, self.isolation)
        return (Codex(self.model, budget, checkpoint) if self.adapter == "codex"
                else Command(self.command, self.model, budget, checkpoint))


def validate_isolation(adapter: str, isolation: str) -> None:
    """Raise `ValueError` unless `isolation` is known and fits `adapter`. Only the claude harness supports nono."""
    if isolation not in ISOLATIONS:
        raise ValueError("isolation must be claude or nono")
    if isolation == "nono" and adapter != "claude":
        raise ValueError("nono isolation supports only the claude harness")


def _role(value: dict[str, object], model: str, root: Path, *, path_only: bool = False,
          isolation: str = "claude") -> Role:
    if set(value) - {"adapter", "command", "identity", "model", "isolation"}:
        raise ValueError("unknown harness role configuration field")
    adapter = value.get("adapter", "codex")
    selected_model = string(value.get("model", model), "role model")
    selected = string(value.get("isolation", isolation), "role isolation")
    validate_isolation(str(adapter), selected)
    fields = set(value) - {"isolation"}
    if adapter == "codex":
        if fields - {"adapter", "model"}:
            raise ValueError("Codex role accepts only adapter and model")
        return Role("codex", selected_model, _command(["codex"], root, path_only=path_only), VERSION)
    if adapter == "claude":
        if fields - {"adapter", "model", "command"}:
            raise ValueError("Claude role accepts only adapter, model, command, and isolation")
        parts = value.get("command", ["claude"])
        if not isinstance(parts, list) or len(cast(list[object], parts)) != 1:
            raise ValueError("Claude command must name only the executable")
        return Role("claude", selected_model, _command(cast(list[object], parts), root, path_only=path_only),
                    CLAUDE_IDENTITY, selected)
    if adapter != "command":
        raise ValueError("harness adapter must be codex, claude, or command")
    return Role("command", selected_model, _command(value.get("command"), root),
                string(value.get("identity"), "adapter identity"))


@dataclass(frozen=True)
class Configuration:
    roles: dict[str, Role]
    source: Path | None = None

    def __post_init__(self) -> None:
        if len({role.isolation for role in self.roles.values() if role.adapter == "claude"}) > 1:
            raise ValueError("every Claude role of a run must use the same isolation")

    @classmethod
    def load(cls, path: Path | None, model: str) -> Configuration:
        document = read(path) if path is not None else {"schema_version": 1}
        if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
            raise ValueError("unsupported harness configuration version")
        overrides = mapping(document.get("roles", {}))
        if set(overrides) - {"task", "reflection", "judge"}:
            raise ValueError("unknown harness role")
        defaults = {key: value for key, value in document.items() if key not in {"schema_version", "roles"}}
        root = path.resolve().parent if path is not None else Path.cwd()
        roles = {name: _role(defaults | mapping(overrides.get(name, {})), model, root)
                 for name in ("task", "reflection", "judge")}
        return cls(roles, path.resolve() if path is not None else None)

    @classmethod
    def single(cls, adapter: str, model: str, isolation: str = "claude") -> Configuration:
        """Return a configuration that runs every role on one headless harness: `claude` or `codex`.

        `isolation` is `claude` (Claude Code's own sandbox) or `nono`, which only the `claude` harness supports.
        """
        if adapter not in ("claude", "codex"):
            raise ValueError("harness must be claude or codex")
        role = _role({"adapter": adapter}, model, Path.cwd(), path_only=True, isolation=isolation)
        return cls({name: role for name in ("task", "reflection", "judge")})

    def check_boundary(self, target: Path) -> None:
        paths = [Path(argument) for role in self.roles.values() for argument in role.command
                 if Path(argument).is_absolute()]
        if self.source is not None:
            paths.append(self.source)
        if any(path.resolve().is_relative_to(target.resolve()) for path in paths):
            raise ValueError("harness configuration and commands must stay outside the candidate")

    def identity(self) -> dict[str, object]:
        return {name: {"adapter": role.adapter, "model": role.model, "fingerprint": role.fingerprint()}
                for name, role in self.roles.items()}

    def create(self, model: str, budget: Budget, checkpoint: Callable[[], None], out: Path | None = None) -> Harness:
        _ = model
        return Harness(self, budget, checkpoint, out)


def _severity(error: Exception) -> int:
    code = error.code if isinstance(error, CodedError) else None
    return 0 if code in TERMINAL_CODES else 2 if code in NOTICE_CODES else 1


@final
class Harness:
    def __init__(self, configuration: Configuration, budget: Budget, checkpoint: Callable[[], None],
                 out: Path | None = None) -> None:
        self.configuration = configuration
        self.identity = configuration.identity()
        self.transports: dict[str, Transport] = {}
        self.login: ClaudeLogin | None = None
        try:
            for name, role in configuration.roles.items():
                transport = (role.create(budget, checkpoint, out) if self.login is None
                             else role.create(budget, checkpoint, out, self.login))
                self.transports[name] = transport
                if self.login is None and isinstance(transport, ClaudeCode):
                    self.login, transport.owns_login = transport.login, False
        except (OSError, ValueError, RuntimeError):
            with contextlib.suppress(Exception):
                self.close()
            raise

    def _unchanged(self) -> None:
        if self.configuration.identity() != self.identity:
            raise CodedError("harness-changed", "harness executable or script differs from the frozen record")

    def _reuse_key(self, name: str, adapter: ClaudeCode) -> str:
        return digest({"fingerprint": mapping(self.identity[name])["fingerprint"], "environment": adapter.environment_key()})

    def preflight(self, recorded: dict[str, object] | None = None) -> dict[str, object]:
        """Run each role preflight. A Claude role reuses its recorded live pass when its reuse key is unchanged.

        The reuse key joins the role fingerprint and the Claude environment key. A changed key means that the
        runtime environment differs from the frozen record. It fails before any live call, so it costs nothing.
        A reused role still runs the free sandbox probe. Codex and Command roles always run.
        """
        self._unchanged()
        keys = mapping(recorded.get("reuse_keys", {})) if recorded else {}
        passes = mapping(recorded.get("roles", {})) if recorded else {}
        reuse_keys = {name: self._reuse_key(name, adapter) for name, adapter in self.transports.items()
                      if isinstance(adapter, ClaudeCode)}
        if any(name in keys and keys[name] != key for name, key in reuse_keys.items()):
            raise EnvironmentDiffers(
                "runtime environment differs from the frozen record; a changed executable, platform, or sandbox "
                + "setting causes this (a token variable does not, because the runner forwards none); "
                + "restore the first-run environment and resume. "
                + "A runner upgrade that changes the sandbox settings or the network probe also causes this; "
                + "restoring the environment cannot fix that case, so start a new run")
        evidence: dict[str, object] = {}
        for name, adapter in self.transports.items():
            kept = passes.get(name)
            if (isinstance(adapter, ClaudeCode) and keys.get(name) == reuse_keys[name] and isinstance(kept, dict)
                    and cast(dict[str, object], kept).get("isolation") == "passed"):
                adapter.check_helper_sandbox()
                _ = adapter.inventory()
                evidence[name] = kept
                continue
            evidence[name] = adapter.preflight()
        live = sum(cast(int, mapping(item).get("live_calls", 0)) for item in evidence.values())
        return {"roles": evidence, "environment_hash": digest(evidence), "live_calls": live, "reuse_keys": reuse_keys}

    def close(self) -> None:
        """Close every transport, then the shared Claude login once. Raise the most severe error.

        A terminal code ranks first, then any other error, then a notice code.
        """
        errors: list[Exception] = []
        for closer in [adapter.close for adapter in self.transports.values()] + ([self.login.close] if self.login else []):
            try:
                closer()
            except Exception as error:
                errors.append(error)
        if errors:
            raise min(errors, key=_severity)

    def check_name(self, candidate: Candidate) -> None:
        """Stop when the task role cannot load the candidate under its name. It makes no model call."""
        self._unchanged()
        check = cast(Callable[[Candidate], None] | None, getattr(self.transports["task"], "check_name", None))
        if check is not None:
            check(candidate)

    def check_candidate(self, candidate: Candidate) -> bool:
        """Run the local contract check of the task role. It makes no model call."""
        self._unchanged()
        return self.transports["task"].check_candidate(candidate)

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        self._unchanged()
        return evaluate(self.transports["task"], self.transports["judge"], candidate, case, holdout=holdout)

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        self._unchanged()
        return self.transports["reflection"].invoke(prompt, candidate, case, holdout=holdout, schema=schema)

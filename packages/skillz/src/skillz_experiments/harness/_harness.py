from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast, final

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError, digest, mapping
from skillz_experiments._contract import Surface
from skillz_experiments._evaluator import evaluate
from skillz_experiments._records import read
from skillz_experiments._budget import Budget
from skillz_experiments.harness._claude import (NOTICE_CODES, TERMINAL_CODES, ClaudeLogin, ClaudeOptions,
                                              resolve_read_roots)
from skillz_experiments.harness._roles import (BUILTIN_ADAPTERS, ROLES, HarnessName, Role, adapter_class,
                                               command_paths, parse_role)
from skillz_experiments.harness._transport import Transport


class EnvironmentDiffers(CodedError):
    """The runtime environment differs from the frozen record. The run stays resumable after the user restores the environment."""

    def __init__(self, message: str) -> None:
        super().__init__("environment-differs", message)

@dataclass(frozen=True)
class Configuration:
    roles: dict[str, Role]
    source: Path | None = None

    @classmethod
    def load(cls, path: Path | None, model: str) -> Configuration:
        document = read(path) if path is not None else {"schema_version": 1}
        if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
            raise ValueError("unsupported harness configuration version")
        overrides = mapping(document.get("roles", {}))
        if set(overrides) - {*ROLES, "simulator"}:
            raise ValueError("unknown harness role")
        defaults = {key: value for key, value in document.items() if key not in {"schema_version", "roles"}}
        root = path.resolve().parent if path is not None else Path.cwd()
        roles = {name: parse_role(defaults | mapping(overrides.get(name, {})), model, root)
                 for name in ROLES}
        if "simulator" in overrides:
            roles["simulator"] = parse_role(defaults | mapping(overrides["simulator"]), model, root)
        return cls(roles, path.resolve() if path is not None else None)

    @classmethod
    def single(cls, adapter: str, model: str, options: ClaudeOptions | None = None) -> Configuration:
        """Return a configuration that runs every role on one headless harness: `claude` or `codex`.

        The simulator role is absent: a journey case then uses the judge role as its simulator.

        `options` apply to every role, and only the `claude` harness takes them.
        """
        if adapter not in BUILTIN_ADAPTERS:
            raise ValueError("harness must be claude or codex")
        role = parse_role({"adapter": adapter, **({} if options is None else options.data())}, model, Path.cwd(),
                          path_only=True)
        return cls({name: role for name in ROLES})

    @classmethod
    def command(cls, argv: list[str], model: str) -> Configuration:
        """Return a configuration that runs every role through one user command that speaks the adapter protocol.

        The identity names the executable, so a resume with another executable stops with `harness-changed`.
        """
        role = parse_role({"adapter": "command", "command": argv, "identity": f"command-{Path(argv[0] if argv else '').name}"},
                          model, Path.cwd())
        return cls({name: role for name in ROLES})

    def check_boundary(self, target: Path) -> None:
        paths = [Path(argument) for role in self.roles.values() for argument in command_paths(role.command)]
        if self.source is not None:
            paths.append(self.source)
        if any(path.resolve().is_relative_to(target.resolve()) for path in paths):
            raise ValueError("harness configuration and commands must stay outside the candidate")

    def identity(self) -> dict[str, object]:
        return {name: {"adapter": role.adapter, "model": role.model, "fingerprint": role.fingerprint()}
                for name, role in self.roles.items()}

    def sizing_options(self) -> ClaudeOptions:
        """Return the options that size a run: those of the task role. A role of another adapter has the defaults."""
        return self.roles["task"].options

    def host_selections(self) -> list[tuple[str, str | None]]:
        """Return the distinct `(adapter, executable)` pairs of the built-in roles. A command role relies on its sandbox probe.

        A Claude role names its own executable. A Codex role runs `codex` from PATH.
        """
        pairs = {(role.adapter, role.command[0] if role.adapter == "claude" else None)
                 for role in self.roles.values() if role.adapter in BUILTIN_ADAPTERS}
        return sorted(pairs, key=lambda pair: (pair[0], pair[1] or ""))

    def check_read_roots(self, out: Path | None) -> None:
        """Resolve the sandbox read roots of every role against `out`. Raise when a root is unusable."""
        for role in self.roles.values():
            _ = resolve_read_roots(role.options.sandbox_read, out)

    @staticmethod
    def judge_captures(configuration: Configuration | None, adapter: HarnessName) -> bool:
        """Return whether the judge can show capture files. Without a configuration, the run's `adapter` is the judge."""
        judge = configuration.roles.get("judge") if configuration is not None else None
        return adapter_class(judge.adapter if judge is not None else adapter).claude_sandbox

    @staticmethod
    def refuses_claude_options(adapter: HarnessName) -> bool:
        """Return whether a run-level Claude option flag is an error for the built-in `adapter`."""
        return adapter_class(adapter).refuses_claude_options

    def create(self, model: str, budget: Budget, checkpoint: Callable[[], None], out: Path | None = None,
               surface: Surface | None = None, files: Mapping[str, str] | None = None) -> Harness:
        _ = model
        return Harness(self, budget, checkpoint, out, surface, files)


def _severity(error: Exception) -> int:
    code = error.code if isinstance(error, CodedError) else None
    return 0 if code in TERMINAL_CODES else 2 if code in NOTICE_CODES else 1


@final
class Harness:
    def __init__(self, configuration: Configuration, budget: Budget, checkpoint: Callable[[], None],
                 out: Path | None = None, surface: Surface | None = None, files: Mapping[str, str] | None = None) -> None:
        """`surface` and `files` (the seed candidate files) reach the task role only; only a Claude task role can take them."""
        if surface is not None and not adapter_class(configuration.roles["task"].adapter).claude_sandbox:
            raise CodedError("contract-surface-unsupported", "the contract declares a surface, and only the claude "
                             + "harness can run it; run with --harness claude")
        self.configuration = configuration
        self.identity = configuration.identity()
        self.transports: dict[str, Transport] = {}
        self.login: ClaudeLogin | None = None
        self.codex_version: str | None = None
        try:
            for name, role in configuration.roles.items():
                transport = role.create(budget, checkpoint, out, self.login, surface if name == "task" else None, files)
                self.transports[name] = transport
                if self.login is None:
                    self.login = transport.adopt_login()
        except (OSError, ValueError, RuntimeError):
            with contextlib.suppress(Exception):
                self.close()
            raise

    def _unchanged(self) -> None:
        if self.configuration.identity() != self.identity:
            raise CodedError("harness-changed", "harness executable or script differs from the frozen record")

    def preflight(self, recorded: dict[str, object] | None = None) -> dict[str, object]:
        """Run each role preflight. A Claude role reuses its recorded live pass when its reuse key is unchanged.

        The reuse key joins the role fingerprint and the Claude environment key. A changed key means that the
        runtime environment differs from the frozen record. It fails before any live call, so it costs nothing.
        A reused role still runs the free sandbox probe. Codex and Command roles always run.
        The observed Codex version sits in the evidence, so another version changes the environment hash.
        The caller compares that hash and stops with `environment-differs`. `codex_version` holds the observed version.
        """
        self._unchanged()
        keys = mapping(recorded.get("reuse_keys", {})) if recorded else {}
        passes = mapping(recorded.get("roles", {})) if recorded else {}
        candidates = {name: adapter.reuse_key(mapping(self.identity[name])["fingerprint"])
                      for name, adapter in self.transports.items()}
        reuse_keys = {name: key for name, key in candidates.items() if key is not None}
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
            if name in reuse_keys and keys.get(name) == reuse_keys[name] and adapter.resume(kept):
                evidence[name] = kept
                continue
            evidence[name] = adapter.preflight()
        facts = [adapter.run_facts(mapping(evidence[name])) for name, adapter in self.transports.items()]
        versions = [fact.get("codex_version") for fact in facts]
        self.codex_version = next((version for version in versions if isinstance(version, str)), None)
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
        self.transports["task"].check_name(candidate)

    def check_candidate(self, candidate: Candidate) -> bool:
        """Run the local contract check of the task role. It makes no model call."""
        self._unchanged()
        return self.transports["task"].check_candidate(candidate)

    @property
    def rejection(self) -> str | None:
        """Return why the last `check_candidate` call failed, when the task transport says so."""
        return self.transports["task"].rejection

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        self._unchanged()
        return evaluate(self.transports["task"], self.transports["judge"], candidate, case, holdout=holdout,
                        simulator=self.transports.get("simulator"))

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        self._unchanged()
        return self.transports["reflection"].invoke(prompt, candidate, case, holdout=holdout, schema=schema)

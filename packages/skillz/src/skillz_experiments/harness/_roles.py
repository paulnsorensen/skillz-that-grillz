"""Adapter selection: which adapter class serves a role name, and how a harness configuration names its roles."""
from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from skillz_experiments._cases import CodedError, digest, string
from skillz_experiments._contract import Surface
from skillz_experiments._budget import Budget
from skillz_experiments.harness._claude import OPTION_FIELDS, ClaudeCode, ClaudeLogin, ClaudeOptions
from skillz_experiments.harness._codex import CODEX_IDENTITY, Codex
from skillz_experiments.harness._command import Command
from skillz_experiments.harness._transport import Transport

HarnessName = str
CLAUDE_IDENTITY = "claude-code-restricted"
ROLES = ("task", "reflection", "judge")
BUILTIN_ADAPTERS = ("claude", "codex")
NONO_REMOVED = ("this run used the nono isolation backend, which the runner no longer supports; see "
                "https://github.com/paulnsorensen/skillz-that-grillz/issues/211; start a new run in a new --out directory")


def adapter_class(adapter: str) -> type[Transport]:
    """Return the class that serves `adapter`. Any name but `claude` and `codex` is a user command."""
    if adapter == "claude":
        return ClaudeCode
    return Codex if adapter == "codex" else Command


_LONG_OR_EQUALS = re.compile(r"(-[^=]*=|[A-Za-z_][A-Za-z0-9_]*=)(.*)", re.DOTALL)
_SHORT_ATTACHED = re.compile(r"(-[A-Za-z])(/.*)", re.DOTALL)


def _split(argument: str) -> tuple[str, str]:
    """Split `--option=value`, `NAME=value`, and `-rVALUE` (absolute value) into prefix and value. Any other argument is all value."""
    found = _LONG_OR_EQUALS.fullmatch(argument) or _SHORT_ATTACHED.fullmatch(argument)
    return (found.group(1), found.group(2)) if found is not None else ("", argument)


def command_paths(command: tuple[str, ...]) -> list[str]:
    """Return each absolute path that an argument names alone or as the value of an `--option=value` argument."""
    values = (_split(argument)[1] for argument in command)
    return [value for value in values if Path(value).is_absolute()]


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
        prefix, name = _split(argument)
        path = root / name
        if (prefix or not argument.startswith("-")) and path.is_file():
            parts[index] = prefix + str(path.absolute())
    return tuple(parts)


@dataclass(frozen=True)
class Role:
    adapter: str
    model: str
    command: tuple[str, ...]
    identity: str
    options: ClaudeOptions = ClaudeOptions()

    def fingerprint(self) -> str:
        files = {part: (str(Path(part).resolve()), hashlib.sha256(Path(part).read_bytes()).hexdigest())
                 for part in command_paths(self.command) if Path(part).is_file()}
        options = self.options.data()
        return digest({"adapter": self.adapter, "model": self.model, "command": self.command,
                       "identity": self.identity, "files": files}
                      | ({"options": options} if options else {}))

    def create(self, budget: Budget, checkpoint: Callable[[], None], out: Path | None = None,
               login: ClaudeLogin | None = None, surface: Surface | None = None,
               files: Mapping[str, str] | None = None) -> Transport:
        if self.adapter == "claude":
            return ClaudeCode(self.model, budget, checkpoint, Path(self.command[0]), out, login, self.options, surface, files)
        return (Codex(self.model, budget, checkpoint) if self.adapter == "codex"
                else Command(self.command, self.model, budget, checkpoint))


def parse_role(value: dict[str, object], model: str, root: Path, *, path_only: bool = False) -> Role:
    if "isolation" in value:
        if value["isolation"] == "nono":
            raise CodedError("nono-removed", NONO_REMOVED)
        raise CodedError("isolation-removed", "the role key `isolation` is removed; delete it from the harness configuration")
    if set(value) - {"adapter", "command", "identity", "model", *OPTION_FIELDS}:
        raise ValueError("unknown harness role configuration field")
    adapter = value.get("adapter", "codex")
    selected_model = string(value.get("model", model), "role model")
    if adapter == "codex":
        if set(value) - {"adapter", "model"}:
            raise ValueError("Codex role accepts only adapter and model")
        return Role("codex", selected_model, _command(["codex"], root, path_only=path_only), CODEX_IDENTITY)
    if adapter == "claude":
        if set(value) - {"adapter", "model", "command", *OPTION_FIELDS}:
            raise ValueError("Claude role accepts only adapter, model, command, effort, sandbox_read, "
                             + "and sandbox_seconds")
        parts = value.get("command", ["claude"])
        if not isinstance(parts, list) or len(cast(list[object], parts)) != 1:
            raise ValueError("Claude command must name only the executable")
        return Role("claude", selected_model, _command(cast(list[object], parts), root, path_only=path_only),
                    CLAUDE_IDENTITY, ClaudeOptions.parse(value))
    if adapter != "command":
        raise ValueError("harness adapter must be codex, claude, or command")
    if set(value) & OPTION_FIELDS:
        raise ValueError("effort, sandbox_read, and sandbox_seconds apply only to a claude role")
    return Role("command", selected_model, _command(value.get("command"), root),
                string(value.get("identity"), "adapter identity"))

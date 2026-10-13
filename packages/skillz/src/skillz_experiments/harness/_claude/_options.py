from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast, get_args, Literal

from skillz_experiments._cases import CodedError
from skillz_experiments.harness._claude._constants import SANDBOX_SECONDS
from skillz_experiments.harness._claude._login import host_config_dir


Effort = Literal["low", "medium", "high", "xhigh", "max"]


EFFORTS = get_args(Effort)


# Host paths that hold sockets or runtime state. A read root must not equal, hold, or sit inside one.
RUNTIME_DENY = ("/proc", "/sys", "/dev", "/run", "/var/run")


# Shared locations that can hold a pathname socket (tmux, X11, lxd). A read root must not equal, hold, or sit inside one.
SOCKET_DENY = ("/tmp", "/var/tmp", "/var/snap", "/var/lib")


SOCKET_WALK_LIMIT = 100_000


# Credential paths under the home directory. A read root must not equal, hold, or sit inside one.
HOME_DENY = (".ssh", ".gnupg", ".aws", ".config", ".docker", ".kube", ".netrc", ".claude.json", ".bashrc", ".zshrc", ".zshenv",
             ".profile", ".bash_profile", ".bash_history", ".zsh_history", ".git-credentials", ".npmrc", ".pypirc",
             ".password-store", ".local/share/keyrings")


SANDBOX_SECONDS_LIMIT = 600


OPTION_FIELDS = frozenset({"effort", "sandbox_read", "sandbox_seconds"})


@dataclass(frozen=True)
class ClaudeOptions:
    """Optional Claude role settings. The defaults keep the original behavior.

    `effort` passes `--effort` to every Claude Code call of the role. `sandbox_read` lists host paths that the
    runner's OS sandbox mounts read-only, for example a browser install. `sandbox_seconds` is the time limit of
    one OS sandbox command.
    """

    effort: str | None = None
    sandbox_read: tuple[str, ...] = ()
    sandbox_seconds: int = SANDBOX_SECONDS

    @classmethod
    def parse(cls, value: Mapping[str, object]) -> ClaudeOptions:
        """Read the option fields of a role mapping or a run record. The caller checks the other fields.

        The read roots become normalized, unique, and sorted, so a reordered or aliased list gives the same options.
        """
        effort = value.get("effort")
        if effort is not None and effort not in EFFORTS:
            raise ValueError(f"effort must be one of {', '.join(EFFORTS)}")
        roots = value.get("sandbox_read", ())
        if not isinstance(roots, (list, tuple)) or not all(
                isinstance(root, str) and Path(root).is_absolute() for root in cast(Sequence[object], roots)):
            raise ValueError("sandbox_read must be a list of absolute paths")
        seconds = value.get("sandbox_seconds", SANDBOX_SECONDS)
        if type(seconds) is not int or not 1 <= seconds <= SANDBOX_SECONDS_LIMIT:
            raise ValueError(f"sandbox_seconds must be an integer from 1 to {SANDBOX_SECONDS_LIMIT}")
        canonical = sorted({os.path.normpath(root) for root in cast(Sequence[str], roots)})
        return cls(cast(str | None, effort), tuple(canonical), seconds)

    def data(self) -> dict[str, object]:
        """Return the fields that differ from the defaults. `parse` reads this mapping back."""
        data: dict[str, object] = {}
        if self.effort is not None:
            data["effort"] = self.effort
        if self.sandbox_read:
            data["sandbox_read"] = list(self.sandbox_read)
        if self.sandbox_seconds != SANDBOX_SECONDS:
            data["sandbox_seconds"] = self.sandbox_seconds
        return data


def _holds_socket(path: Path) -> bool:
    """Return whether `path` is or holds a socket entry. The walk follows no link and fails closed on an error or past `SOCKET_WALK_LIMIT` entries."""
    def is_socket(entry: str) -> bool:
        try:
            return stat.S_ISSOCK(os.lstat(entry).st_mode)
        except OSError:
            return True
    failed = False

    def onerror(_error: OSError) -> None:
        nonlocal failed
        failed = True
    seen = 0
    if is_socket(str(path)):
        return True
    if not path.is_dir():
        return False
    for directory, names, files in os.walk(path, followlinks=False, onerror=onerror):
        seen += len(names) + len(files)
        if failed or seen > SOCKET_WALK_LIMIT or any(is_socket(os.path.join(directory, name)) for name in (*names, *files)):
            return True
    return failed


def resolve_read_roots(roots: tuple[str, ...], out: Path | None) -> tuple[str, ...]:
    """Check the extra sandbox read roots. Return their paths.

    Each root must exist, must not be a symlink, and must be quotable in the seatbelt profile.
    A root must not equal, hold, or sit inside the Claude config directory, the temporary directory, or the run directory.
    The temporary directory holds the workspaces and the login link.
    A root must not equal or hold the home directory.
    A root must not equal, hold, or sit inside a runtime path (`RUNTIME_DENY`, `$XDG_RUNTIME_DIR`) or a home credential path (`HOME_DENY`).
    A root must not be or hold a socket entry. The check walks the tree without following links.
    """
    home = Path.home().resolve()
    credentials = host_config_dir() / ".credentials.json"
    hidden = [path.resolve() for path in (host_config_dir(), Path(tempfile.gettempdir()), *([] if out is None else [out]),
                                          *([credentials] if credentials.exists() else []))]
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    denied = [Path(path).resolve() for path in (*RUNTIME_DENY, *SOCKET_DENY, *([runtime] if runtime else []))]
    denied += [(home / name).resolve() for name in HOME_DENY]
    resolved: list[str] = []
    for root in roots:
        path = Path(root).resolve()
        if not path.exists():
            raise CodedError("sandbox-read-invalid", f"sandbox read root {root} does not exist")
        if os.path.normpath(root) != str(path):
            raise CodedError("sandbox-read-invalid", f"sandbox read root {root} is or holds a symlink; give the resolved path {path}")
        if '"' in str(path) or "\\" in str(path) or not str(path).isprintable():
            raise CodedError("sandbox-read-invalid", f"sandbox read root {root} holds a quote, a backslash, or a control character")
        if home.is_relative_to(path) or any(path.is_relative_to(item) or item.is_relative_to(path) for item in hidden):
            raise CodedError("sandbox-read-invalid", f"sandbox read root {root} overlaps the home, Claude config, temporary, or run directory")
        if any(path.is_relative_to(item) or item.is_relative_to(path) for item in denied):
            raise CodedError("sandbox-read-invalid", f"sandbox read root {root} overlaps a runtime or credential path")
        if _holds_socket(path):
            raise CodedError("sandbox-read-invalid", f"sandbox read root {root} holds a socket entry or is too large to check")
        resolved.append(str(path))
    return tuple(resolved)


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))

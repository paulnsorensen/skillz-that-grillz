from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast, final, get_args

from skillz_experiments._candidate import Candidate, make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import Case, CodedError, digest, loads_untrusted, mapping, relative, string
from skillz_experiments._contract import resolve
from skillz_experiments._evaluation import helper_failure, usage
from skillz_experiments._evaluator import answer_schema
from skillz_experiments._graders import CAPTURE_DIRECTORY
from skillz_experiments import _nono
from skillz_experiments._isolation import listening, probe, watched, watched_abstract, watched_unix
from skillz_experiments._records import write_bytes
from skillz_experiments._runtime import Budget, BudgetExhausted, process

TOOLS = "Bash,Read,Skill"
PROBE_PREFIX = "skillz-probe-"
AUTH_FAILED = "authentication failed"
CLAUDE_MEMORY = ("CLAUDE.md", "CLAUDE.local.md")
NETWORK_PROBE = "loopback-tcp-http-v2"
READ_PROBE = "read-tool-outside-workspace-v1"
# Curl exit codes that show a request attempt. Codes 5 and 6 are name-resolution failures, and 22 needs `-f`.
CURL_ATTEMPTED = frozenset({0, 7, 28, 52, 56, 97})
LISTENER_REACHED = "network isolation failed: the runner-owned listener accepted a connection from the Bash probe"
CONFIG_PREFIX = "skillz-claude-config-"
CREDENTIALS = ".credentials.json"
CREDENTIAL_LIMIT = 1_000_000
TERMINAL_CODES = frozenset({"isolation-failed", "credential-changed"})
NOTICE_CODES = frozenset({"credential-rotated", "credential-refreshed"})
SANDBOX_HELPERS = ("socat",)
SANDBOX_CAPTURE = "/run/skillz-capture"
SANDBOX_STDERR = "stderr"
SANDBOX_STDERR_LIMIT = 100_000
# Agents that Claude Code lists without any user file. The `--tools` list leaves no way to run them.
BUILTIN_AGENTS = frozenset({"general-purpose", "Explore", "Plan", "statusline-setup", "output-style-setup",
                            "claude-code-guide"})
LOGIN_HINT = "run `claude` once and log in"
INSTALL_BUBBLEWRAP = "install bubblewrap (for example `sudo apt install bubblewrap`)"
INSTALL_SOCAT = "install socat (for example `sudo apt install socat`)"
USERNS_FIX = ("allow unprivileged user namespaces with `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`, "
              "or add an AppArmor profile for bwrap")
NONO_HINT = "or run with `--isolation nono`"
# Settings sources that Claude Code reads besides `--settings`. `project` keeps the staged project skill and drops user
# settings, user skills, and plugins. `--restricted` would also drop the project skill, so the runner does not use it.
SETTING_SOURCES = "project"
Isolation = Literal["claude", "nono"]
ISOLATIONS: tuple[str, ...] = get_args(Isolation)
LIVE_TOOLS = ("/usr/bin/python3", "/usr/bin/curl")
INVENTORY_REQUEST = json.dumps({"type": "control_request", "request_id": "skillz-inventory",
                                "request": {"subtype": "initialize"}}) + "\n"
INVENTORY_SECONDS = 60


PERMISSION_DENY_ROOTS = ("/home", "/root", "/Users", "/mnt", "/media", "/opt", "/srv", "/workspaces", "/data")
RUNTIME_READ = ("/usr", "/bin", "/lib", "/lib32", "/lib64", "/libx32", "/proc/self", "/etc/ld.so.cache", "/etc/ld.so.conf",
                "/etc/ld.so.conf.d", "/etc/alternatives", "/etc/localtime", "/etc/passwd", "/etc/group", "/etc/nsswitch.conf",
                "/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")
MACOS_READ = ("/System", "/Library")
SEATBELT_RUNTIME = ("/usr", "/bin", "/System", "/Library/Developer/CommandLineTools", "/private/var/db/dyld")
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
SANDBOX_SECONDS = 20
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
    credentials = _host_config_dir() / ".credentials.json"
    hidden = [path.resolve() for path in (_host_config_dir(), Path(tempfile.gettempdir()), *([] if out is None else [out]),
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


class NetworkIsolationFailed(CodedError):
    """The live Bash path reaches the network, or the network probe gives no usable result."""

    def __init__(self, reason: str) -> None:
        super().__init__("network-isolation-failed", f"Claude Code isolation preflight fails: {reason}; no unsafe fallback")


class SandboxUnavailable(CodedError):
    """The Bash sandbox cannot start. The message names the cause and the fix. The run never falls back."""

    def __init__(self, cause: str, fix: str) -> None:
        self.fix: str = fix
        super().__init__("sandbox-unavailable", f"Claude Code sandbox unavailable: {cause}; no unsafe fallback; fix: {fix}")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def host_tools(isolation: str) -> list[tuple[str, bool, str]]:
    """List each host tool that the sandbox and the live preflight need, as (name, found, fix).

    The doctor reports these rows, and the helper sandbox probe stops on the first missing one.
    """
    wanted = [] if sys.platform == "darwin" else [
        ("bwrap", INSTALL_BUBBLEWRAP), *([] if isolation == "nono" else [(helper, INSTALL_SOCAT) for helper in SANDBOX_HELPERS])]
    return [*((name, shutil.which(name) is not None, fix) for name, fix in wanted),
            *((path, Path(path).is_file(), f"install {Path(path).name}") for path in LIVE_TOOLS)]


def _sandbox_fix(detail: str) -> str:
    """Pick the fix that matches the sandbox failure text."""
    text = detail.lower()
    if "socat" in text:
        return INSTALL_SOCAT
    if any(word in text for word in ("uid map", "operation not permitted", "permission denied", "namespace", "apparmor")):
        return USERNS_FIX
    if "bubblewrap" in text or "bwrap" in text:
        return INSTALL_BUBBLEWRAP
    return f"{INSTALL_BUBBLEWRAP}; {INSTALL_SOCAT}; {USERNS_FIX}"


def _host_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser().absolute() if configured else Path.home() / ".claude"


CredentialState = Literal["held", "rotated", "refreshed", "lost"]


def _load(path: Path) -> tuple[bytes, dict[str, object]] | None:
    """Read a regular login file of at most `CREDENTIAL_LIMIT` bytes without following a symlink.

    Return None unless the file holds a JSON object.
    """
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            return None
        data = stream.read(CREDENTIAL_LIMIT + 1)
    if len(data) > CREDENTIAL_LIMIT:
        return None
    try:
        return data, mapping(loads_untrusted(data.decode("utf-8")))
    except ValueError:
        return None


def _signed_in(document: dict[str, object], keys: set[str] | None = None) -> bool:
    """Check that `claudeAiOauth` holds nonempty `accessToken` and `refreshToken` strings, and that the top-level keys equal `keys`."""
    oauth = document.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return False
    tokens = cast(dict[str, object], oauth)
    if not all(isinstance(tokens.get(name), str) and tokens.get(name) for name in ("accessToken", "refreshToken")):
        return False
    return keys is None or set(document) == keys


@dataclass(frozen=True)
class _Credential:
    """The identity of the linked login file: its resolved path and its device and inode numbers."""
    path: Path
    device: int
    inode: int

    @classmethod
    def find(cls) -> _Credential:
        source = _host_config_dir() / CREDENTIALS
        try:
            target = source.resolve(strict=True)
            info = target.stat()
        except OSError:
            raise CodedError("login-missing", f"no Claude login at {source}; {LOGIN_HINT}") from None
        if not target.is_file():
            raise CodedError("login-missing", f"no Claude login at {source}; {LOGIN_HINT}")
        return cls(target, info.st_dev, info.st_ino)

    def _matches(self, info: os.stat_result) -> bool:
        return stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == (self.device, self.inode)

    def unchanged(self) -> bool:
        """Check that the real login path still holds this regular file, by device and inode."""
        try:
            return self._matches(self.path.lstat())
        except OSError:
            return False

    def state(self, link: Path) -> CredentialState:
        """Classify the config-directory entry `link` when the run closes.

        `held`: a symlink to this file, unchanged. `rotated`: a symlink to the login path, where another
        process put a new login file that has the login shape. `refreshed`: Claude Code replaced the symlink
        with a regular file. `lost`: any other state, including a new real file that is not a login.
        """
        try:
            info = link.lstat()
            if stat.S_ISREG(info.st_mode):
                return "refreshed"
            if not stat.S_ISLNK(info.st_mode) or Path(os.readlink(link)) != self.path:
                return "lost"
            current = self.path.lstat()
            if self._matches(current):
                return "held"
            loaded = _load(self.path) if stat.S_ISREG(current.st_mode) else None
        except OSError:
            return "lost"
        return "rotated" if loaded is not None and _signed_in(loaded[1]) else "lost"

    def restore(self, refreshed: Path) -> bool:
        """Copy a refreshed login over the real file with mode 0600. Return False when the copy cannot happen.

        The source must be a regular JSON-object file of at most `CREDENTIAL_LIMIT` bytes, opened without
        following a symlink. It must hold the login shape and the top-level keys of the real file. The copy
        happens only while the real file appears unchanged, so it never overwrites a newer login.
        Any `OSError` also returns False.
        """
        try:
            return self._restore(refreshed)
        except OSError:
            return False

    def _restore(self, refreshed: Path) -> bool:
        source = _load(refreshed)
        if source is None or not self.unchanged():
            return False
        original = _load(self.path)
        if original is None or not _signed_in(source[1], set(original[1])):
            return False
        return write_bytes(self.path, source[0], guard=self.unchanged)


@final
class ClaudeLogin:
    """The one config directory and login link that every Claude role of a run shares.

    The owner closes it once. Close deletes the directory, then reports what happened to the login.
    With `api_key`, no directory exists and no login links: nono injects the host API key instead.
    """

    def __init__(self, *, api_key: bool = False) -> None:
        self.credential: _Credential | None = None
        self._temporary: Path | None = None
        if api_key or sys.platform == "darwin":
            self.config_dir = _host_config_dir()
            return
        self.credential = _Credential.find()
        self.config_dir = self._temporary = Path(tempfile.mkdtemp(prefix=CONFIG_PREFIX))
        try:
            (self.config_dir / CREDENTIALS).symlink_to(self.credential.path)
        except OSError:
            shutil.rmtree(self.config_dir, ignore_errors=True)
            raise

    def close(self) -> None:
        """Delete the temporary config directory, then report what happened to the login.

        `rotated` and `refreshed` are warnings: the run stays resumable. Claude Code can replace the symlink with a
        refreshed login; the runner copies that file back while the real file is unchanged, before the directory goes.
        Any other change stops the run with `credential-changed`. A second call does nothing.
        """
        directory, self._temporary = self._temporary, None
        if directory is None or self.credential is None:
            return
        restored = False
        try:
            state = self.credential.state(directory / CREDENTIALS)
            if state == "refreshed":
                restored = self.credential.restore(directory / CREDENTIALS)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        path = self.credential.path
        if state == "rotated":
            raise CodedError("credential-rotated", f"another process replaced the Claude login file {path} during the run; "
                             + "the run used the current file")
        if restored:
            raise CodedError("credential-refreshed", f"Claude Code refreshed the login during the run; the runner copied "
                             + f"it back to {path}")
        if state != "held":
            raise CodedError("credential-changed", f"the Claude login file {path} changed during the run; "
                             + "the Claude login link moved, or the runner could not restore a refreshed login. "
                             + "Log in again with `claude`, then start a new run")


def _user_memory(config_dir: Path) -> list[str]:
    """Return the user memory entries in `config_dir` that Claude Code would load: `CLAUDE.md` and `rules` with `.md` files."""
    rules = config_dir / "rules"
    found = ["CLAUDE.md"] if os.path.lexists(config_dir / "CLAUDE.md") else []
    try:
        if os.path.lexists(rules) and (not rules.is_dir() or any(path.is_file() for path in rules.rglob("*.md"))):
            found.append("rules")
    except OSError as error:
        raise _preflight_leak(f"the user memory directory {rules} cannot be scanned ({error.strerror or error})") from None
    return found


def _preflight_leak(detail: str) -> CodedError:
    return CodedError("preflight-leak", f"Claude Code isolation preflight fails: {detail}; no unsafe fallback")


def _login_failed(fix: str) -> CodedError:
    return CodedError("login-missing", f"Claude Code isolation preflight fails: {AUTH_FAILED}; no unsafe fallback; fix: {fix}")


def _check_ancestors(workspace: Path) -> None:
    """Stop when Claude Code would load a `CLAUDE.md` or `CLAUDE.local.md` from an ancestor of `workspace`."""
    for directory in workspace.resolve().parents:
        for name in CLAUDE_MEMORY:
            if os.path.lexists(directory / name):
                raise _preflight_leak(f"Claude Code would load {directory / name}, an ancestor of the workspace; "
                                      + "move it out, or set TMPDIR to a directory without one")


def settings(workspace: Path, out: Path | None = None, *, overrides: Collection[str] = (),
             unix_sockets: bool = False, sandboxed: bool = True) -> dict[str, object]:
    """Return the sandbox floor. A missing sandbox stops the run, and no command leaves the sandbox.

    Sandboxed commands cannot read the host from `/`. The narrower allow wins, so they read only the
    workspace and the runtime roots. The Read tool follows permission rules, not the sandbox, so a deny
    rule covers the host roots, `/proc`, and the run directory `out` for that tool. Nothing else confines that
    tool, so the live preflight proves it: the Read tool must fail to read a file outside the workspace. Commands cannot write
    `.agents` or `.claude`, so they cannot change the candidate or plant project settings.
    Claude Code's own sandbox enforces the `.agents` and `.claude` write deny rules. `overrides` names the skills that
    `skillOverrides` turns off. `unix_sockets` skips the Unix-socket filter, which cannot start where the host blocks
    a nested user namespace; the live preflight then probes a host socket. `sandboxed=False` turns Claude Code's own
    sandbox off, for a role that nono confines as a whole. Then nono keeps the staged trees beside the workspace
    read-only, and the deny rules do not apply: a task can still create `<workspace>/.claude` entries.
    """
    runtime = [*RUNTIME_READ, *(MACOS_READ if sys.platform == "darwin" else ())]
    temporary = _unique([tempfile.gettempdir(), str(Path(tempfile.gettempdir()).resolve())])
    hidden = ["/proc", *([] if out is None else [str(out), str(out.resolve())])]
    network: dict[str, object] = {"allowedDomains": [], "strictAllowlist": True}
    if unix_sockets:
        network["allowAllUnixSockets"] = True
    protected = [f"{root}/{name}" for root in _unique([str(workspace), str(workspace.resolve())]) for name in (".agents", ".claude")]
    floor: dict[str, object] = {
        "sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False, "network": network,
                    "filesystem": {"denyRead": ["/"], "denyWrite": protected,
                                   "allowRead": _unique([str(workspace), str(workspace.resolve()), *runtime])}},
        "disableBundledSkills": True, "disableAllHooks": True,
        "permissions": {"allow": ["Skill"],
                        "deny": [*[f"Read(/{root}/**)" for root in _unique([*PERMISSION_DENY_ROOTS, str(Path.home()), *hidden])],
                                 *[f"Read(/{root}/{CONFIG_PREFIX}*/**)" for root in temporary]]}}
    if overrides:
        floor["skillOverrides"] = {name: "off" for name in sorted(overrides)}
    if not sandboxed:
        # Claude Code allows sandboxed Bash on its own. With its sandbox off, Bash needs an explicit rule; nono is the boundary.
        floor["sandbox"] = {"enabled": False}
        floor["permissions"] = cast(dict[str, object], floor["permissions"]) | {"allow": ["Skill", "Bash"]}
    return floor


@dataclass(frozen=True)
class Inventory:
    """The command names that Claude Code lists to an SDK client.

    `builtin` holds Claude Code's own commands, which the docs keep hidden from the model when bundled skills are off.
    `foreign` holds every other name except the staged probe skill: account, user, or plugin skills.
    """
    builtin: frozenset[str]
    foreign: frozenset[str]


def _control_response(events: list[dict[str, object]]) -> dict[str, object]:
    response = next((event.get("response") for event in events
                     if event.get("type") == "control_response" and isinstance(event.get("response"), dict)), None)
    return cast(dict[str, object], response) if isinstance(response, dict) else {}


def _commands(events: list[dict[str, object]]) -> tuple[frozenset[str], frozenset[str]] | None:
    """Read the `initialize` control response. Return the built-in names and the other names, or None without a valid list."""
    body = _control_response(events).get("response")
    rows = cast(dict[str, object], body).get("commands") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return None
    builtin: set[str] = set()
    other: set[str] = set()
    for row in cast(list[object], rows):
        entry = cast(dict[str, object], row) if isinstance(row, dict) else {}
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            return None
        (builtin if entry.get("builtin") is True else other).add(name)
    return frozenset(builtin), frozenset(other)


def _events(stdout: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            events.append(mapping(loads_untrusted(line)))
        except ValueError:
            raise RuntimeError("invalid Claude Code JSON event stream") from None
    return events


def _final(events: list[dict[str, object]]) -> dict[str, object] | None:
    return next((event for event in reversed(events) if event.get("type") == "result"), None)


def _loaded_skills(events: list[dict[str, object]], builtin: Collection[str] = ()) -> list[str] | None:
    """Return the skill names in every init event except Claude Code's own commands, or None without an init event."""
    inits = [event for event in events if event.get("type") == "system" and event.get("subtype") == "init"]
    if not inits:
        return None
    loaded: set[str] = set()
    for event in inits:
        raw = event.get("skills")
        items = cast(list[object], raw) if isinstance(raw, list) else None
        names = [cast(dict[str, object], item).get("name") if isinstance(item, dict) else item for item in items or []]
        if items is None or not all(isinstance(name, str) for name in names):
            raise CodedError("isolation-failed", "Claude Code isolation fails: the init event skills field is not a list of names")
        loaded.update(cast(list[str], names))
    return sorted(loaded.difference(builtin))


def _entries(event: dict[str, object], field: str) -> list[str]:
    raw = event.get(field)
    names: list[str] = []
    for item in cast(list[object], raw) if isinstance(raw, list) else []:
        names.append(str(mapping(cast(object, item)).get("name", "")) if isinstance(item, dict) else str(item))
    return names


def _plugins(event: dict[str, object]) -> list[str]:
    """Name each plugin in an init event that does not ship inside Claude Code.

    A built-in plugin has the path `builtin` and a source that ends with `@builtin`.
    """
    raw = event.get("plugins")
    names: list[str] = []
    for item in cast(list[object], raw) if isinstance(raw, list) else []:
        entry: dict[str, object] = mapping(cast(object, item)) if isinstance(item, dict) else {}
        if entry.get("path") == "builtin" and str(entry.get("source", "")).endswith("@builtin"):
            continue
        names.append(str(entry.get("name", "")) if entry else str(cast(object, item)))
    return names


def _leak(events: list[dict[str, object]], skills: list[str], builtin: Collection[str] = ()) -> str | None:
    """Name the entry in an init event that the transport does not configure, or return None.

    The transport configures only the candidate skill. It loads no plugin and no MCP server.
    Claude Code's own commands and built-in plugins ship with the executable, so they do not count.
    """
    for event in (event for event in events if event.get("type") == "system" and event.get("subtype") == "init"):
        found = {"skill": [name for name in _entries(event, "skills") if name not in skills and name not in builtin],
                 "plugin": _plugins(event),
                 "agent": [name for name in _entries(event, "agents") if name not in BUILTIN_AGENTS],
                 "MCP server": _entries(event, "mcp_servers")}
        for kind, names in found.items():
            if names:
                return f"foreign {kind} in init event: {sorted(names)}"
    return None


def _failure(returncode: int, stderr: str, events: list[dict[str, object]], skills: list[str],
             builtin: Collection[str] = (), *, nono_fix: bool = False) -> str | None:
    """Name the isolation failure that a probe run shows, or return None when the run is isolated.

    Raise SandboxUnavailable when the Bash sandbox cannot start. With `nono_fix`, its fix also offers nono.
    """
    final = _final(events)
    errored = returncode != 0 or (final is not None and final.get("is_error") is True)
    detail = (stderr[:8192] + " " + (str(final.get("result", ""))[:2048] if final else "")).lower()
    if returncode != 0 and "sandbox" in detail:
        fix = _sandbox_fix(detail)
        raise SandboxUnavailable(f"the Bash sandbox cannot start ({' '.join(stderr[:200].split())})",
                                 f"{fix}; {NONO_HINT}" if nono_fix else fix)
    if errored and any(word in detail for word in ("login", "api key", "authenticat", "unauthorized", "401", "oauth")):
        return AUTH_FAILED
    loaded = _loaded_skills(events, builtin)
    if loaded is None:
        return "no init event" if returncode == 0 else f"execution failed (exit {returncode})"
    if set(loaded) - set(skills):
        return f"foreign skill in init event: {loaded}"
    if loaded != sorted(skills):
        return f"expected skill missing from init event: {loaded}"
    if errored or final is None:
        return f"execution failed (exit {returncode})"
    return None


def _trace(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """Turn Bash tool calls into completed command events. A tool error means exit code 1."""
    commands: dict[str, str] = {}
    trace: list[dict[str, object]] = []
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in (cast(dict[str, object], item) for item in cast(list[object], content) if isinstance(item, dict)):
            if block.get("type") == "tool_use" and block.get("name") == "Bash" and isinstance(block.get("id"), str):
                command = mapping(block.get("input")).get("command") if isinstance(block.get("input"), dict) else None
                if isinstance(command, str):
                    commands[cast(str, block["id"])] = command
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in commands:
                trace.append({"type": "item.completed", "item": {
                    "type": "command_execution", "command": commands[cast(str, block["tool_use_id"])],
                    "exit_code": 1 if block.get("is_error") is True else 0}})
    return trace


def _token_events(final: dict[str, object]) -> list[dict[str, object]]:
    """Map Claude usage to the shared counts. Input tokens include cache creation and cache reads."""
    raw = final.get("usage")
    if not isinstance(raw, dict) or not raw:
        return []
    counts = mapping(cast(object, raw))

    def count(name: str, *, optional: bool = False) -> int | None:
        if optional and name not in counts:
            return 0
        value = counts.get(name)
        return value if type(value) is int and value >= 0 else None

    input_tokens = count("input_tokens")
    created = count("cache_creation_input_tokens", optional=True)
    cached = count("cache_read_input_tokens", optional=True)
    total = input_tokens + created + cached if (input_tokens is not None and created is not None
                                                 and cached is not None) else None
    return [{"type": "turn.completed", "usage": {
        "input_tokens": total, "cached_input_tokens": cached, "output_tokens": count("output_tokens")}}]


def _declares(text: str, skill: str) -> bool:
    """Check that the SKILL.md frontmatter names the contract skill, has a description, and declares no hooks."""
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return False
    fields: dict[str, str] = {}
    for line in lines[1:lines.index("---", 1)]:
        if not line or line[0].isspace():
            continue
        key, _, value = line.partition(":")
        key = key.strip().removeprefix("? ").strip().strip("\"'").strip().lower()
        fields[key] = value.strip().strip("\"'")
    return (fields.get("name") == skill and bool(fields.get("description"))
            and "hooks" not in fields and "user-invocable" not in fields)


def _bash_control_passed(events: list[dict[str, object]], command_marker: str, control_token: str) -> bool:
    """Accept the control token only from the Bash `tool_result` that answers the `cat` of the control file."""
    uses: set[str] = set()
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if isinstance(message, dict) else None
        for block in cast(list[object], content) if isinstance(content, list) else []:
            item = mapping(block)
            if item.get("type") == "tool_use" and item.get("name") == "Bash":
                command = mapping(item.get("input", {})).get("command")
                if isinstance(command, str) and command_marker in command and isinstance(item.get("id"), str):
                    uses.add(cast(str, item["id"]))
            if item.get("type") == "tool_result" and item.get("tool_use_id") in uses and control_token in json.dumps(item):
                return True
    return False


def _read_failure(events: list[dict[str, object]], sealed_token: str, sealed: str, control_token: str,
                  control_file: str) -> str | None:
    """Judge the read probe. It needs evidence that Bash ran: a command on the sealed path, and the workspace token."""
    text = json.dumps(events)
    if sealed_token in text:
        return "read isolation failed: a host file is readable"
    commands = [cast(str, mapping(item["item"])["command"]) for item in _trace(events)]
    if not any(sealed in command for command in commands):
        return "read probe has no evidence: no Bash command read the sealed host file"
    if not _bash_control_passed(events, control_file, control_token):
        return "read probe has no positive control: Bash cannot read a workspace file"
    return None


def _read_tool_failure(events: list[dict[str, object]], sealed_token: str, sealed: Path, *, evidence: bool) -> str | None:
    """Judge the Read-tool probe. The Read tool must try the sealed host file and fail to return its token.

    Without `evidence`, only a returned token fails. With it, a missing Read call also fails.

    Claude Code's `claude -p` has no flag that confines the Read tool to the workspace, so only this probe proves it.
    """
    uses: set[str] = set()
    answered = False
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if isinstance(message, dict) else None
        for block in cast(list[object], content) if isinstance(content, list) else []:
            item = mapping(block)
            if item.get("type") == "tool_use" and item.get("name") == "Read" and isinstance(item.get("id"), str):
                target = mapping(item.get("input", {})).get("file_path")
                if isinstance(target, str) and Path(target).resolve() == sealed.resolve():
                    uses.add(cast(str, item["id"]))
            if item.get("type") == "tool_result" and item.get("tool_use_id") in uses:
                if sealed_token in json.dumps(item):
                    return "read isolation failed: the Read tool reached a path outside the workspace"
                answered = True
    if evidence and not answered:
        return "read-tool probe has no evidence: no Read tool call answered for the sealed host file"
    return None


def _write_failure(events: list[dict[str, object]], files: list[str], wrote: bool, controlled: bool) -> str | None:
    """Judge the write probe. It needs a failed Bash write to each of `files` (in `.agents` and `.claude`),
    and a workspace write that succeeds.
    """
    if wrote:
        return "write isolation failed: the task model can write to .agents or .claude"
    attempts = [mapping(item["item"]) for item in _trace(events)]
    for file in files:
        if not any(cast(str, item["command"]).startswith("printf") and file in cast(str, item["command"])
                   and item["exit_code"] == 1 for item in attempts):
            return f"write probe has no evidence: no failed Bash printf to {'.agents' if '/.agents/' in file else '.claude'}"
    if not controlled:
        return "write probe has no positive control: Bash cannot write a workspace file"
    return None


def network_commands(port: int, token: str) -> list[str]:
    """Build the live Bash network attempts. Each command must run word for word.

    The first command is a direct loopback TCP attempt. It sends the token if it connects, and prints `denied-<token>`
    if not. The other two send an HTTP request through any configured proxy. `--noproxy ''` clears `NO_PROXY`, so
    curl uses the sandbox proxy for loopback names too. They print `exit-<code>-<token>`. The listener judges the token.
    """
    curl = "/usr/bin/curl --noproxy '' --max-time 5 -sS -o /dev/null"
    return ["/usr/bin/python3 -c \"import socket;c=socket.socket();c.settimeout(5);"
            + f"r=c.connect_ex(('127.0.0.1',{port}));r or c.send(b'{token}');"
            + f"print(('open-' if r==0 else 'denied-')+'{token}')\"",
            *[f"{curl} http://{host}:{port}/{token}; echo exit-$?-{token}" for host in ("127.0.0.1", "localhost")]]


def _results(events: list[dict[str, object]], command: str) -> list[str]:
    """Return the Bash results that answer a tool call whose command equals `command`."""
    uses: set[str] = set()
    results: list[str] = []
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if isinstance(message, dict) else None
        for block in cast(list[object], content) if isinstance(content, list) else []:
            item = mapping(block)
            if item.get("type") == "tool_use" and item.get("name") == "Bash" and isinstance(item.get("id"), str):
                if mapping(item.get("input", {})).get("command") == command:
                    uses.add(cast(str, item["id"]))
            elif item.get("type") == "tool_result" and item.get("tool_use_id") in uses:
                results.append(json.dumps(item))
    return results


def _network_failure(events: list[dict[str, object]], port: int, token: str) -> str | None:
    """Judge the network probe. Each exact command needs output that shows it ran."""
    direct, *proxied = network_commands(port, token)
    outputs = _results(events, direct)
    if not any(f"denied-{token}" in text for text in outputs):
        return f"network probe has no evidence: the direct TCP command output is missing or malformed (output: {outputs!r:.200})"
    for command in proxied:
        codes = [int(code) for text in _results(events, command)
                 for code in cast(list[str], re.findall(rf"exit-(\d+)-{token}", text))]
        if not codes or any(code not in CURL_ATTEMPTED for code in codes):
            return f"network probe has no evidence: the HTTP command output is missing, or curl did not attempt a request (exit codes: {codes})"
    return None


def unix_command(address: str, token: str) -> str:
    """Build the live Bash attempt on a host Unix socket. It sends the token if it connects and prints `denied-<token>` if not.

    `address` is a socket path, or a backslash, `0`, and a name for an abstract socket; Python reads that as the NUL byte.
    """
    return ("/usr/bin/python3 -c \"import socket;c=socket.socket(socket.AF_UNIX);c.settimeout(5);"
            + f"r=c.connect_ex('{address}');r or c.send(b'{token}');print(('open-' if r==0 else 'denied-')+'{token}')\"")


def _unix_failure(events: list[dict[str, object]], address: str, token: str) -> str | None:
    """Judge the Unix-socket probe. The exact command needs output that shows it ran and was denied."""
    outputs = _results(events, unix_command(address, token))
    if not any(f"denied-{token}" in text for text in outputs):
        return f"network probe has no evidence: the Unix-socket command output is missing or malformed (output: {outputs!r:.200})"
    return None


NESTED_PROBE = ("import ctypes,os\n"
                "libc=ctypes.CDLL(None,use_errno=True)\n"
                "uid=os.getuid()\n"
                "if libc.unshare(0x10000000)!=0: raise SystemExit('unshare')\n"
                "try:\n"
                " open('/proc/self/setgroups','w').write('deny')\n"
                " open('/proc/self/uid_map','w').write(f'0 {uid} 1')\n"
                "except OSError: raise SystemExit('map')\n"
                "print('nested-ok')\n")


@functools.cache
def nested_userns_blocked() -> bool:
    """Check, with no model call, whether a process that `bwrap` starts can create its own user namespace.

    Claude Code's Unix-socket filter needs that nested namespace. On Ubuntu 24.04 and later, the AppArmor profile
    `bwrap-userns-restrict` strips the capabilities it needs, and the sysctl alone does not lift it.
    Return False on macOS, without `bwrap`, or when the first-level sandbox itself fails; the sandbox checks
    report those cases. The result holds for the process, so every role gets the same answer.
    """
    tool = shutil.which("bwrap")
    if sys.platform == "darwin" or tool is None:
        return False
    try:
        result = subprocess.run([tool, "--unshare-all", "--die-with-parent", "--ro-bind", "/", "/", "--dev", "/dev",
                                 "--proc", "/proc", "/usr/bin/python3", "-I", "-c", NESTED_PROBE],
                                capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode != 0 and result.stderr.strip() in {"unshare", "map"}


def seatbelt_profile(workspace: Path, read_roots: tuple[str, ...] = ()) -> str:
    """Build the macOS `sandbox-exec` profile. Everything is denied unless this profile allows it.

    The profile allows no network. It allows reads of the system runtime, the extra read roots, and the
    workspace, and writes to the workspace except `.agents`. Later rules win, so the `.agents` deny follows
    the write allow.
    """
    path = str(workspace)
    rules = ["(version 1)", "(deny default)", "(allow process-fork)", "(allow process-exec)",
             "(allow signal (target self))", "(allow sysctl-read)", "(allow file-read-metadata)",
             *[f'(allow file-read* (subpath "{root}"))' for root in (*SEATBELT_RUNTIME, *read_roots)],
             '(allow file-read* (literal "/dev/null") (literal "/dev/urandom"))',
             f'(allow file-read* (subpath "{path}"))', f'(allow file-write* (subpath "{path}"))',
             f'(deny file-write* (subpath "{path}/.agents"))', '(allow file-write* (literal "/dev/null"))']
    return "\n".join(rules) + "\n"


def sandbox_argv(system: str, tool: str, workspace: Path, argv: list[str], environment: dict[str, str],
                 read_roots: tuple[str, ...] = (), capture: Path | None = None) -> list[str]:
    """Wrap `argv` for the OS sandbox: `sandbox-exec` on macOS, bubblewrap elsewhere. No external network.
    Host access is read-only and limited to the system runtime and the extra read roots.

    Under bubblewrap, the command gets its own network namespace.
    It can reach a server that it starts on loopback, but not the host loopback.

    Under bubblewrap with `capture`, the host directory `capture` is bound at `/run/skillz-capture`, outside the
    workspace, and a shell sends the command's standard error to a file there before the command starts.
    `sandbox` reads it. Only bubblewrap can then write to the process standard error,
    so a message that starts with `bwrap:` is a setup failure that the command cannot forge.
    """
    assignments = [f"{name}={value}" for name, value in environment.items()]
    if system == "darwin":
        return [tool, "-p", seatbelt_profile(workspace, read_roots), "/usr/bin/env", "-i", *assignments, *argv]
    path = str(workspace)
    mount = ["--bind", str(capture), SANDBOX_CAPTURE] if capture is not None else []
    shell = ['/bin/sh', '-c', 'exec "$@" 2>"$0"', f"{SANDBOX_CAPTURE}/{SANDBOX_STDERR}"] if capture is not None else [
        '/bin/sh', '-c', 'exec "$@" 2>/dev/null', 'sh']
    return [tool, "--unshare-all", "--die-with-parent", "--clearenv",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
            *[part for root in read_roots for part in ("--ro-bind", root, root)],
            "--bind", path, path, "--ro-bind", f"{path}/.agents", f"{path}/.agents", *mount, "--chdir", path,
            *shell, "/usr/bin/env", "-i", *assignments, *argv]


def _remove_tree(path: Path) -> None:
    """Remove a directory tree, including a subdirectory that the helper made unreadable. Never raises."""
    try:
        path.chmod(0o700)
        with os.scandir(path) as entries:
            children = list(entries)
        for child in children:
            if child.is_dir(follow_symlinks=False):
                _remove_tree(Path(child.path))
            else:
                os.unlink(child.path)
        path.rmdir()
    except OSError:
        pass


def _take_stderr(capture: Path) -> str:
    """Read the command's standard error file. A link, a special file, or a missing file reads as empty. Never raises."""
    try:
        descriptor = os.open(capture / SANDBOX_STDERR, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            return ""
    except OSError:
        os.close(descriptor)
        return ""
    try:
        handle = os.fdopen(descriptor, "rb")
    except OSError:
        os.close(descriptor)
        return ""
    try:
        with handle:
            data = handle.read(SANDBOX_STDERR_LIMIT)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _answer(final: dict[str, object]) -> dict[str, object]:
    structured = final.get("structured_output")
    if isinstance(structured, dict):
        return mapping(cast(object, structured))
    text = final.get("result")
    try:
        return mapping(loads_untrusted(text)) if isinstance(text, str) else {}
    except ValueError:
        return {}


def _probe_name() -> str:
    return f"{PROBE_PREFIX}{secrets.token_hex(8)}"


def _probe_skill(workspace: Path, name: str) -> None:
    skill = workspace / ".agents/skills" / name
    skill.mkdir()
    _ = (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Inspect public fixtures\n---\nInspection probe.\n")


@final
class ClaudeCode:
    rejection: str | None = None

    def __init__(self, model: str, budget: Budget, checkpoint: Callable[[], None], executable: Path | None = None,
                 out: Path | None = None, login: ClaudeLogin | None = None, isolation: str = "claude",
                 options: ClaudeOptions | None = None) -> None:
        """`isolation` is `claude` (Claude Code's own Bash sandbox) or `nono` (nono confines the whole process)."""
        if isolation not in ISOLATIONS:
            raise ValueError("isolation must be claude or nono")
        self.isolation = isolation
        self.nono = _nono.require() if isolation == "nono" else None
        self.out = out
        self.options = options or ClaudeOptions()
        self.read_roots = resolve_read_roots(self.options.sandbox_read, out)
        found = shutil.which("claude") if executable is None else str(executable)
        if found is None:
            raise RuntimeError("Claude Code is unavailable")
        self.executable = Path(found).absolute()
        self.model = string(model, "explicit model")
        self.budget = budget
        self.checkpoint = checkpoint
        self.owns_login = login is None
        self.login = ClaudeLogin(api_key=self.nono is not None) if login is None else login
        self.credential = self.login.credential
        self.config_dir = self.login.config_dir
        self._inventory: Inventory | None = None

    def close(self) -> None:
        """Close the login when this role created it. The harness closes a login that it shares."""
        if self.owns_login:
            self.login.close()

    def unix_sockets(self) -> bool:
        """Return True when the host blocks the nested user namespace of Claude Code's Unix-socket filter.

        The settings then skip that filter. The filesystem and domain rules stay on, and the live preflight
        probes a host Unix socket instead. Under nono, Claude Code's own sandbox is off, so this is False.
        """
        return self.nono is None and nested_userns_blocked()

    def _nono_fix(self) -> bool:
        """Return True when a failure of Claude Code's own sandbox can also be fixed by `--isolation nono`."""
        return self.isolation == "claude" and sys.platform.startswith("linux")

    def _login_fix(self) -> str:
        return f"export a valid {_nono.CREDENTIAL}" if self.nono is not None else LOGIN_HINT

    def _floor(self, workspace: Path, overrides: Collection[str]) -> dict[str, object]:
        return settings(workspace, self.out, overrides=overrides, unix_sockets=self.unix_sockets(),
                        sandboxed=self.nono is None)

    def _config(self, workspace: Path) -> Path:
        """Return the Claude config directory of one process. Under nono, each process gets a fresh, empty directory
        beside its workspace: Bash can read the config directory there, so no transcript or memory may cross calls.
        """
        return workspace.parent / "claude-config" if self.nono is not None else self.config_dir

    def _environment(self, workspace: Path) -> dict[str, str]:
        environment = {"PATH": f"{self.executable.parent}:/usr/bin:/bin", "HOME": str(workspace / "home"),
                       "TMPDIR": str(workspace / "tmp"), "LANG": "C.UTF-8"}
        environment |= {"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1", "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1"}
        if self.nono is not None:
            environment["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        return environment | {"CLAUDE_CONFIG_DIR": str(self._config(workspace))}

    def agents_dir(self, workspace: Path) -> Path:
        """Return where the staged `.agents` tree sits while Claude Code runs. Under nono it sits beside the workspace,
        because Landlock cannot keep a directory inside the writable workspace read-only.
        """
        return (workspace.parent if self.nono is not None else workspace) / ".agents"

    def _stage(self, workspace: Path) -> None:
        """Copy the staged skills to the project skill directory that Claude Code loads from.

        Under nono, `.agents` moves beside the workspace, and a workspace symlink keeps the relative paths that the
        task prompt and the activation check use. A write through the symlink reaches the read-only tree and fails.
        """
        if self.nono is not None:
            _ = shutil.move(workspace / ".agents", self.agents_dir(workspace))
            (workspace / ".agents").symlink_to(Path("..") / ".agents")
        project = self.agents_dir(workspace).parent
        _ = shutil.copytree(project / ".agents/skills", project / ".claude/skills")

    def _launch(self, workspace: Path, argv: list[str], settings_path: Path) -> tuple[list[str], dict[str, str]]:
        """Return the command and environment that start `argv`. Under nono, nono starts Claude Code with a profile
        that grants the workspace, a fresh config directory, and read access to the staged skills.
        """
        environment = self._environment(workspace)
        if self.nono is None:
            return argv, environment
        state = workspace.parent / "nono-state"
        state.mkdir(exist_ok=True)
        self._config(workspace).mkdir(exist_ok=True)
        profile_path = workspace.parent / "nono-profile.json"
        _ = profile_path.write_text(json.dumps(self._profile(self._config(workspace), workspace.parent, settings_path,
                                                             environment)))
        return _nono.command(self.nono, profile_path, environment, argv), _nono.host_environment(state)

    def _profile(self, config_dir: Path, project: Path, settings_file: Path, environment: dict[str, str]) -> dict[str, object]:
        """Build the nono profile for `_launch` and `environment_key`, so the preflight key follows the real sandbox."""
        return _nono.profile(config_dir=config_dir, project=project, executable=self.executable.resolve(),
                             settings_file=settings_file, variables=sorted(environment))

    def _base(self, settings_path: Path) -> list[str]:
        return [str(self.executable), "-p", "--setting-sources", SETTING_SOURCES, "--no-session-persistence",
                "--tools", TOOLS, "--strict-mcp-config", "--settings", str(settings_path),
                "--output-format", "stream-json", "--verbose"]

    def _command(self, settings_path: Path, schema: dict[str, object] | None) -> list[str]:
        command = [*self._base(settings_path), "--model", self.model,
                   *(["--effort", self.options.effort] if self.options.effort is not None else [])]
        return command + (["--json-schema", json.dumps(schema)] if schema is not None else [])

    def _run(self, workspace: Path, prompt: str, schema: dict[str, object] | None, timeout: float,
             expected: list[str]) -> tuple[int, str, list[dict[str, object]]]:
        _check_ancestors(workspace)
        self._stage(workspace)
        settings_path = workspace.parent / "settings.json"
        _ = settings_path.write_text(json.dumps(self._floor(workspace, self.inventory().foreign - set(expected))))
        command, environment = self._launch(workspace, self._command(settings_path, schema), settings_path)
        result = process(command, cwd=workspace, timeout=timeout, environment=environment, input_text=prompt)
        return result.returncode, result.stderr, _events(result.stdout)

    def _list(self, workspace: Path, overrides: frozenset[str]) -> tuple[frozenset[str], frozenset[str]]:
        """Send one `initialize` control request and return the built-in and other command names. No model turn runs."""
        _check_ancestors(workspace)
        settings_path = workspace.parent / "inventory-settings.json"
        _ = settings_path.write_text(json.dumps(self._floor(workspace, overrides)))
        command, environment = self._launch(workspace, [*self._base(settings_path), "--input-format", "stream-json"],
                                            settings_path)
        timeout = min(INVENTORY_SECONDS, self.budget.remaining())
        try:
            result = process(command, cwd=workspace, timeout=timeout, environment=environment, input_text=INVENTORY_REQUEST)
        except BudgetExhausted:
            if timeout < INVENTORY_SECONDS:
                raise
            raise _preflight_leak(f"the Claude Code skill inventory timed out after {INVENTORY_SECONDS} seconds") from None
        events = _events(result.stdout)
        commands = _commands(events)
        if commands is None:
            reason = _failure(result.returncode, result.stderr, events, [], nono_fix=self._nono_fix())
            if reason == AUTH_FAILED:
                raise _login_failed(self._login_fix())
            error = str(_control_response(events).get("error", ""))[:200]
            raise _preflight_leak("the Claude Code initialize response has no valid command list, so the skill inventory "
                                  + f"is unknown (exit {result.returncode}; stderr: {' '.join(result.stderr[:200].split())!r}; "
                                  + f"control response error: {error!r})")
        return commands

    def inventory(self) -> Inventory:
        """List what Claude Code loads, with no model call, and turn each foreign skill off.

        The first `initialize` request lists every command. Each name that is not built-in and not the probe
        skill gets `skillOverrides: off`. A second request must then list only the probe skill. A name that
        stays on, such as a plugin skill, stops the run with `preflight-leak`. The probe name is random for each
        inventory, so a foreign skill cannot pass as the probe.
        """
        if self._inventory is not None:
            return self._inventory
        name = _probe_name()
        with tempfile.TemporaryDirectory(prefix="skillz-inventory-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            _probe_skill(workspace, name)
            self._stage(workspace)
            builtin, listed = self._list(workspace, frozenset())
            if name not in listed:
                raise _preflight_leak(f"the staged probe skill does not load; Claude Code lists {sorted(listed)}")
            foreign = listed - {name}
            if foreign:
                _, left = self._list(workspace, foreign)
                if left != frozenset({name}):
                    raise _preflight_leak(f"skillOverrides cannot turn off {sorted(left - {name})}, "
                                          + f"or it hides the probe skill (listed: {sorted(left)})")
        self._inventory = Inventory(builtin, foreign)
        return self._inventory

    def environment_key(self) -> str:
        """Hash what the live preflight depends on besides the role fingerprint: sandbox settings and process environment.

        The key leaves out the per-process config directory, so it stays stable across processes. It also leaves
        out the skill overrides: they follow the login's skills, and every task call checks its init event.
        Under nono it also holds the nono executable and the profile shape.
        """
        environment = {name: value for name, value in self._environment(Path("/TASK")).items() if name != "CLAUDE_CONFIG_DIR"}
        isolation: dict[str, object] = {"isolation": self.isolation}
        if self.nono is not None:
            isolation |= {"nono": self.nono, "nono_sha256": hashlib.sha256(Path(self.nono).read_bytes()).hexdigest(),
                          "landlock_abi": _nono.landlock_abi(), "profile": self._profile(
                              Path("/CONFIG"), Path("/PROJECT"), Path("/PROJECT/settings.json"), environment)}
        return digest({"settings": self._floor(Path("/TASK"), ()), "environment": environment, "tools": TOOLS,
                       "skill": PROBE_PREFIX, "platform": sys.platform, "network_probe": NETWORK_PROBE, "read_probe": READ_PROBE,
                       "setting_sources": SETTING_SOURCES} | isolation)

    def _probe_sandbox(self, workspace: Path, sealed: Path) -> None:
        for name, found, fix in host_tools(self.isolation):
            if not found:
                raise SandboxUnavailable(f"{name} is missing, and the Claude Code sandbox needs it", fix)
        with listening() as port:
            script = probe(workspace, sealed, Path(__file__).resolve(), port)
            code, output, _stderr = self.sandbox(workspace, ["/usr/bin/python3", "-c", script],
                                        max(SANDBOX_SECONDS, self.options.sandbox_seconds))
        if code != 0 or output.strip() != "isolation-ok":
            raise _preflight_leak("sandbox probe failed")

    def _sealed(self, root: Path) -> tuple[Path, Path, str]:
        workspace = make_workspace(root / "workspace")
        sealed = root / "sealed"
        token = secrets.token_hex(16)
        _ = sealed.write_text(token)
        (workspace / "escape").symlink_to(sealed)
        return workspace, sealed, token

    def _check_memory(self) -> None:
        """On macOS, stop before any call when user memory in the real config directory would load."""
        memory = _user_memory(self.config_dir) if sys.platform == "darwin" else []
        if memory:
            raise _preflight_leak(f"user memory {', '.join(memory)} in the real config directory {self.config_dir} "
                                  + "would load; move it out for the run")

    def check_helper_sandbox(self) -> None:
        """Run the free helper checks: user memory and the helper sandbox probe. Neither makes a model call."""
        self._check_memory()
        with tempfile.TemporaryDirectory(prefix="skillz-preflight-") as directory:
            workspace, sealed, _token = self._sealed(Path(directory))
            self._probe_sandbox(workspace, sealed)

    def _prompt(self, sealed: Path, control_file: Path, protected: list[Path], write_control: Path, write_token: str,
                commands: list[str]) -> str:
        return (f"Run `cat {sealed}` with the Bash tool. Then run `cat {control_file}` with the Bash tool. "
                + f"Then read the file `{sealed}` with the Read tool, not Bash. "
                + "".join(f"Then run `printf {write_token} > {file}` with the Bash tool. " for file in protected)
                + f"Then run `printf {write_token} > {write_control}` with the Bash tool. "
                + "".join(f"Then run `{command}` with the Bash tool. " for command in commands)
                + "Reply with the read outputs, or the word denied for each command that fails.")

    def preflight(self) -> dict[str, object]:
        self._check_memory()
        with tempfile.TemporaryDirectory(prefix="skillz-preflight-") as directory:
            root = Path(directory)
            workspace, sealed, token = self._sealed(root)
            self._probe_sandbox(workspace, sealed)
            builtin = self.inventory().builtin
            name = _probe_name()
            _probe_skill(workspace, name)
            control = secrets.token_hex(16)
            control_file = workspace / "control.txt"
            _ = control_file.write_text(control)
            protected = [self.agents_dir(workspace) / "write-probe",
                         self.agents_dir(workspace).parent / ".claude/skills" / name / "write-probe"]
            write_control = workspace / "write-control.txt"
            write_token = secrets.token_hex(16)
            network_token = secrets.token_hex(16)
            sockets = root / "s"
            sockets.mkdir()
            probing = self.unix_sockets() or self.nono is not None
            abstract = f"skillz-{network_token[:16]}"
            with (watched() as (port, connected), watched_unix(sockets, active=probing) as (socket_path, socket_reached),
                  watched_abstract(abstract, active=probing) as abstract_reached):
                self.budget.claim()
                self.checkpoint()
                commands = network_commands(port, network_token)
                if probing:
                    commands += [unix_command(str(socket_path), network_token), unix_command("\\0" + abstract, network_token)]
                try:
                    returncode, stderr, events = self._run(
                        workspace, self._prompt(sealed, control_file, protected, write_control, write_token, commands),
                        None, min(120, self.budget.remaining()), [name])
                except Exception:
                    if connected(network_token) or socket_reached(network_token) or abstract_reached(network_token):
                        raise NetworkIsolationFailed(LISTENER_REACHED) from None
                    raise
                reached = connected(network_token)
                socket_open = socket_reached(network_token) or abstract_reached(network_token)
            wrote = any(file.exists() for file in protected)
            controlled = write_control.is_file() and write_control.read_text() == write_token
        if reached:
            raise NetworkIsolationFailed(LISTENER_REACHED)
        if socket_open:
            raise NetworkIsolationFailed("network isolation failed: the Bash probe reached a runner-owned host Unix socket")
        leak = _leak(events, [name], builtin)
        if leak is not None:
            raise _preflight_leak(leak + ("; the real config directory exposes user entries" if sys.platform == "darwin" else ""))
        reason = _failure(returncode, stderr, events, [name], builtin, nono_fix=self._nono_fix())
        if reason == AUTH_FAILED:
            raise _login_failed(self._login_fix())
        network = _network_failure(events, port, network_token)
        if network is None and probing:
            network = _unix_failure(events, str(socket_path), network_token)
        if network is None and probing:
            network = _unix_failure(events, "\\0" + abstract, network_token)
        if reason is None:
            reason = _read_tool_failure(events, token, sealed, evidence=False)
        if reason is None:
            reason = _read_failure(events, token, str(sealed), control, str(control_file))
        if reason is None:
            reason = _read_tool_failure(events, token, sealed, evidence=True)
        if reason is None:
            reason = _write_failure(events, [str(file) for file in protected], wrote, controlled)
        if reason is None and network is not None:
            raise NetworkIsolationFailed(network)
        if reason is not None:
            raise _preflight_leak(reason)
        return {"adapter": "claude", "model": self.model, "isolation": "passed", "backend": self.isolation,
                "network": "denied", "live_calls": 1,
                "unix_socket_filter": "nono" if self.nono is not None else "off" if self.unix_sockets() else "on",
                "environment_key": self.environment_key()}

    def check_name(self, candidate: Candidate) -> None:
        """Stop when the candidate name matches a Claude Code command or a foreign skill. The init event lists names
        only, so it could not show whether the candidate or the other entry loaded. No model call runs.
        """
        inventory = self.inventory()
        if candidate.skill in inventory.builtin | inventory.foreign:
            raise CodedError("candidate-name-taken", f"the candidate skill name {candidate.skill!r} matches a Claude Code "
                             + "command or another loaded skill, so the init event cannot show that the candidate "
                             + "loaded; rename the skill for the run")

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        return self._invoke(prompt, candidate, case, {}, holdout=holdout, schema=schema)

    def view(self, prompt: str, attachments: Mapping[str, bytes], *, holdout: bool = False,
             schema: dict[str, object] | None = None) -> dict[str, object]:
        """Invoke without a candidate or a case. Write each attachment under `capture/` in the workspace first.

        The prompt gets the absolute path of each attachment, so the model can open it with the Read tool.
        """
        return self._invoke(prompt, None, None, attachments, holdout=holdout, schema=schema)

    def _invoke(self, prompt: str, candidate: Candidate | None, case: Case | None, attachments: Mapping[str, bytes],
                *, holdout: bool, schema: dict[str, object] | None) -> dict[str, object]:
        self._check_memory()
        builtin = self.inventory().builtin
        if candidate is not None:
            self.check_name(candidate)
        with tempfile.TemporaryDirectory(prefix="skillz-task-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            stage_task(workspace, candidate, case)
            paths: list[str] = []
            for name, data in sorted(attachments.items()):
                path = workspace / CAPTURE_DIRECTORY / relative(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                _ = path.write_bytes(data)
                paths.append(str(path.resolve()))
            if paths:
                prompt += "\nCAPTURE FILES:\n" + "\n".join(paths)
            expected = [] if candidate is None else [candidate.skill]
            self.budget.claim(holdout=holdout)
            self.checkpoint()
            started = time.monotonic()
            returncode, stderr, events = self._run(workspace, prompt, schema or answer_schema(), self.budget.remaining(),
                                                   expected)
            final = _final(events)
            failed = bool(returncode) or final is None or final.get("is_error") is True
            loaded = _loaded_skills(events, builtin)
            if loaded is None and not failed:
                raise CodedError("isolation-failed", "Claude Code isolation fails: the stream has no init event, "
                                 + "so the skill list is unknown; the invocation counts against the budget")
            if loaded is not None:
                if set(loaded) - set(expected):
                    raise CodedError("isolation-failed", f"Claude Code isolation fails: skills in init event {loaded}, "
                                     + f"expected {expected}; the invocation counts against the budget")
                if candidate is not None and not loaded and not failed:
                    trace = _trace(events) + _token_events(cast(dict[str, object], final))
                    return {"answer": {"result_json": "", "load_marker": ""}, "events": [], "usage": usage(trace),
                            "workspace": str(workspace), "latency_seconds": time.monotonic() - started,
                            "output_files": snapshot_outputs(workspace)}
            if failed or final is None:
                reason = (_failure(returncode, stderr, events, expected, builtin, nono_fix=self._nono_fix())
                          or "missing-final-response")
                raise RuntimeError(f"Claude Code fails: {reason} (exit {returncode}); the invocation counts against the budget")
            trace = _trace(events) + _token_events(final)
            return {"answer": _answer(final), "events": trace, "usage": usage(trace), "workspace": str(workspace),
                    "latency_seconds": time.monotonic() - started, "output_files": snapshot_outputs(workspace)}

    def sandbox(self, workspace: Path, argv: list[str], seconds: int | None = None) -> tuple[int, str, str]:
        """Run `argv` in the OS sandbox: `sandbox-exec` on macOS, bubblewrap elsewhere. Fail closed without it.

        Return the exit code, the standard output, and the standard error.

        A command that exceeds `seconds` (default `options.sandbox_seconds`) returns code 124. Budget exhaustion still raises.
        """
        system = "darwin" if sys.platform == "darwin" else "linux"
        name = "sandbox-exec" if system == "darwin" else "bwrap"
        tool = shutil.which(name)
        if tool is None:
            raise SandboxUnavailable(f"{name} is unavailable", INSTALL_BUBBLEWRAP if system == "linux"
                                     else "run on a macOS host that provides `sandbox-exec`")
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(workspace / "home"),
                       "TMPDIR": str(workspace / "tmp"), "LANG": "C.UTF-8"}
        limit = self.options.sandbox_seconds if seconds is None else seconds
        timeout = min(limit, self.budget.remaining())
        capture = (Path(tempfile.mkdtemp(prefix="skillz-capture-")) if system == "linux" else None)
        try:
            try:
                result = process(sandbox_argv(system, tool, workspace.resolve(), argv, environment, self.read_roots,
                                              capture), cwd=workspace, timeout=timeout,
                                 environment={"PATH": "/usr/bin:/bin"})
            except BudgetExhausted:
                if timeout < limit:
                    raise
                return 124, "", ""
            if result.returncode != 0 and result.stderr.startswith(f"{name}:"):
                raise SandboxUnavailable(f"sandbox setup fails: {result.stderr[:200].strip()}", _sandbox_fix(result.stderr))
            return result.returncode, result.stdout, result.stderr if capture is None else _take_stderr(capture)
        finally:
            if capture is not None:
                _remove_tree(capture)

    def check_candidate(self, candidate: Candidate) -> bool:
        self.rejection = None
        self.check_name(candidate)
        with tempfile.TemporaryDirectory(prefix="skillz-contract-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            rules = resolve(candidate.contract)
            root = workspace / ".agents/skills" / rules.skill
            candidate.materialize(root)
            skill_file = root / "SKILL.md"
            if not skill_file.is_file() or not _declares(skill_file.read_text(), rules.skill):
                return False
            self.rejection = helper_failure(self, workspace, rules)
            return self.rejection is None

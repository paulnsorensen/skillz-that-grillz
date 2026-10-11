from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast, final, Literal

from skillz_experiments._cases import CodedError, loads_untrusted, mapping
from skillz_experiments._records import write_bytes
from skillz_experiments.harness._claude._constants import AUTH_FAILED, CONFIG_PREFIX, LOGIN_HINT


CLAUDE_MEMORY = ("CLAUDE.md", "CLAUDE.local.md")


CREDENTIALS = ".credentials.json"


CREDENTIAL_LIMIT = 1_000_000


def host_config_dir() -> Path:
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
        source = host_config_dir() / CREDENTIALS
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
            self.config_dir = host_config_dir()
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


def user_memory(config_dir: Path) -> list[str]:
    """Return the user memory entries in `config_dir` that Claude Code would load: `CLAUDE.md` and `rules` with `.md` files."""
    rules = config_dir / "rules"
    found = ["CLAUDE.md"] if os.path.lexists(config_dir / "CLAUDE.md") else []
    try:
        if os.path.lexists(rules) and (not rules.is_dir() or any(path.is_file() for path in rules.rglob("*.md"))):
            found.append("rules")
    except OSError as error:
        raise preflight_leak(f"the user memory directory {rules} cannot be scanned ({error.strerror or error})") from None
    return found


def preflight_leak(detail: str) -> CodedError:
    return CodedError("preflight-leak", f"Claude Code isolation preflight fails: {detail}; no unsafe fallback")


def login_failed(fix: str) -> CodedError:
    return CodedError("login-missing", f"Claude Code isolation preflight fails: {AUTH_FAILED}; no unsafe fallback; fix: {fix}")


def check_ancestors(workspace: Path) -> None:
    """Stop when Claude Code would load a `CLAUDE.md` or `CLAUDE.local.md` from an ancestor of `workspace`."""
    for directory in workspace.resolve().parents:
        for name in CLAUDE_MEMORY:
            if os.path.lexists(directory / name):
                raise preflight_leak(f"Claude Code would load {directory / name}, an ancestor of the workspace; "
                                      + "move it out, or set TMPDIR to a directory without one")

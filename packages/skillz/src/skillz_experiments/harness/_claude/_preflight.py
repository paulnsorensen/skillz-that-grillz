"""The live preflight of Claude Code: one model call checks Bash reads, writes, and network inside the sandbox.

`Preflight` is a thin friend of one `ClaudeCode` instance. It calls the private members `_run`, `_sealed`,
and `_check_memory`, and the public `late` and `inventory`.
"""
# pyright: reportImportCycles=false
from __future__ import annotations

import secrets
import sys
import tempfile
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import final, TYPE_CHECKING

from skillz_experiments._cases import CodedError
from skillz_experiments._redact import write_transcript
from skillz_experiments._runtime import SANDBOX_SECONDS, SandboxTimeout
from skillz_experiments.harness._claude._constants import AUTH_FAILED, LOGIN_HINT
from skillz_experiments.harness._claude._events import (
    assistant_text,
    failure,
    find_leak,
    probe_name,
    probe_skill,
    read_failure,
    read_tool_failure,
    tool_transcript,
    write_failure,
)
from skillz_experiments.harness._claude._login import login_failed, preflight_leak
from skillz_experiments.harness._claude._sandbox import (
    host_tools,
    network_commands,
    network_failure,
    NetworkIsolationFailed,
    SandboxUnavailable,
    unix_command,
    unix_failure,
)
from skillz_experiments.harness._isolation import listening, probe, watched, watched_abstract, watched_unix

if TYPE_CHECKING:
    from skillz_experiments.harness._claude._transport import ClaudeCode


LISTENER_REACHED = "network isolation failed: the runner-owned listener accepted a connection from the Bash probe"


# The private file in the run directory that holds the tool transcript of a failed live preflight.
PREFLIGHT_TRANSCRIPT = "preflight.events.json"


@dataclass
class _Attempt:
    """What the one live call and its listeners left behind, for the verdict steps to judge."""

    name: str
    sealed: Path
    token: str
    control: str
    control_file: Path
    protected: list[Path]
    wrote: bool
    controlled: bool
    port: int
    network_token: str
    probing: bool
    socket_path: Path
    abstract: str
    reached: bool
    socket_open: bool
    returncode: int
    stderr: str
    events: list[dict[str, object]]
    builtin: Collection[str] = field(default_factory=frozenset[str])


def _leak(reason: str | None) -> CodedError | None:
    return None if reason is None else preflight_leak(reason)


def probe_sandbox(claude: ClaudeCode, workspace: Path, sealed: Path) -> None:
    """Run the free sandbox probe in `workspace`. Raise when a host tool is missing or the probe reports a leak."""
    for name, found, fix in host_tools():
        if not found:
            raise SandboxUnavailable(f"{name} is missing, and the Claude Code sandbox needs it", fix)
    with listening() as port:
        script = probe(workspace, sealed, Path(__file__).resolve(), port)
        try:
            code, output, _stderr = claude.sandbox(workspace, ["/usr/bin/python3", "-c", script],
                                                   max(SANDBOX_SECONDS, claude.options.sandbox_seconds))
        except SandboxTimeout:
            code, output = 124, ""
    if code != 0 or output.strip() != "isolation-ok":
        raise preflight_leak("sandbox probe failed")


@final
class Preflight:
    def __init__(self, claude: ClaudeCode) -> None:
        self.claude = claude
        # The order decides which reason wins when several fire. The first check that returns an error stops the run.
        self.checks: tuple[Callable[[_Attempt], CodedError | None], ...] = (
            self._leaked, self._failed, partial(self._read_tool, evidence=False), self._read,
            partial(self._read_tool, evidence=True), self._write, self._network)

    def run(self) -> dict[str, object]:
        claude = self.claude
        claude._check_memory()  # pyright: ignore[reportPrivateUsage]
        with tempfile.TemporaryDirectory(prefix="skillz-preflight-") as directory:
            attempt = self._attempt(Path(directory))
        try:
            self._verify(attempt)
        except CodedError as error:
            self._keep_transcript(error, attempt.events)
            raise
        return {"adapter": "claude", "model": claude.model, "isolation": "passed", "credential": claude.credential_kind,
                "network": "denied", "live_calls": 1, "unix_socket_filter": "off" if claude.unix_sockets() else "on",
                "environment_key": claude.environment_key()}

    def _attempt(self, root: Path) -> _Attempt:
        claude = self.claude
        workspace, sealed, token = claude._sealed(root)  # pyright: ignore[reportPrivateUsage]
        probe_sandbox(claude, workspace, sealed)
        _ = claude.inventory()
        name = probe_name()
        probe_skill(workspace, name)
        control = secrets.token_hex(16)
        control_file = workspace / "control.txt"
        _ = control_file.write_text(control)
        protected = [workspace / ".agents/write-probe", workspace / ".claude/skills" / name / "write-probe"]
        write_control = workspace / "write-control.txt"
        write_token = secrets.token_hex(16)
        network_token = secrets.token_hex(16)
        sockets = root / "s"
        sockets.mkdir()
        probing = claude.unix_sockets()
        abstract = f"skillz-{network_token[:16]}"
        with (watched() as (port, connected), watched_unix(sockets, active=probing) as (socket_path, socket_reached),
              watched_abstract(abstract, active=probing) as abstract_reached):
            if claude.out is not None:
                (claude.out / PREFLIGHT_TRANSCRIPT).unlink(missing_ok=True)
            claude.budget.claim(role="preflight")
            claude.checkpoint()
            commands = network_commands(port, network_token)
            if probing:
                commands += [unix_command(str(socket_path), network_token), unix_command("\\0" + abstract, network_token)]
            try:
                returncode, stderr, events = claude._run(  # pyright: ignore[reportPrivateUsage]
                    workspace, self._prompt(sealed, control_file, protected, write_control, write_token, commands),
                    None, [name])
            except Exception:
                if connected(network_token) or socket_reached(network_token) or abstract_reached(network_token):
                    raise NetworkIsolationFailed(LISTENER_REACHED) from None
                raise
            reached = connected(network_token)
            socket_open = socket_reached(network_token) or abstract_reached(network_token)
        wrote = any(file.exists() for file in protected)
        controlled = write_control.is_file() and write_control.read_text() == write_token
        return _Attempt(name, sealed, token, control, control_file, protected, wrote, controlled, port, network_token,
                        probing, socket_path, abstract, reached, socket_open, returncode, stderr, events)

    def _prompt(self, sealed: Path, control_file: Path, protected: list[Path], write_control: Path, write_token: str,
                commands: list[str]) -> str:
        return (f"Run `cat {sealed}` with the Bash tool. Then run `cat {control_file}` with the Bash tool. "
                + f"Then read the file `{sealed}` with the Read tool, not Bash. "
                + "".join(f"Then run `printf {write_token} > {file}` with the Bash tool. " for file in protected)
                + f"Then run `printf {write_token} > {write_control}` with the Bash tool. "
                + "".join(f"Then run `{command}` with the Bash tool. " for command in commands)
                + "Reply with the read outputs, or the word denied for each command that fails.")

    def _verify(self, attempt: _Attempt) -> None:
        """Raise the first error of the ordered verdict. The relist for a late built-in skill runs after the listener and socket checks."""
        claude = self.claude
        if attempt.reached:
            raise NetworkIsolationFailed(LISTENER_REACHED)
        if attempt.socket_open:
            raise NetworkIsolationFailed("network isolation failed: the Bash probe reached a runner-owned host Unix socket")
        attempt.builtin = claude.late.adopt(attempt.events, [attempt.name], claude.inventory(), claude.declared).builtin
        for check in self.checks:
            error = check(attempt)
            if error is not None:
                raise error

    def _leaked(self, attempt: _Attempt) -> CodedError | None:
        leak = find_leak(attempt.events, [attempt.name], attempt.builtin, self.claude.declared)
        if leak is None:
            return None
        return preflight_leak(leak + ("; the real config directory exposes user entries" if sys.platform == "darwin" else ""))

    def _failed(self, attempt: _Attempt) -> CodedError | None:
        reason = failure(attempt.returncode, attempt.stderr, attempt.events, [attempt.name], attempt.builtin,
                         allowed=self.claude.declared.skills)
        return login_failed(LOGIN_HINT) if reason == AUTH_FAILED else _leak(reason)

    def _read_tool(self, attempt: _Attempt, *, evidence: bool) -> CodedError | None:
        return _leak(read_tool_failure(attempt.events, attempt.token, attempt.sealed, evidence=evidence))

    def _read(self, attempt: _Attempt) -> CodedError | None:
        return _leak(read_failure(attempt.events, attempt.token, str(attempt.sealed), attempt.control,
                                  str(attempt.control_file)))

    def _write(self, attempt: _Attempt) -> CodedError | None:
        return _leak(write_failure(attempt.events, [str(file) for file in attempt.protected], attempt.wrote,
                                   attempt.controlled))

    def _network(self, attempt: _Attempt) -> CodedError | None:
        network = network_failure(attempt.events, attempt.port, attempt.network_token)
        if network is None and attempt.probing:
            network = unix_failure(attempt.events, str(attempt.socket_path), attempt.network_token)
        if network is None and attempt.probing:
            network = unix_failure(attempt.events, "\\0" + attempt.abstract, attempt.network_token)
        return None if network is None else NetworkIsolationFailed(network)

    def _keep_transcript(self, error: CodedError, events: list[dict[str, object]]) -> None:
        """Write the redacted tool transcript of a failed live preflight to the run directory, and name it in `error`.

        The file is private (mode 0600). Without a run directory, as in `--preflight-only`, nothing is written. A failed
        write keeps the original stop and code, and the message says that the transcript is missing.
        """
        if self.claude.out is None:
            return
        path = self.claude.out / PREFLIGHT_TRANSCRIPT
        try:
            write_transcript(path, {"schema_version": 1, "role": self.claude.role, "failure": str(error),
                                    "tools": tool_transcript(events), "answer": assistant_text(events)})
        except OSError as failure:
            error.args = (f"{error}; the preflight transcript could not be written: {failure.strerror}",)
            return
        # The run records and prints `str(error)`, so the stop names the transcript through the message.
        error.args = (f"{error}; the preflight transcript is in {path}",)

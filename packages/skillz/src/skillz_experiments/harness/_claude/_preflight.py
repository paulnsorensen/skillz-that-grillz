"""The live preflight of Claude Code: two model calls check Bash reads, writes, network, and the Read tool.

`Preflight` is a thin friend of one `ClaudeCode` instance. It calls the private members `_run`, `_sealed`,
and `_check_memory`, and the public `late` and `inventory`.
"""
# pyright: reportImportCycles=false
from __future__ import annotations

import secrets
import shutil
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
from skillz_experiments.harness._claude._constants import AUTH_FAILED, HOST_FILE, LINK, LOGIN_HINT
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
from skillz_experiments.harness._claude._pairs import tool_pairs
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

# The two preflight calls of each role. A live sonnet role refused one long prompt that mixed the Read steps with the
# Bash sweep as a sandbox-escape test, so each call has one plain task and the prompts avoid words such as "sealed".
SANDBOX_CHECK = "Bash sandbox check"
READ_CHECK = "file-access check"
SANDBOX_INTRO = ("This is a check of the Bash sandbox in this workspace. Run each command below with the Bash tool, "
                 + "exactly as written and in the given order. The sandbox blocks some of these commands, so a failed "
                 + "command is a normal result: continue with the next command. Do not change a command, and do not "
                 + "use another tool. ")
READ_INTRO = ("This is a file-access check for this workspace. Open each file below with the Read tool, one Read call "
              + "per file, in the given order. Some files are outside the workspace, so the Read tool can refuse them, "
              + "and a refusal is a normal result: continue with the next file. Do not use another tool. ")


@dataclass(frozen=True)
class _Call:
    """One live preflight call: the check it runs, the tool that check needs, and what the call returned."""

    check: str
    tool: str
    returncode: int
    stderr: str
    events: list[dict[str, object]]


@dataclass
class _Attempt:
    """What the live calls and their listeners left behind, for the verdict steps to judge."""

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
    calls: list[_Call]
    builtin: Collection[str] = field(default_factory=frozenset[str])

    @property
    def events(self) -> list[dict[str, object]]:
        """Return the events of every call in call order. A leak in either call fails the checks that read them."""
        return [event for call in self.calls for event in call.events]


def _leak(reason: str | None) -> CodedError | None:
    return None if reason is None else preflight_leak(reason)


def probe_sandbox(claude: ClaudeCode, workspace: Path, sealed: Path) -> None:
    """Run the free sandbox probe in `workspace`. Raise when a host tool is missing or the probe reports a leak."""
    for name, found, fix in host_tools():
        if not found:
            raise SandboxUnavailable(f"{name} is missing, and the Claude Code sandbox needs it", fix)
    with listening() as port:
        script = probe(workspace, sealed, Path(__file__).resolve(), port, link=LINK)
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
        # A confirmed leak in either call wins over a call that made no tool call.
        self.checks: tuple[Callable[[_Attempt], CodedError | None], ...] = (
            self._leaked, self._failed, partial(self._read_tool, evidence=False), partial(self._refused, tool="Bash"),
            self._read, self._write, self._network, partial(self._refused, tool="Read"),
            partial(self._read_tool, evidence=True))

    def run(self) -> dict[str, object]:
        claude = self.claude
        claude._check_memory()  # pyright: ignore[reportPrivateUsage]
        if not Path(HOST_FILE).is_file():
            raise CodedError("host-not-ready", f"the Read-tool probe needs the host file {HOST_FILE}, and this host has "
                             + "none; no model call ran")
        with tempfile.TemporaryDirectory(prefix="skillz-preflight-") as directory:
            attempt = self._attempt(Path(directory))
        try:
            self._verify(attempt)
        except CodedError as error:
            self._keep_transcript(error, attempt.events)
            raise
        return {"adapter": "claude", "model": claude.model, "isolation": "passed", "credential": claude.credential_kind,
                "network": "denied", "live_calls": len(attempt.calls),
                "unix_socket_filter": "off" if claude.unix_sockets() else "on",
                "read_tool": {"host_file": HOST_FILE, "result": "refused"}, "environment_key": claude.environment_key()}

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
        protected = [workspace / ".agents/write-check.txt", workspace / ".claude/skills" / name / "write-check.txt"]
        write_control = workspace / "write-control.txt"
        write_token = secrets.token_hex(16)
        network_token = secrets.token_hex(16)
        sockets = root / "s"
        sockets.mkdir()
        probing = claude.unix_sockets()
        abstract = f"skillz-{network_token[:16]}"
        calls: list[_Call] = []
        with (watched() as (port, connected), watched_unix(sockets, active=probing) as (socket_path, socket_reached),
              watched_abstract(abstract, active=probing) as abstract_reached):
            if claude.out is not None:
                (claude.out / PREFLIGHT_TRANSCRIPT).unlink(missing_ok=True)
            commands = network_commands(port, network_token)
            if probing:
                commands += [unix_command(str(socket_path), network_token), unix_command("\\0" + abstract, network_token)]
            try:
                calls.append(self._call(workspace, SANDBOX_CHECK, "Bash", self._sandbox_prompt(
                    sealed, control_file, protected, write_control, write_token, commands), name))
                # Each call stages the project skills again, so keep the write evidence of the Bash call first.
                wrote = any(file.exists() for file in protected)
                shutil.rmtree(workspace / ".claude/skills")
                calls.append(self._call(workspace, READ_CHECK, "Read", self._read_prompt(sealed, control_file), name))
            except Exception:
                if connected(network_token) or socket_reached(network_token) or abstract_reached(network_token):
                    raise NetworkIsolationFailed(LISTENER_REACHED) from None
                raise
            reached = connected(network_token)
            socket_open = socket_reached(network_token) or abstract_reached(network_token)
        wrote = wrote or any(file.exists() for file in protected)
        controlled = write_control.is_file() and write_control.read_text() == write_token
        return _Attempt(name, sealed, token, control, control_file, protected, wrote, controlled, port, network_token,
                        probing, socket_path, abstract, reached, socket_open, calls)

    def _call(self, workspace: Path, check: str, tool: str, prompt: str, name: str) -> _Call:
        """Claim one preflight call from the budget, then run it. Each call is a separate live model call."""
        claude = self.claude
        claude.budget.claim(role="preflight")
        claude.checkpoint()
        returncode, stderr, events = claude._run(workspace, prompt, None, [name])  # pyright: ignore[reportPrivateUsage]
        return _Call(check, tool, returncode, stderr, events)

    def _sandbox_prompt(self, outside: Path, control_file: Path, protected: list[Path], write_control: Path,
                        write_token: str, commands: list[str]) -> str:
        """Return the prompt of the Bash call: the reads, the writes, and the network commands, in that order.

        A live model refused one long prompt that mixed these steps with the Read-tool steps. So this call names one
        tool and one plain task, and it says that a blocked command is a normal result.
        """
        return (SANDBOX_INTRO
                + f"Run `cat {outside}` with the Bash tool. Run `cat {control_file}` with the Bash tool. "
                + "".join(f"Run `printf {write_token} > {file}` with the Bash tool. " for file in protected)
                + f"Run `printf {write_token} > {write_control}` with the Bash tool. "
                + "".join(f"Run `{command}` with the Bash tool. " for command in commands)
                + "Then reply with one line per command: its output, or the word failed.")

    def _read_prompt(self, outside: Path, control_file: Path) -> str:
        """Return the prompt of the Read call. The verdict needs each of the four Read calls.

        The Read tool must refuse the file outside the workspace, `HOST_FILE`, and the workspace symlink to the outside
        file. It must read the control file and return its token.
        """
        files = [outside, Path(HOST_FILE), control_file.parent / LINK, control_file]
        return (READ_INTRO + "".join(f"Read the file `{file}` with the Read tool. " for file in files)
                + "Then reply with one line per file: its text, or the word refused.")

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
        for call in attempt.calls:
            reason = failure(call.returncode, call.stderr, call.events, [attempt.name], attempt.builtin,
                             allowed=self.claude.declared.skills)
            if reason is not None:
                return login_failed(LOGIN_HINT) if reason == AUTH_FAILED else preflight_leak(reason)
        return None

    def _refused(self, attempt: _Attempt, *, tool: str) -> CodedError | None:
        """Fail closed when the call of `tool` made no call of that tool. A refusal is not a confirmed leak."""
        for call in attempt.calls:
            if call.tool == tool and not any(True for _pair in tool_pairs(call.events, tool)):
                return preflight_leak(f"the {call.check} has no evidence: the model made no {tool} tool call, so it "
                                      + "refused or skipped the check; this is not a confirmed leak")
        return None

    def _read_tool(self, attempt: _Attempt, *, evidence: bool) -> CodedError | None:
        return _leak(read_tool_failure(attempt.events, attempt.token, attempt.sealed, attempt.control_file.parent / LINK,
                                       attempt.control, attempt.control_file, evidence=evidence))

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

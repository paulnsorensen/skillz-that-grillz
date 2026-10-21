from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Collection, Mapping
from pathlib import Path, PurePosixPath
from typing import cast, ClassVar, final

from skillz_experiments._candidate import Candidate, make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import Case, CodedError, digest, relative, string
from skillz_experiments._contract import resolve, Surface
from skillz_experiments.evaluation import answer_schema, CheckResult, helper_failure, Reply, Transcript, usage
from skillz_experiments._graders import CAPTURE_DIRECTORY
from skillz_experiments._budget import Budget, BudgetExhausted
from skillz_experiments._redact import write_transcript
from skillz_experiments._runtime import SANDBOX_SECONDS, SandboxTimeout, live, model_call, process, sandboxed
from skillz_experiments.harness._check import (
    Check,
    credential_row,
    failed,
    MISSING_CREDENTIAL,
    SUBSCRIPTION_LOGIN,
    UNVERIFIED_CREDENTIAL,
)
from skillz_experiments.harness._isolation import listening, probe, watched, watched_abstract, watched_unix
from skillz_experiments.harness._claude._constants import (
    AUTH_FAILED,
    INSTALL_BUBBLEWRAP,
    LOGIN_HINT,
    NOTICE_CODES,
    PROBE_PREFIX,
    SANDBOX_STDERR,
)
from skillz_experiments.harness._claude._keys import restore, safe_keys
from skillz_experiments.harness._claude._events import (
    answer,
    assistant_text,
    declared_commands,
    control_response,
    declares,
    parse_events,
    failure,
    final_event,
    init_inventory,
    is_result,
    find_leak,
    loaded_skills,
    marker_seen,
    not_loaded,
    probe_name,
    probe_skill,
    read_failure,
    read_tool_failure,
    token_events,
    tool_transcript,
    event_trace,
    user_line,
    write_failure,
)
from skillz_experiments.harness._claude._login import (
    check_ancestors,
    login_failed,
    preflight_leak,
    user_memory,
    ClaudeLogin,
    login_credential_kind,
)
from skillz_experiments.harness._claude._options import ClaudeOptions, resolve_read_roots
from skillz_experiments.harness._claude._sandbox import (
    network_failure,
    remove_tree,
    sandbox_fix,
    unix_failure,
    host_tools,
    nested_userns_blocked,
    network_commands,
    NetworkIsolationFailed,
    sandbox_argv,
    SandboxUnavailable,
    unix_command,
    INSTALL_SOCAT,
    USERNS_FIX,
)
from skillz_experiments.harness._claude._late import LateBuiltins
from skillz_experiments.harness._claude._settings import declared_names, Inventory, settings


TOOLS = "Bash,Read,Skill"


NETWORK_PROBE = "loopback-dns-nonloopback-v3"


READ_PROBE = "read-tool-outside-workspace-v1"


LISTENER_REACHED = "network isolation failed: the runner-owned listener accepted a connection from the Bash probe"


SANDBOX_STDERR_LIMIT = 100_000


# Settings sources that Claude Code reads besides `--settings`. `project` keeps the staged project skill and drops user
# settings, user skills, and plugins. `--restricted` would also drop the project skill, so the runner does not use it.
SETTING_SOURCES = "project"


INVENTORY_REQUEST = json.dumps({"type": "control_request", "request_id": "skillz-inventory",
                                "request": {"subtype": "initialize"}}) + "\n"


INVENTORY_SECONDS = 60


MCP_FILE = "mcp.json"


# The private file in the run directory that holds the tool transcript of a failed live preflight.
PREFLIGHT_TRANSCRIPT = "preflight.events.json"


_NO_INIT = "has no init event, so the skill list is unknown; the invocation counts against the budget"


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


@final
class ClaudeCode:
    claude_sandbox: ClassVar[bool] = True
    refuses_claude_options: ClassVar[bool] = False
    role: str = "task"

    def __init__(self, model: str, budget: Budget, checkpoint: Callable[[], None], executable: Path | None = None,
                 out: Path | None = None, login: ClaudeLogin | None = None, options: ClaudeOptions | None = None,
                 surface: Surface | None = None, files: Mapping[str, str] | None = None) -> None:
        """`surface` is the contract's extra tool surface, and `files` are the skill files that it names."""
        self.surface = surface
        self.declared = declared_names(surface, files)
        self.mcp_seed = (files or {}).get(surface.mcp_config) if surface is not None and surface.mcp_config is not None else None
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
        self.login = ClaudeLogin() if login is None else login
        self.credential = self.login.credential
        self.config_dir = self.login.config_dir
        self._inventory: Inventory | None = None

        def builtin_listing(workspace: Path) -> frozenset[str]:
            self._stage(workspace)
            return self._list(workspace, frozenset())[0]

        self._late = LateBuiltins(builtin_listing)
        self._unix_sockets: bool | None = None

    @property
    def credential_kind(self) -> str:
        """Name the credential kind. A macOS Keychain login has no file, so the runner cannot verify it."""
        return SUBSCRIPTION_LOGIN if self.credential is not None else UNVERIFIED_CREDENTIAL

    @classmethod
    def host_checks(cls, executable: str | None = None) -> list[Check]:
        """Check the Claude Code host: tools, login, sandbox, and skill inventory. `executable` names a configured role's."""
        return [*_claude_checks(executable), Check("live-isolation", "needs-live", LIVE_PREFLIGHT)]

    def close(self) -> None:
        """Close the login when this role created it. The harness closes a login that it shares."""
        if self.owns_login:
            self.login.close()

    def unix_sockets(self) -> bool:
        """Return True when the host blocks the nested user namespace of Claude Code's Unix-socket filter.

        The settings then skip that filter. The filesystem and domain rules stay on, and the live preflight
        probes a host Unix socket instead.
        """
        if self._unix_sockets is None:
            self._unix_sockets = nested_userns_blocked()
        return self._unix_sockets

    def _floor(self, workspace: Path, overrides: Collection[str]) -> dict[str, object]:
        floor = settings(workspace, self.out, overrides=overrides, unix_sockets=self.unix_sockets())
        if self.declared.servers:
            permissions = cast(dict[str, object], floor["permissions"])
            floor["permissions"] = permissions | {"allow": [*cast(list[str], permissions["allow"]),
                                                            *(f"mcp__{name}" for name in sorted(self.declared.servers))]}
        return floor

    def _environment(self, workspace: Path) -> dict[str, str]:
        environment = _executable_environment(self.executable) | {"HOME": str(workspace / "home"), "TMPDIR": str(workspace / "tmp")}
        return environment | {"CLAUDE_CONFIG_DIR": str(self.config_dir)}

    def _stage(self, workspace: Path, candidate: Candidate | None = None) -> None:
        """Copy the staged skills to the project skill directory that Claude Code loads from.

        A contract surface adds the candidate's agents to `.claude/agents` and its companions to `.claude/skills`;
        companion files and directories are read-only.
        """
        _ = shutil.copytree(workspace / ".agents/skills", workspace / ".claude/skills")
        if self.surface is None or candidate is None:
            return
        for path in self.surface.agents:
            agent = workspace / ".claude/agents" / PurePosixPath(path).name
            agent.parent.mkdir(parents=True, exist_ok=True)
            _ = agent.write_text(candidate.files[path], encoding="utf-8")
        for root in self.surface.companions:
            target = workspace / ".claude/skills" / PurePosixPath(root).name
            files = {name: text.encode("utf-8") for name, text in candidate.files.items()} | candidate.frozen
            for name, data in files.items():
                if name.startswith(root + "/"):
                    file = target / name.removeprefix(root + "/")
                    file.parent.mkdir(parents=True, exist_ok=True)
                    _ = file.write_bytes(data)
                    file.chmod(0o555 if name.endswith(".pyz") else 0o444)
            for directory in sorted((path for path in target.rglob("*") if path.is_dir()), reverse=True):
                directory.chmod(0o555)
            target.chmod(0o555)

    def _tools(self) -> str:
        """Return the `--tools` value: the base tools, then each surface tool that is not already there."""
        base = TOOLS.split(",")
        return ",".join([*base, *(tool for tool in (self.surface.tools if self.surface else ()) if tool not in base)])

    def _base(self, settings_path: Path, mcp_path: Path | None = None) -> list[str]:
        return [str(self.executable), "-p", "--setting-sources", SETTING_SOURCES, "--no-session-persistence",
                "--tools", self._tools(), "--strict-mcp-config",
                *([] if mcp_path is None else ["--mcp-config", str(mcp_path)]),
                "--settings", str(settings_path), "--output-format", "stream-json", "--verbose"]

    def _command(self, settings_path: Path, schema: dict[str, object] | None, mcp_path: Path | None = None) -> list[str]:
        command = [*self._base(settings_path, mcp_path), "--model", self.model,
                   *(["--effort", self.options.effort] if self.options.effort is not None else [])]
        return command + (["--json-schema", json.dumps(schema)] if schema is not None else [])

    def _start(self, workspace: Path, schema: dict[str, object] | None, expected: list[str],
               candidate: Candidate | None = None, *, stream: bool = False) -> tuple[list[str], dict[str, str]]:
        """Stage the workspace and return the command and environment. `stream` adds the stream-json input format."""
        check_ancestors(workspace)
        if candidate is not None and declared_names(self.surface, candidate.files) != self.declared:
            raise CodedError("contract-surface", "the candidate files declare different agents, servers, or companions "
                             + "than the seed files; the contract surface is fixed for the run")
        if (candidate is not None and self.surface is not None and self.surface.mcp_config is not None
                and candidate.files[self.surface.mcp_config] != self.mcp_seed):
            raise CodedError("contract-surface", f"the candidate changes the MCP config {self.surface.mcp_config}; "
                             + "its server commands run on the host, so the seed text is fixed for the run")
        self._stage(workspace, candidate)
        settings_path = workspace.parent / "settings.json"
        _ = settings_path.write_text(json.dumps(self._floor(workspace, self.inventory().foreign - set(expected))))
        mcp_path = None
        if self.surface is not None and self.surface.mcp_config is not None and candidate is not None:
            mcp_path = workspace.parent / MCP_FILE
            _ = mcp_path.write_text(candidate.files[self.surface.mcp_config], encoding="utf-8")
        argv = self._command(settings_path, schema, mcp_path) + (["--input-format", "stream-json"] if stream else [])
        return argv, self._environment(workspace)

    def _run(self, workspace: Path, prompt: str, schema: dict[str, object] | None,
             expected: list[str], candidate: Candidate | None = None) -> tuple[int, str, list[dict[str, object]]]:
        command, environment = self._start(workspace, schema, expected, candidate)
        result = model_call(self.budget, command, cwd=workspace, environment=environment, input_text=prompt)
        return result.returncode, result.stderr, parse_events(result.stdout)

    def _list(self, workspace: Path, overrides: frozenset[str]) -> tuple[frozenset[str], frozenset[str]]:
        """Send one `initialize` control request and return the built-in and other command names. No model turn runs."""
        check_ancestors(workspace)
        settings_path = workspace.parent / "inventory-settings.json"
        _ = settings_path.write_text(json.dumps(self._floor(workspace, overrides)))
        command, environment = [*self._base(settings_path), "--input-format", "stream-json"], self._environment(workspace)
        timeout = min(INVENTORY_SECONDS, self.budget.remaining())
        try:
            result = process(command, cwd=workspace, timeout=timeout, environment=environment, input_text=INVENTORY_REQUEST)
        except BudgetExhausted:
            if timeout < INVENTORY_SECONDS:
                raise
            raise preflight_leak(f"the Claude Code skill inventory timed out after {INVENTORY_SECONDS} seconds") from None
        events = parse_events(result.stdout)
        commands = declared_commands(events)
        if commands is None:
            reason = failure(result.returncode, result.stderr, events, [])
            if reason == AUTH_FAILED:
                raise login_failed(LOGIN_HINT)
            response = control_response(events)
            if result.returncode != 0 and not response:
                raise CodedError("host-not-ready", f"the Claude Code initialize request exited {result.returncode} with no control "
                                 + f"response, so the skill inventory is unknown (stderr: {' '.join(result.stderr[:200].split())!r}); "
                                 + "fix the claude executable, then run again")
            error = str(response.get("error", ""))[:200]
            raise preflight_leak("the Claude Code initialize response has no valid command list, so the skill inventory "
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
        name = probe_name()
        with tempfile.TemporaryDirectory(prefix="skillz-inventory-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            probe_skill(workspace, name)
            self._stage(workspace)
            builtin, listed = self._list(workspace, frozenset())
            if name not in listed:
                raise preflight_leak(f"the staged probe skill does not load; Claude Code lists {sorted(listed)}")
            foreign = listed - {name}
            if foreign:
                _, left = self._list(workspace, foreign)
                if left != frozenset({name}):
                    raise preflight_leak(f"skillOverrides cannot turn off {sorted(left - {name})}, "
                                          + f"or it hides the probe skill (listed: {sorted(left)})")
        self._inventory = Inventory(builtin, foreign)
        return self._inventory

    def environment_key(self) -> str:
        """Hash what the live preflight depends on besides the role fingerprint: sandbox settings and process environment.

        The key leaves out the run's temporary config directory, so it stays stable across processes. It also leaves
        out the skill overrides: they follow the login's skills, and every task call checks its init event.
        """
        environment = {name: value for name, value in self._environment(Path("/TASK")).items() if name != "CLAUDE_CONFIG_DIR"}
        surface: dict[str, object] = {} if self.surface is None else {
            "surface": self.surface.data() | {"servers": sorted(self.declared.servers)}}
        return digest({"settings": self._floor(Path("/TASK"), ()), "environment": environment, "tools": TOOLS,
                       "skill": PROBE_PREFIX, "platform": sys.platform, "network_probe": NETWORK_PROBE, "read_probe": READ_PROBE,
                       "setting_sources": SETTING_SOURCES, "isolation": "claude"} | surface)

    def reuse_key(self, fingerprint: object) -> str | None:
        """Return the key under which a live pass of this role stays valid: the role fingerprint and the environment key."""
        return digest({"fingerprint": fingerprint, "environment": self.environment_key()})

    def resume(self, kept: object) -> bool:
        """Return whether the recorded pass `kept` holds. A held pass still runs the free sandbox probe."""
        if not (isinstance(kept, dict) and cast(dict[str, object], kept).get("isolation") == "passed"):
            return False
        self.check_helper_sandbox()
        _ = self.inventory()
        return True

    def run_facts(self, evidence: Mapping[str, object]) -> dict[str, object]:
        """Report no run-record fact for Claude Code."""
        del evidence
        return {}

    def adopt_login(self) -> ClaudeLogin | None:
        """Give the login of this role to the run, which then closes it once."""
        self.owns_login = False
        return self.login

    def _probe_sandbox(self, workspace: Path, sealed: Path) -> None:
        for name, found, fix in host_tools():
            if not found:
                raise SandboxUnavailable(f"{name} is missing, and the Claude Code sandbox needs it", fix)
        with listening() as port:
            script = probe(workspace, sealed, Path(__file__).resolve(), port)
            try:
                code, output, _stderr = self.sandbox(workspace, ["/usr/bin/python3", "-c", script],
                                                     max(SANDBOX_SECONDS, self.options.sandbox_seconds))
            except SandboxTimeout:
                code, output = 124, ""
        if code != 0 or output.strip() != "isolation-ok":
            raise preflight_leak("sandbox probe failed")

    def _sealed(self, root: Path) -> tuple[Path, Path, str]:
        workspace = make_workspace(root / "workspace")
        sealed = root / "sealed"
        token = secrets.token_hex(16)
        _ = sealed.write_text(token)
        (workspace / "escape").symlink_to(sealed)
        return workspace, sealed, token

    def _check_memory(self) -> None:
        """On macOS, stop before any call when user memory in the real config directory would load."""
        memory = user_memory(self.config_dir) if sys.platform == "darwin" else []
        if memory:
            raise preflight_leak(f"user memory {', '.join(memory)} in the real config directory {self.config_dir} "
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
            _ = self.inventory()
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
            probing = self.unix_sockets()
            abstract = f"skillz-{network_token[:16]}"
            with (watched() as (port, connected), watched_unix(sockets, active=probing) as (socket_path, socket_reached),
                  watched_abstract(abstract, active=probing) as abstract_reached):
                if self.out is not None:
                    (self.out / PREFLIGHT_TRANSCRIPT).unlink(missing_ok=True)
                self.budget.claim(role="preflight")
                self.checkpoint()
                commands = network_commands(port, network_token)
                if probing:
                    commands += [unix_command(str(socket_path), network_token), unix_command("\\0" + abstract, network_token)]
                try:
                    returncode, stderr, events = self._run(
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
        try:
            self._inventory = self._late.adopt(events, [name], self.inventory(), self.declared)
            builtin = self._inventory.builtin
            if reached:
                raise NetworkIsolationFailed(LISTENER_REACHED)
            if socket_open:
                raise NetworkIsolationFailed("network isolation failed: the Bash probe reached a runner-owned host Unix socket")
            leak = find_leak(events, [name], builtin, self.declared)
            if leak is not None:
                raise preflight_leak(leak + ("; the real config directory exposes user entries" if sys.platform == "darwin" else ""))
            reason = failure(returncode, stderr, events, [name], builtin, allowed=self.declared.skills)
            if reason == AUTH_FAILED:
                raise login_failed(LOGIN_HINT)
            network = network_failure(events, port, network_token)
            if network is None and probing:
                network = unix_failure(events, str(socket_path), network_token)
            if network is None and probing:
                network = unix_failure(events, "\\0" + abstract, network_token)
            if reason is None:
                reason = read_tool_failure(events, token, sealed, evidence=False)
            if reason is None:
                reason = read_failure(events, token, str(sealed), control, str(control_file))
            if reason is None:
                reason = read_tool_failure(events, token, sealed, evidence=True)
            if reason is None:
                reason = write_failure(events, [str(file) for file in protected], wrote, controlled)
            if reason is None and network is not None:
                raise NetworkIsolationFailed(network)
            if reason is not None:
                raise preflight_leak(reason)
        except CodedError as error:
            self._keep_transcript(error, events)
            raise
        return {"adapter": "claude", "model": self.model, "isolation": "passed", "credential": self.credential_kind,
                "network": "denied", "live_calls": 1, "unix_socket_filter": "off" if self.unix_sockets() else "on",
                "environment_key": self.environment_key()}

    def _keep_transcript(self, error: CodedError, events: list[dict[str, object]]) -> None:
        """Write the redacted tool transcript of a failed live preflight to the run directory, and name it in `error`.

        The file is private (mode 0600). Without a run directory, as in `--preflight-only`, nothing is written. A failed
        write keeps the original stop and code, and the message says that the transcript is missing.
        """
        if self.out is None:
            return
        path = self.out / PREFLIGHT_TRANSCRIPT
        try:
            write_transcript(path, {"schema_version": 1, "role": self.role, "failure": str(error),
                                    "tools": tool_transcript(events), "answer": assistant_text(events)})
        except OSError as failure:
            error.args = (f"{error}; the preflight transcript could not be written: {failure.strerror}",)
            return
        # The run records and prints `str(error)`, so the stop names the transcript through the message.
        error.args = (f"{error}; the preflight transcript is in {path}",)

    def check_name(self, candidate: Candidate) -> None:
        """Stop when the candidate name matches a Claude Code command or a foreign skill. The init event lists names
        only, so it could not show whether the candidate or the other entry loaded. No model call runs.
        """
        inventory = self.inventory()
        taken = inventory.builtin | inventory.foreign
        if self.declared.skills & taken:
            raise CodedError("candidate-name-taken", f"the contract companion skills {sorted(self.declared.skills & taken)} "
                             + "match a Claude Code command or another loaded skill; rename them for the run")
        if candidate.skill in taken:
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
        _ = self.inventory()
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
            self.budget.claim(holdout=holdout, role=self.role)
            self.checkpoint()
            started = time.monotonic()
            sent, names = safe_keys(schema or answer_schema())
            with self.budget.timed(self.role):
                returncode, stderr, events = self._run(workspace, prompt, sent, expected, candidate)
                self._inventory = self._late.adopt(events, expected, self.inventory(), self.declared)
                final, unloaded = self._verdict(events, bool(returncode), returncode, stderr, expected,
                                                self._inventory.builtin)
            trace = event_trace(events) + token_events(final)
            if unloaded:
                return not_loaded(workspace, started, trace)
            return {"answer": restore(answer(final), names), "events": trace, "usage": usage(trace), "workspace": str(workspace),
                    "latency_seconds": time.monotonic() - started, "output_files": snapshot_outputs(workspace)}

    def converse(self, request: str, candidate: Candidate, case: Case, holdout: bool,
                 simulate: Callable[[Transcript], Reply]) -> dict[str, object]:
        """Run a journey on one live process. Send `request`, then each simulator reply after a `result` event.

        Stop when the simulator is done, at `max_turns`, or when the process exits. Each agent turn claims one call.
        Stop after turn 1 when its init event lacks the candidate, and return the not-loaded result.
        A later init event that lists other skills or tools than the first one stops the case with `isolation-failed`.
        The workspace and config directory live for the whole journey.
        """
        if case.journey is None:
            raise ValueError("converse needs a journey case")
        self._check_memory()
        builtin = self.inventory().builtin
        self.check_name(candidate)
        expected = [candidate.skill]
        events: list[dict[str, object]] = []
        transcript: Transcript = []
        first: tuple[list[str], ...] | None = None
        with tempfile.TemporaryDirectory(prefix="skillz-journey-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            stage_task(workspace, candidate, case)
            command, environment = self._start(workspace, None, expected, candidate, stream=True)
            started = time.monotonic()
            message, shown = request, case.request
            closed = False
            with live(command, cwd=workspace, environment=environment) as child:
                for turn in range(case.journey.max_turns):
                    self.budget.claim(holdout=holdout, role=self.role)
                    self.checkpoint()
                    with self.budget.timed(self.role):
                        lines, ended = child.turn(user_line(message), self.budget.remaining(), is_result)
                    fresh = parse_events("".join(lines))
                    events += fresh
                    self._inventory = self._late.adopt(fresh, expected, self.inventory(), self.declared)
                    builtin = self._inventory.builtin
                    for init in (event for event in fresh if event.get("type") == "system" and event.get("subtype") == "init"):
                        if first is None:
                            first = init_inventory(init)
                        elif init_inventory(init) != first:
                            raise CodedError("isolation-failed", "Claude Code isolation fails: the skill, tool, agent, MCP server, or plugin inventory "
                                             + f"changed in turn {turn + 1}; the invocation counts against the budget")
                    if any(event.get("type") == "result" and event.get("is_error") is True for event in fresh):
                        raise RuntimeError(f"Claude Code fails: error-result (turn {turn + 1} reports an error result); "
                                           + "the invocation counts against the budget")
                    if not ended:
                        break
                    if first is None:
                        raise CodedError("isolation-failed", "Claude Code isolation fails: turn 1 " + _NO_INIT)
                    transcript += [{"role": "user", "text": shown}, {"role": "assistant", "text": assistant_text(fresh)}]
                    if turn == 0 and not [name for name in cast(list[str], loaded_skills(fresh, builtin))
                                          if name not in self.declared.skills]:
                        closed = True
                        break
                    if turn + 1 >= case.journey.max_turns:
                        closed = True
                        break
                    reply = simulate(transcript)
                    if reply.done:
                        closed = True
                        break
                    message = shown = reply.reply
                returncode = child.close()
                stderr = child.stderr
            _, unloaded = self._verdict(events, not closed, returncode, stderr, expected, builtin)
            results = [event for event in events if event.get("type") == "result"]
            trace = event_trace(events) + [token for result in results for token in token_events(result)]
            if unloaded:
                return not_loaded(workspace, started, trace) | {"transcript": transcript, "raw_events": events}
            marker = candidate.identity if marker_seen(events, candidate.identity) else ""
            return {"answer": {"result_json": transcript[-1]["text"], "load_marker": marker}, "events": trace,
                    "usage": usage(trace), "workspace": str(workspace), "latency_seconds": time.monotonic() - started,
                    "output_files": snapshot_outputs(workspace), "transcript": transcript, "raw_events": events}

    def _verdict(self, events: list[dict[str, object]], crashed: bool, returncode: int, stderr: str, expected: list[str],
                 builtin: Collection[str]) -> tuple[dict[str, object], bool]:
        """Judge the finished stream. Return the final result and whether Claude Code never registered the candidate.

        Raise when the isolation check fails or when the run failed: a crash, an error result, or no result.
        """
        final = final_event(events)
        failed = crashed or final is None or final.get("is_error") is True
        loaded = loaded_skills(events, builtin)
        if loaded is None and not failed:
            raise CodedError("isolation-failed", "Claude Code isolation fails: the stream " + _NO_INIT)
        unloaded = False
        if loaded is not None:
            loaded = [name for name in loaded if name not in self.declared.skills]
            if set(loaded) - set(expected):
                raise CodedError("isolation-failed", f"Claude Code isolation fails: skills in init event {loaded}, "
                                 + f"expected {expected}; the invocation counts against the budget")
            unloaded = bool(expected) and not loaded and not failed
        if failed or final is None:
            reason = (failure(returncode, stderr, events, expected, builtin, allowed=self.declared.skills)
                      or "missing-final-response")
            raise RuntimeError(f"Claude Code fails: {reason} (exit {returncode}); the invocation counts against the budget")
        return final, unloaded

    def sandbox(self, workspace: Path, argv: list[str], seconds: int | None = None) -> tuple[int, str, str]:
        """Run `argv` in the OS sandbox: `sandbox-exec` on macOS, bubblewrap elsewhere. Fail closed without it.

        Return the exit code, the standard output, and the standard error.

        A command that reaches the sandbox time limit, `seconds` (default `options.sandbox_seconds`), raises
        `SandboxTimeout`. A run budget that ends the command raises `BudgetExhausted`.
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
        capture = (Path(tempfile.mkdtemp(prefix="skillz-capture-")) if system == "linux" else None)
        try:
            result = sandboxed(sandbox_argv(system, tool, workspace.resolve(), argv, environment, self.read_roots,
                                            capture), cwd=workspace, limit=limit, budget=self.budget,
                               environment={"PATH": "/usr/bin:/bin"})
            if result.returncode != 0 and result.stderr.startswith(f"{name}:"):
                raise SandboxUnavailable(f"sandbox setup fails: {result.stderr[:200].strip()}", sandbox_fix(result.stderr))
            return result.returncode, result.stdout, result.stderr if capture is None else _take_stderr(capture)
        finally:
            if capture is not None:
                remove_tree(capture)

    def check_candidate(self, candidate: Candidate) -> CheckResult:
        self.check_name(candidate)
        with tempfile.TemporaryDirectory(prefix="skillz-contract-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            rules = resolve(candidate.contract)
            root = workspace / ".agents/skills" / rules.skill
            candidate.materialize(root)
            skill_file = root / "SKILL.md"
            if not skill_file.is_file() or not declares(skill_file.read_text(), rules.skill):
                return CheckResult(False, None, "fail")
            return helper_failure(self, workspace, rules)


APPARMOR_SYSCTL = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
APPARMOR_BWRAP = Path("/etc/apparmor.d/bwrap-userns-restrict")
PROBE_FIX = "; ".join((INSTALL_BUBBLEWRAP, INSTALL_SOCAT, USERNS_FIX))
INVENTORY_FIX = "run `claude` once to see why its skill list fails, and remove any plugin or setting that blocks it"
SETUP_FIX = "check that TMPDIR and HOME name writable directories, then run again"
LIVE_PREFLIGHT = ("one model call after budget approval checks Bash reads, writes, and network inside the sandbox; "
                  "a free check cannot prove them")


def _executable_environment(executable: Path) -> dict[str, str]:
    """Return the environment parts that the run and the doctor share when they start `executable`.

    `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` stays `0`. On Claude Code 2.1.301, a value of `1` replaced the sandbox-runtime
    filesystem and Unix-socket rules on a host that blocks the nested user namespace (#322, #323). The child
    environment holds no credential variable, so nothing is left to scrub. The live preflight stays the detector
    if a later Claude Code version changes this.
    """
    return {"PATH": f"{executable.parent}:/usr/bin:/bin", "LANG": "C.UTF-8",
            "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "0", "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1"}


def _version(executable: str) -> Check:
    """Run `claude --version` through the executable and environment of the run. A nonzero exit fails the row."""
    fix = "reinstall Claude Code, then run `claude --version` once to see why it fails"
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=15, check=False,
                                env=_executable_environment(Path(executable).absolute()))
    except (OSError, subprocess.TimeoutExpired) as error:
        return Check("claude", "fail", f"`{executable} --version` did not finish: {error}", fix)
    if result.returncode != 0:
        return Check("claude", "fail", f"`{executable} --version` exited {result.returncode} (stderr: "
                     + f"{' '.join(result.stderr[:200].split())!r})", fix)
    return Check("claude", "pass", result.stdout.strip() or "unknown version")


def _apparmor() -> Check:
    """Report the Ubuntu user-namespace restriction state. It explains the nested-namespace result; it never fails."""
    try:
        restricted = APPARMOR_SYSCTL.read_text().strip()
    except OSError:
        restricted = "absent"
    profile = "installed" if APPARMOR_BWRAP.exists() else "absent"
    return Check("apparmor", "info", f"kernel.apparmor_restrict_unprivileged_userns={restricted}; "
                 + f"bwrap-userns-restrict profile {profile}. The profile strips capabilities from processes that bwrap "
                 + "starts, whatever the sysctl says")


def _tool_checks() -> list[Check]:
    """Report each host tool row of `host_tools`, so one pass names every missing tool.

    A name is a command on PATH or an absolute path, and the detail of a missing tool says which one was looked up.
    """
    return [Check(f"tool:{name}", "pass" if found else "fail",
                  f"`{name}` found" if found else f"`{name}` not found" + ("" if name.startswith("/") else " on PATH"),
                  None if found else fix)
            for name, found, fix in host_tools()]


def _probe_checks(transport: ClaudeCode, probe: bool) -> list[Check]:
    """Report the helper sandbox probe and the skill inventory as separate rows. The inventory never waits on the probe."""
    checks: list[Check] = []
    if probe:
        try:
            transport.check_helper_sandbox()
        except (CodedError, OSError, RuntimeError) as error:
            checks.append(failed("sandbox-probe", error, PROBE_FIX))
        else:
            checks.append(Check("sandbox-probe", "pass", "the helper sandbox probe passed"))
    try:
        inventory = transport.inventory()
    except (CodedError, OSError, RuntimeError) as error:
        checks.append(failed("skill-inventory", error, INVENTORY_FIX))
    else:
        turned_off = f"; turned off {sorted(inventory.foreign)}" if inventory.foreign else ""
        checks.append(Check("skill-inventory", "pass", f"only the probe skill loads besides {len(inventory.builtin)} "
                            + f"Claude Code commands{turned_off}"))
    return checks


def _login_checks(executable: str, probe: bool) -> list[Check]:
    try:
        transport = ClaudeCode("doctor", Budget(1, 120, 0), lambda: None, Path(executable))
    except CodedError as error:
        return [failed("login", error, LOGIN_HINT), credential_row(MISSING_CREDENTIAL)]
    except (OSError, RuntimeError) as error:
        return [failed("claude-setup", error, SETUP_FIX), credential_row(login_credential_kind())]
    checks = [Check("login", "pass", "Claude login found" if transport.credential else "macOS Keychain login"),
              credential_row(transport.credential_kind)]
    try:
        if transport.unix_sockets():
            checks.append(Check("nested-namespace", "info", "this host blocks a user namespace inside bwrap, so the runner "
                                + "turns off Claude Code's Unix-socket filter (allowAllUnixSockets); file and domain rules "
                                + "stay on, and the live preflight proves that a host Unix socket stays unreachable"))
        checks += _probe_checks(transport, probe)
    finally:
        try:
            transport.close()
        except CodedError as error:
            checks.append(Check("login-close", "info", str(error)) if error.code in NOTICE_CODES
                          else failed("login-close", error, LOGIN_HINT))
    return checks


def _claude_checks(named: str | None = None) -> list[Check]:
    """Check Claude Code. `named` is the executable of a configured role; the default is `claude` on PATH."""
    executable = shutil.which(named or "claude")
    if executable is None:
        return [Check("claude", "fail", f"the `{named or 'claude'}` executable is not on PATH",
                      "install Claude Code, then run `claude` once and log in"),
                credential_row(login_credential_kind())]
    tools = _tool_checks()
    checks = [_version(executable), *tools]
    if sys.platform != "darwin":
        checks.append(_apparmor())
    return checks + _login_checks(executable, all(tool.status == "pass" for tool in tools))

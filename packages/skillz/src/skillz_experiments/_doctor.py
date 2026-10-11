"""Free host checks for a run: every check that needs no model call, in one pass, before the first question."""
from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from skillz_experiments import _claude
from skillz_experiments._cases import CodedError
from skillz_experiments._codex import MINIMUM_TEXT, Codex, credential_kind as codex_credential_kind, host_home
from skillz_experiments._evaluator import API_KEY, MISSING_CREDENTIAL, SUBSCRIPTION_LOGIN, UNVERIFIED_CREDENTIAL
from skillz_experiments._runtime import Budget

Status = Literal["pass", "fail", "info", "needs-live"]
APPARMOR_SYSCTL = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
APPARMOR_BWRAP = Path("/etc/apparmor.d/bwrap-userns-restrict")
PROBE_FIX = "; ".join((_claude.INSTALL_BUBBLEWRAP, _claude.INSTALL_SOCAT, _claude.USERNS_FIX))
INVENTORY_FIX = "run `claude` once to see why its skill list fails, and remove any plugin or setting that blocks it"
SETUP_FIX = "check that TMPDIR and HOME name writable directories, then run again"
CODEX_FIX = f"install Codex {MINIMUM_TEXT} or newer and log in with `codex`, or run with `--harness claude`"
LIVE_PREFLIGHT = ("one model call after budget approval checks Bash reads, writes, and network inside the sandbox; "
                  "a free check cannot prove them")


@dataclass(frozen=True)
class Check:
    """One host check. A `fail` row names its fix; `info` explains a choice the runner makes."""
    name: str
    status: Status
    detail: str
    fix: str | None = None

    def data(self) -> dict[str, object]:
        row: dict[str, object] = {"check": self.name, "status": self.status, "detail": self.detail}
        return row | ({"fix": self.fix} if self.fix else {})


def _version(executable: str) -> str:
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=15, check=False,
                                env={"PATH": "/usr/bin:/bin"})
    except (OSError, subprocess.TimeoutExpired):
        return "unknown version"
    return result.stdout.strip() or "unknown version"


def _failed(name: str, error: Exception, fix: str) -> Check:
    """Fail `name` with the fix that the error carries, then the fix in its text, then `fix`."""
    text = str(error)
    carried = cast(str | None, getattr(error, "fix", None))
    return Check(name, "fail", text, carried or text.partition("; fix: ")[2] or fix)


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
    """Report each host tool row of `_claude.host_tools`, so one pass names every missing tool."""
    return [Check(f"tool:{name}", "pass" if found else "fail", f"`{name}` found", None if found else fix)
            for name, found, fix in _claude.host_tools()]


def _probe_checks(transport: _claude.ClaudeCode, probe: bool) -> list[Check]:
    """Report the helper sandbox probe and the skill inventory as separate rows. The inventory never waits on the probe."""
    checks: list[Check] = []
    if probe:
        try:
            transport.check_helper_sandbox()
        except (CodedError, OSError, RuntimeError) as error:
            checks.append(_failed("sandbox-probe", error, PROBE_FIX))
        else:
            checks.append(Check("sandbox-probe", "pass", "the helper sandbox probe passed"))
    try:
        inventory = transport.inventory()
    except (CodedError, OSError, RuntimeError) as error:
        checks.append(_failed("skill-inventory", error, INVENTORY_FIX))
    else:
        turned_off = f"; turned off {sorted(inventory.foreign)}" if inventory.foreign else ""
        checks.append(Check("skill-inventory", "pass", f"only the probe skill loads besides {len(inventory.builtin)} "
                            + f"Claude Code commands{turned_off}"))
    return checks


def _credential(kind: str) -> Check:
    """Report the credential kind of a built-in role. A subscription login needs no API key."""
    if kind == SUBSCRIPTION_LOGIN:
        return Check("credential", "pass", kind)
    if kind == API_KEY:
        return Check("credential", "info", f"{kind}; the harness bills this key per token, not a subscription")
    if kind == UNVERIFIED_CREDENTIAL:
        return Check("credential", "info", f"{kind}; the runner cannot read this login, so a failed login shows at the first model call")
    return Check("credential", "fail", kind, "log in with the harness CLI; the runner uses that login and needs no API key")


def _login_checks(executable: str, probe: bool) -> list[Check]:
    try:
        transport = _claude.ClaudeCode("doctor", Budget(1, 120, 0), lambda: None, Path(executable))
    except CodedError as error:
        return [_failed("login", error, _claude.LOGIN_HINT), _credential(MISSING_CREDENTIAL)]
    except (OSError, RuntimeError) as error:
        return [_failed("claude-setup", error, SETUP_FIX), _credential(_claude.login_credential_kind())]
    checks = [Check("login", "pass", "Claude login found" if transport.credential else "macOS Keychain login"),
              _credential(transport.credential_kind)]
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
            checks.append(Check("login-close", "info", str(error)) if error.code in _claude.NOTICE_CODES
                          else _failed("login-close", error, _claude.LOGIN_HINT))
    return checks


def _claude_checks(named: str | None = None) -> list[Check]:
    """Check Claude Code. `named` is the executable of a configured role; the default is `claude` on PATH."""
    executable = shutil.which(named or "claude")
    if executable is None:
        return [Check("claude", "fail", f"the `{named or 'claude'}` executable is not on PATH",
                      "install Claude Code, then run `claude` once and log in"),
                _credential(_claude.login_credential_kind())]
    tools = _tool_checks()
    checks = [Check("claude", "pass", _version(executable)), *tools]
    if sys.platform != "darwin":
        checks.append(_apparmor())
    return checks + _login_checks(executable, all(tool.status == "pass" for tool in tools))


def _codex_checks() -> list[Check]:
    try:
        transport = Codex("doctor", Budget(1, 120, 0), lambda: None)
    except (OSError, RuntimeError) as error:
        return [_failed("codex", error, CODEX_FIX), _credential(codex_credential_kind(host_home()))]
    try:
        _ = transport.preflight()
    except (CodedError, OSError, RuntimeError) as error:
        return [_failed("codex-preflight", error, CODEX_FIX), _credential(transport.credential_kind)]
    finally:
        transport.close()
    return [Check("codex-preflight", "pass", "version, sandbox, and skill discovery checks passed"),
            _credential(transport.credential_kind)]


def doctor(harness: str, executable: str | None = None) -> dict[str, object]:
    """Run every free host check for `harness` and report all of them. It makes no model call.

    `ok` is False when any check fails. Checks that need a model call report `needs-live`.
    The `credential` row names the credential kind of the harness: a subscription login, an API key,
    an unverified macOS Keychain login, or missing. `executable` names the Claude executable of a configured role.
    """
    checks = _claude_checks(executable) if harness == "claude" else _codex_checks()
    if harness == "claude":
        checks.append(Check("live-isolation", "needs-live", LIVE_PREFLIGHT))
    return {"ok": not any(check.status == "fail" for check in checks), "harness": harness,
            "checks": [check.data() for check in checks], "live_calls": 0}


def host_report(selections: Sequence[tuple[str, str | None]]) -> dict[str, object]:
    """Run the host checks of each `(harness, executable)` pair. One pair gives its own report.

    Several pairs give one report with a flat `checks` list. Each check name carries the harness as a prefix,
    and the executable too when two pairs share a harness.
    """
    reports = [doctor(harness, executable) for harness, executable in selections]
    if len(reports) == 1:
        return reports[0]
    harnesses = [harness for harness, _ in selections]
    checks: list[dict[str, object]] = []
    for (harness, executable), report in zip(selections, reports):
        label = harness if harnesses.count(harness) == 1 else f"{harness}({executable})"
        checks += [row | {"check": f"{label}:{row['check']}"} for row in cast(list[dict[str, object]], report["checks"])]
    return {"ok": all(report["ok"] for report in reports), "harness": "+".join(dict.fromkeys(harnesses)),
            "checks": checks, "live_calls": 0}

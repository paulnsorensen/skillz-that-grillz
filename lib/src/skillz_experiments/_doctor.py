"""Free host checks for a run: every check that needs no model call, in one pass, before the first question."""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from skillz_experiments import _claude, _nono
from skillz_experiments._cases import CodedError
from skillz_experiments._codex import Codex
from skillz_experiments._harness import validate_isolation
from skillz_experiments._runtime import Budget

Status = Literal["pass", "fail", "info", "needs-live"]
APPARMOR_SYSCTL = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
APPARMOR_BWRAP = Path("/etc/apparmor.d/bwrap-userns-restrict")
PROBE_FIX = "; ".join((_claude.INSTALL_BUBBLEWRAP, _claude.INSTALL_SOCAT, _claude.USERNS_FIX))
INVENTORY_FIX = "run `claude` once to see why its skill list fails, and remove any plugin or setting that blocks it"
SETUP_FIX = "check that TMPDIR and HOME name writable directories, then run again"
CODEX_FIX = "install the pinned Codex version and log in with `codex`, or run with `--harness claude`"
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


def _nono_checks() -> list[Check]:
    """Report each nono requirement, so a host that cannot run nono learns every fix in one pass."""
    unmet = _nono.problems()
    checks = [Check("nono", "fail", cause, fix) for cause, fix in unmet]
    nono = shutil.which("nono")
    if nono is not None:
        checks.append(Check("nono", "pass" if not unmet else "info", f"{_version(nono)}; Landlock ABI {_nono.landlock_abi()}"))
    return checks


def _tool_checks(isolation: str) -> list[Check]:
    """Report each host tool row of `_claude.host_tools`, so one pass names every missing tool."""
    return [Check(f"tool:{name}", "pass" if found else "fail", f"`{name}` found", None if found else fix)
            for name, found, fix in _claude.host_tools(isolation)]


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


def _login_checks(executable: str, isolation: str, probe: bool) -> list[Check]:
    nono = isolation == "nono"
    try:
        transport = _claude.ClaudeCode("doctor", Budget(1, 120, 0), lambda: None, Path(executable), isolation=isolation)
    except CodedError as error:
        return [_failed("login", error, _claude.LOGIN_HINT)]
    except (OSError, RuntimeError) as error:
        return [_failed("claude-setup", error, SETUP_FIX)]
    checks = [Check("login", "pass", "nono injects the host API key" if nono else
                    "Claude login found" if transport.credential else "macOS Keychain login")]
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


def _claude_checks(isolation: str) -> list[Check]:
    executable = shutil.which("claude")
    if executable is None:
        return [Check("claude", "fail", "the `claude` executable is not on PATH", "install Claude Code, then run `claude` once and log in")]
    nono = isolation == "nono"
    checks = [Check("claude", "pass", _version(executable))]
    if nono:
        checks += _nono_checks()
    tools = _tool_checks(isolation)
    checks += tools
    if sys.platform != "darwin" and not nono:
        checks.append(_apparmor())
    if any(check.status == "fail" for check in checks if check.name == "nono"):
        return checks
    return checks + _login_checks(executable, isolation, all(tool.status == "pass" for tool in tools))


def _codex_checks() -> list[Check]:
    try:
        transport = Codex("doctor", Budget(1, 120, 0), lambda: None)
    except (OSError, RuntimeError) as error:
        return [_failed("codex", error, CODEX_FIX)]
    try:
        _ = transport.preflight()
    except (CodedError, OSError, RuntimeError) as error:
        return [_failed("codex-preflight", error, CODEX_FIX)]
    finally:
        transport.close()
    return [Check("codex-preflight", "pass", "version, sandbox, and skill discovery checks passed")]


def doctor(harness: str, isolation: str = "claude") -> dict[str, object]:
    """Run every free host check for `harness` and report all of them. It makes no model call.

    `ok` is False when any check fails. Checks that need a model call report `needs-live`.
    `isolation` is `claude` or `nono`; only the `claude` harness supports `nono`.
    """
    try:
        validate_isolation(harness, isolation)
    except ValueError as error:
        checks = [Check("isolation", "fail", str(error), "use `--isolation claude`, or `--harness claude` with nono")]
    else:
        checks = _claude_checks(isolation) if harness == "claude" else _codex_checks()
    if harness == "claude":
        checks.append(Check("live-isolation", "needs-live", LIVE_PREFLIGHT))
    return {"ok": not any(check.status == "fail" for check in checks), "harness": harness, "isolation": isolation,
            "checks": [check.data() for check in checks], "live_calls": 0}

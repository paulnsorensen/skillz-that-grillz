"""Free host checks for a run: every check that needs no model call, in one pass, before the first question."""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from skillz_experiments import _claude, _nono
from skillz_experiments._cases import CodedError
from skillz_experiments._codex import Codex
from skillz_experiments._runtime import Budget

Status = Literal["pass", "fail", "info", "needs-live"]
APPARMOR_SYSCTL = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
APPARMOR_BWRAP = Path("/etc/apparmor.d/bwrap-userns-restrict")
LIVE_TOOLS = ("/usr/bin/python3", "/usr/bin/curl")
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


def _failed(name: str, error: Exception, fix: str | None = None) -> Check:
    text = str(error)
    return Check(name, "fail", text, text.partition("; fix: ")[2] or fix)


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


def _claude_checks(isolation: str) -> list[Check]:
    executable = shutil.which("claude")
    if executable is None:
        return [Check("claude", "fail", "the `claude` executable is not on PATH", "install Claude Code, then run `claude` once and log in")]
    checks = [Check("claude", "pass", _version(executable))]
    nono = isolation == "nono"
    if nono:
        checks += _nono_checks()
    if sys.platform != "darwin":
        helpers = () if nono else _claude.SANDBOX_HELPERS
        checks += [Check(f"tool:{name}", "pass" if shutil.which(name) else "fail", f"`{name}` on PATH",
                         None if shutil.which(name) else fix)
                   for name, fix in (("bwrap", _claude.INSTALL_BUBBLEWRAP), *((helper, _claude.INSTALL_SOCAT)
                                                                             for helper in helpers))]
        if not nono:
            checks.append(_apparmor())
    checks += [Check(f"tool:{path}", "pass" if Path(path).is_file() else "fail", "the live preflight runs it",
                     None if Path(path).is_file() else f"install {Path(path).name}") for path in LIVE_TOOLS]
    if any(check.status == "fail" for check in checks if check.name == "nono"):
        return checks
    try:
        transport = _claude.ClaudeCode("doctor", Budget(1, 120, 0), lambda: None, Path(executable), isolation=isolation)
    except CodedError as error:
        return [*checks, _failed("login", error, _claude.LOGIN_HINT)]
    try:
        login = ("nono injects the host API key" if nono else
                 "Claude login found" if transport.credential else "macOS Keychain login")
        checks.append(Check("login", "pass", login))
        if transport.unix_sockets():
            checks.append(Check("nested-namespace", "info", "this host blocks a user namespace inside bwrap, so the runner "
                                + "turns off Claude Code's Unix-socket filter (allowAllUnixSockets); file and domain rules "
                                + "stay on, and the live preflight proves that a host Unix socket stays unreachable"))
        try:
            transport.check_sandbox()
        except (CodedError, OSError, RuntimeError) as error:
            checks.append(_failed("sandbox-and-skills", error))
        else:
            inventory = transport.inventory()
            turned_off = f"; turned off {sorted(inventory.foreign)}" if inventory.foreign else ""
            checks.append(Check("sandbox-and-skills", "pass", "the helper sandbox probe passed, and only the probe skill "
                                + f"loads besides {len(inventory.builtin)} Claude Code commands{turned_off}"))
    finally:
        try:
            transport.close()
        except CodedError as error:
            checks.append(_failed("login-close", error))
    return checks


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
    if isolation == "nono" and harness != "claude":
        checks = [Check("isolation", "fail", "nono isolation supports only the claude harness", "use `--harness claude`")]
    else:
        checks = _claude_checks(isolation) if harness == "claude" else _codex_checks()
    if harness == "claude":
        checks.append(Check("live-isolation", "needs-live", LIVE_PREFLIGHT))
    return {"ok": not any(check.status == "fail" for check in checks), "harness": harness, "isolation": isolation,
            "checks": [check.data() for check in checks], "live_calls": 0}

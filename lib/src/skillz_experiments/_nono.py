"""nono isolation for the Claude role on Linux: Landlock file rules, a domain allowlist proxy, and API key injection.

nono runs the whole Claude Code process, so its file tools and its Bash commands share one boundary. Landlock needs no
user namespace, so Ubuntu's `bwrap-userns-restrict` profile does not apply. nono's proxy allows only the model host and
puts the real API key on the request; the sandbox sees only a per-session proxy token.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import sys
from pathlib import Path
from typing import cast

from skillz_experiments._cases import CodedError

MODEL_HOST = "api.anthropic.com"
CREDENTIAL = "ANTHROPIC_API_KEY"
# Landlock ABI 4 (Linux 6.7) adds TCP connect rules; nono needs them to force traffic through its proxy.
NETWORK_ABI = 4
LANDLOCK_CREATE_RULESET = 444
LANDLOCK_CREATE_RULESET_VERSION = 1
# Read the core system paths; deny credential stores and shell files. No group grants a shared temp directory.
GROUPS = ("system_read_linux_core", "deny_credentials", "deny_shell_configs", "deny_shell_history")
INSTALL = "install nono (for example `brew install nono`)"


def landlock_abi() -> int:
    """Return the kernel's Landlock ABI version, or -1 when Landlock is unavailable."""
    if not sys.platform.startswith("linux"):
        return -1
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return int(cast(int, libc.syscall(LANDLOCK_CREATE_RULESET, None, 0, LANDLOCK_CREATE_RULESET_VERSION)))
    except (OSError, AttributeError):
        return -1


def problems() -> list[tuple[str, str]]:
    """Return each unmet nono requirement as a pair of cause and fix. An empty list means nono can run the role."""
    if not sys.platform.startswith("linux"):
        return [("nono isolation is verified only on Linux", "use `--isolation claude` on this host")]
    found: list[tuple[str, str]] = []
    if shutil.which("nono") is None:
        found.append(("`nono` is not on PATH", INSTALL))
    abi = landlock_abi()
    if abi < NETWORK_ABI:
        found.append((f"Landlock ABI {abi} lacks network rules (needs {NETWORK_ABI}, Linux 6.7 or later)",
                      "use a newer kernel, or use `--isolation claude`"))
    if not os.environ.get(CREDENTIAL):
        found.append((f"{CREDENTIAL} is not set on the host", f"export {CREDENTIAL}; nono injects it through its proxy, "
                      + "so the sandbox never sees the key"))
    return found


def require() -> str:
    """Return the nono executable path, or stop with `nono-unavailable` naming every unmet requirement."""
    unmet = problems()
    if unmet:
        raise CodedError("nono-unavailable", "nono isolation cannot run: " + "; ".join(cause for cause, _ in unmet)
                         + "; no unsafe fallback; fix: " + "; ".join(fix for _, fix in unmet))
    return str(shutil.which("nono"))


def profile(*, config_dir: Path, project: Path, executable: Path, settings_file: Path,
            variables: list[str]) -> dict[str, object]:
    """Build the nono profile for one Claude process. The working directory (the workspace) is the only write grant
    besides the config directory. `project` holds the staged `.claude` and `.agents` trees, which stay read-only.

    The profile grants a recursive read of the executable's directory. Stop with `nono-unavailable` when that
    directory is `$HOME`, `/`, or an ancestor of `project`, because the grant would then expose the host or the run.
    """
    wide = executable.parent
    if wide in (Path("/"), Path.home().resolve()) or project.resolve().is_relative_to(wide):
        raise CodedError("nono-unavailable", f"nono isolation cannot grant read access to {wide}, the directory of the "
                         + "Claude Code executable: it holds the host home, the root, or the run directory; no unsafe "
                         + "fallback; fix: install or copy Claude Code into its own directory")
    return {
        "meta": {"name": "skillz-task", "description": "skillz autoimprove task process"},
        "groups": {"include": list(GROUPS)},
        "workdir": {"access": "readwrite"},
        "filesystem": {"allow": [str(config_dir)],
                       "read": [str(project / ".claude"), str(project / ".agents"), str(executable.parent)],
                       "read_file": [str(settings_file)]},
        "network": {"allow_domain": [MODEL_HOST], "credentials": ["anthropic"]},
        "linux": {"af_unix_mediation": "pathname"},
        "environment": {"allow_vars": variables},
    }


def command(nono: str, profile_file: Path, environment: dict[str, str], argv: list[str]) -> list[str]:
    """Wrap `argv` in `nono run`. `/usr/bin/env` sets the child variables, so nono's own HOME stays private."""
    return [nono, "run", "--silent", "--no-audit", "--profile", str(profile_file), "--allow-cwd", "--",
            "/usr/bin/env", *[f"{name}={value}" for name, value in environment.items()], *argv]


def host_environment(state: Path) -> dict[str, str]:
    """Return the environment of the nono process: its private state home and the key it injects."""
    return {"PATH": "/usr/bin:/bin", "HOME": str(state), "LANG": "C.UTF-8", CREDENTIAL: os.environ.get(CREDENTIAL, "")}

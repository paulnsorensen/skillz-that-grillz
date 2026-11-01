"""The macOS Seatbelt profile for one sandboxed command. The Claude adapter and the Gemini `sandbox` operation use it."""
from __future__ import annotations

from pathlib import Path

DEVELOPER_DIR = "/Library/Developer/CommandLineTools"
SEATBELT_RUNTIME = ("/usr", "/bin", "/System", DEVELOPER_DIR, "/private/var/db/dyld")
# The `/usr/bin/python3` shim (`xcrun`) adds four of these names. `seatbelt_environment` sets `DEVELOPER_DIR`.
XCRUN_ENVIRONMENT = ("CPATH", "DEVELOPER_DIR", "LIBRARY_PATH", "MANPATH", "SDKROOT")


def seatbelt_environment() -> list[str]:
    """Name the Command Line Tools as the developer directory, when they exist.

    The pin keeps the `/usr/bin/python3` shim inside the readable runtime.
    """
    return [f"DEVELOPER_DIR={DEVELOPER_DIR}"] if Path(DEVELOPER_DIR).is_dir() else []


def command_profile(workspace: Path, read_roots: tuple[str, ...] = (), capture: Path | None = None) -> str:
    """Build the macOS `sandbox-exec` profile. Everything is denied unless this profile allows it.

    The profile allows no network. It allows reads of the root directory itself (not its children).
    It also allows reads of the system runtime, the extra read roots, and the workspace.
    It allows writes to the workspace except `.agents`. Later rules win, so the `.agents` deny follows
    the write allow. A `capture` directory also gets a write allow, for the command's standard error file.
    """
    path = str(workspace)
    rules = ["(version 1)", "(deny default)", "(allow process-fork)", "(allow process-exec)",
             "(allow signal (target self))", "(allow sysctl-read)", "(allow file-read-metadata)",
             '(allow file-read* (literal "/"))',
             *[f'(allow file-read* (subpath "{root}"))' for root in (*SEATBELT_RUNTIME, *read_roots)],
             '(allow file-read* (literal "/dev/null") (literal "/dev/urandom"))',
             f'(allow file-read* (subpath "{path}"))', f'(allow file-write* (subpath "{path}"))',
             f'(deny file-write* (subpath "{path}/.agents"))', '(allow file-write* (literal "/dev/null"))']
    if capture is not None:
        rules.append(f'(allow file-write* (subpath "{capture}"))')
    return "\n".join(rules) + "\n"

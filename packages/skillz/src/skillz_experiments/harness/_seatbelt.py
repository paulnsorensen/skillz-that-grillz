"""The macOS Seatbelt profile for one sandboxed command. The Claude adapter and the Gemini `sandbox` operation use it."""
from __future__ import annotations

from pathlib import Path

SEATBELT_RUNTIME = ("/usr", "/bin", "/System", "/Library/Developer/CommandLineTools", "/private/var/db/dyld")
DEVELOPER_DIR = "/Library/Developer/CommandLineTools"


def command_profile(workspace: Path, read_roots: tuple[str, ...] = ()) -> str:
    """Build the macOS `sandbox-exec` profile. Everything is denied unless this profile allows it.

    The profile allows no network. It allows reads of the root directory itself (not its children), the system runtime, the extra read roots, and the
    workspace, and writes to the workspace except `.agents`. Later rules win, so the `.agents` deny follows
    the write allow.
    """
    path = str(workspace)
    rules = ["(version 1)", "(deny default)", "(allow process-fork)", "(allow process-exec)",
             "(allow signal (target self))", "(allow sysctl-read)", "(allow file-read-metadata)",
             '(allow file-read* (literal "/"))',
             *[f'(allow file-read* (subpath "{root}"))' for root in (*SEATBELT_RUNTIME, *read_roots)],
             '(allow file-read* (literal "/dev/null") (literal "/dev/urandom"))',
             f'(allow file-read* (subpath "{path}"))', f'(allow file-write* (subpath "{path}"))',
             f'(deny file-write* (subpath "{path}/.agents"))', '(allow file-write* (literal "/dev/null"))']
    return "\n".join(rules) + "\n"

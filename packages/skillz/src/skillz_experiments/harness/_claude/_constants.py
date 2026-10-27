from __future__ import annotations

from typing import Literal


PROBE_PREFIX = "skillz-probe-"


# The host file that the preflight asks the Read tool to read. Linux and macOS both have it, and it holds no secret.
HOST_FILE = "/etc/hosts"

# The workspace symlink to the file outside the workspace. The sandbox and Read-tool probes must not follow it out of
# the workspace. The name is plain, because the live Read-tool prompt names it.
LINK = "link.txt"

AUTH_FAILED = "authentication failed"


CONFIG_PREFIX = "skillz-claude-config-"


TERMINAL_CODES = frozenset({"isolation-failed", "credential-changed"})


NOTICE_CODES = frozenset({"credential-rotated", "credential-refreshed"})


def stop_kind(code: object) -> Literal["terminal", "notice"] | None:
    """Return the stop kind of a Claude failure code, or None for any other value."""
    if not isinstance(code, str):
        return None
    if code in TERMINAL_CODES:
        return "terminal"
    return "notice" if code in NOTICE_CODES else None


SANDBOX_STDERR = "stderr"


LOGIN_HINT = "run `claude` once and log in"


INSTALL_BUBBLEWRAP = "install bubblewrap (for example `sudo apt install bubblewrap`)"

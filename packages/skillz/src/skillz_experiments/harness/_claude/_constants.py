from __future__ import annotations

from typing import Literal


PROBE_PREFIX = "skillz-probe-"


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


SANDBOX_SECONDS = 20

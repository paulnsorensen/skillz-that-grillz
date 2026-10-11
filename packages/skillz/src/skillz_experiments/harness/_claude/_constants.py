from __future__ import annotations


PROBE_PREFIX = "skillz-probe-"


AUTH_FAILED = "authentication failed"


CONFIG_PREFIX = "skillz-claude-config-"


TERMINAL_CODES = frozenset({"isolation-failed", "credential-changed"})


NOTICE_CODES = frozenset({"credential-rotated", "credential-refreshed"})


SANDBOX_STDERR = "stderr"


LOGIN_HINT = "run `claude` once and log in"


INSTALL_BUBBLEWRAP = "install bubblewrap (for example `sudo apt install bubblewrap`)"


SANDBOX_SECONDS = 20

"""One free host check row, and the two helpers that every adapter's checks share."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

SUBSCRIPTION_LOGIN = "subscription login"
API_KEY = "api key"
UNVERIFIED_CREDENTIAL = "unverified (macOS Keychain)"
MISSING_CREDENTIAL = "missing"

Status = Literal["pass", "fail", "info", "needs-live"]


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


def failed(name: str, error: Exception, fix: str) -> Check:
    """Fail `name` with the fix that the error carries, then the fix in its text, then `fix`."""
    text = str(error)
    carried = cast(str | None, getattr(error, "fix", None))
    return Check(name, "fail", text, carried or text.partition("; fix: ")[2] or fix)


def credential_row(kind: str) -> Check:
    """Report the credential kind of a built-in role. A subscription login needs no API key."""
    if kind == SUBSCRIPTION_LOGIN:
        return Check("credential", "pass", kind)
    if kind == API_KEY:
        return Check("credential", "info", f"{kind}; the harness bills this key per token, not a subscription")
    if kind == UNVERIFIED_CREDENTIAL:
        return Check("credential", "info", f"{kind}; the runner cannot read this login, so a failed login shows at the first model call")
    return Check("credential", "fail", kind, "log in with the harness CLI; the runner uses that login and needs no API key")

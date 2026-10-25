"""Free host checks for a run: every check that needs no model call, in one pass, before the first question."""
from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from skillz_experiments.harness._roles import adapter_class


def doctor(harness: str, executable: str | None = None) -> dict[str, object]:
    """Run every free host check for `harness` and report all of them. It makes no model call.

    `ok` is False when any check fails. Checks that need a model call report `needs-live`.
    The `credential` row names the credential kind of the harness: a subscription login, an API key,
    an unverified macOS Keychain login, or missing. `executable` names the Claude executable of a configured role.
    """
    checks = adapter_class(harness).host_checks(executable)
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

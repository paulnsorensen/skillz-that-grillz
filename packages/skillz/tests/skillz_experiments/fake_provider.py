"""One shared base for the fake transports and providers in the experiment tests.

The base holds the no-op members that `Transport` (`harness/_transport.py`) requires. A fake inherits it
and writes only the members its test cares about, so a new protocol member is one edit here.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing_extensions import override

from skillz_experiments.evaluation import CheckResult

PASSED = CheckResult(True, None, "pass")


class ProviderCrash(OverflowError):
    """A provider error that ends the run. The gate does not catch an `OverflowError`; `run` records it and re-raises."""


class FakeProvider:
    """No-op defaults for every `Transport` member that a fake does not exercise."""

    def check_name(self, candidate: object) -> None:
        del candidate

    def reuse_key(self, fingerprint: object) -> str | None:
        del fingerprint

    def resume(self, kept: object) -> bool:
        del kept
        return False

    def run_facts(self, evidence: Mapping[str, object]) -> dict[str, object]:
        del evidence
        return {}

    def adopt_login(self) -> None:
        return None

    def preflight(self) -> dict[str, object]:
        return {}

    def close(self) -> None:
        pass

    def check_candidate(self, candidate: object) -> CheckResult:
        del candidate
        return PASSED


class FakeRunProvider(FakeProvider):
    """The run `Provider` port: `preflight` takes the recorded evidence and `run_facts` is a dict, not a method."""

    run_facts: dict[str, object] = {}  # pyright: ignore[reportIncompatibleMethodOverride]

    @override
    def preflight(self, recorded: dict[str, object] | None = None) -> dict[str, object]:
        del recorded
        return {}

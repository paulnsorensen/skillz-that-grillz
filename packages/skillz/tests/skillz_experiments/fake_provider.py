"""One shared base for the fake transports and providers in the experiment tests.

The base holds the no-op members that `Transport` (`harness/_transport.py`) requires. A fake inherits it
and writes only the members its test cares about, so a new protocol member is one edit here.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from skillz_experiments.evaluation import CheckResult
from skillz_experiments.harness import Configuration
from skillz_experiments.run import Stop, run
from skillz_experiments.run._state import Factory

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


class FakeRunProvider:
    """No-op defaults for the run `Provider` port (`run/_state.py`): the members that a run fake rarely overrides."""

    def __init__(self) -> None:
        self.run_facts: dict[str, object] = {}
    def preflight(self, recorded: dict[str, object] | None = None) -> dict[str, object]:
        del recorded
        return {"isolation": "test-provider"}

    def close(self) -> None:
        pass

    def check_name(self, candidate: object) -> None:
        del candidate

    def check_candidate(self, candidate: object) -> CheckResult:
        del candidate
        return PASSED


def search_approval(target: Path, out: Path, model: str, *, configuration: Configuration | None = None) -> int:
    """Read the separate search estimate without making a provider call."""
    try:
        _ = run(target, out, model, search=True, configuration=configuration)
    except Stop as stopped:
        if stopped.code != "budget-unapproved":
            raise
        return cast(dict[str, int], stopped.data["estimate"])["calls"]
    raise AssertionError("search did not ask for its separate budget")


def approved_search(target: Path, out: Path, model: str, case_hash: str, baseline_calls: int, *,
                    factory: Factory | None = None, statistics: Mapping[str, float] | None = None) -> dict[str, object]:
    """Measure a new baseline, then request and approve search with the same fake provider."""
    baseline = run(target, out, model, live=True, approve_cases=case_hash, approve_budget=baseline_calls,
                   factory=factory, statistics=statistics)
    assert baseline["search"] == "not-run"
    return run(target, out, model, live=True, search=True, approve_budget=search_approval(target, out, model),
               factory=factory)

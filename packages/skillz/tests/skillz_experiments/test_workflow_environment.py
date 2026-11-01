"""The environment-differs stop names its evidence even when only the hash changed."""

import threading
from typing import cast, final

import pytest
from fake_provider import FakeRunProvider
from typing_extensions import override
from skillz_experiments._budget import Budget
from skillz_experiments.harness import EnvironmentDiffers
from skillz_experiments.run._session import Session
from skillz_experiments.run._state import Provider


@final
class _Observing(FakeRunProvider):
    def __init__(self, observed: dict[str, object]) -> None:
        super().__init__()
        self.observed: dict[str, object] = observed

    @override
    def preflight(self, recorded: dict[str, object] | None = None) -> dict[str, object]:
        del recorded
        return self.observed


def _session(recorded: dict[str, object], observed: dict[str, object]) -> Session:
    session = object.__new__(Session)
    session.budget = Budget(1, 60.0, reserve=0)
    session.lock = threading.RLock()
    session.record = {"preflight": recorded, "environment_hash": recorded["environment_hash"]}
    session.provider = cast(Provider, cast(object, _Observing(observed)))
    return session


def test_a_hash_only_difference_reads_as_a_sentence() -> None:
    session = _session({"environment_hash": "b"}, {"environment_hash": "b"})
    session.record["environment_hash"] = "a"
    with pytest.raises(EnvironmentDiffers) as caught:
        session.preflight()
    assert "changed evidence: the environment hash only;" in str(caught.value)

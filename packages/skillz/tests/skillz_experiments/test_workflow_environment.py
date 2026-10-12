"""The environment-differs stop names its evidence even when only the hash changed."""

import threading
from types import SimpleNamespace
from typing import cast

import pytest
from skillz_experiments._budget import Budget
from skillz_experiments.harness import EnvironmentDiffers
from skillz_experiments.run._session import Session


def _session(recorded: dict[str, object], observed: dict[str, object]) -> Session:
    session = object.__new__(Session)
    session.budget = Budget(1, 60.0, reserve=0)
    session.lock = threading.RLock()
    session.record = {"preflight": recorded, "environment_hash": recorded["environment_hash"]}
    session.provider = cast(object, SimpleNamespace(preflight=lambda: observed))  # pyright: ignore[reportAttributeAccessIssue]
    return session


def test_a_hash_only_difference_reads_as_a_sentence() -> None:
    session = _session({"environment_hash": "b"}, {"environment_hash": "b"})
    session.record["environment_hash"] = "a"
    with pytest.raises(EnvironmentDiffers) as caught:
        session.preflight()
    assert "changed evidence: the environment hash only;" in str(caught.value)

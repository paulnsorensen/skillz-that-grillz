"""The environment-differs stop names its evidence even when only the hash changed."""

from types import SimpleNamespace
from typing import cast

import pytest
from skillz_experiments._workflow import _Session  # pyright: ignore[reportPrivateUsage] -- tests build a bare session
from skillz_experiments.harness import EnvironmentDiffers


def _session(recorded: dict[str, object], observed: dict[str, object]) -> _Session:
    session = object.__new__(_Session)
    session.record = {"preflight": recorded, "environment_hash": recorded["environment_hash"]}
    session.provider = cast(object, SimpleNamespace(preflight=lambda: observed))  # pyright: ignore[reportAttributeAccessIssue]
    return session


def test_a_hash_only_difference_reads_as_a_sentence() -> None:
    session = _session({"environment_hash": "b"}, {"environment_hash": "b"})
    session.record["environment_hash"] = "a"
    with pytest.raises(EnvironmentDiffers) as caught:
        session.preflight()
    assert "changed evidence: the environment hash only;" in str(caught.value)

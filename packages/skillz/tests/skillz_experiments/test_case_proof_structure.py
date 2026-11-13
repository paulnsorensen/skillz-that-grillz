from __future__ import annotations

import pytest

from skillz_experiments._records import proof_problem


@pytest.mark.ac("gh-657:AC-24")
@pytest.mark.parametrize("value,suffix", [
    ({}, None),
    ({"pass": "evals/proofs/a"}, None),
    (None, ""),
    ({"pass": 1}, "/pass"),
    ({"pass": "../outside"}, "/pass"),
    ({"fail": "evals/.hidden"}, "/fail"),
    ({"other": "evals/proofs/a"}, "/other"),
    ({"a/b~c": "evals/proofs/a"}, "/a~1b~0c"),
])
def test_proof_structure_checks_only_metadata(value: object, suffix: str | None) -> None:
    problem = proof_problem(value)
    assert (None if problem is None else problem[0]) == suffix

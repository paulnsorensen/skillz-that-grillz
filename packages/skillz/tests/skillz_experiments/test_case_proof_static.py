from __future__ import annotations

import json
import subprocess
from pathlib import Path
from collections.abc import Callable
from typing import cast
import pytest
from jsonschema import Draft202012Validator

from skillz_experiments._evals_format import parse_evals, parse_journeys
from skillz_experiments._intake import load_draft
from skillz_experiments._schemas import SchemaName, schema

EVAL: dict[str, object] = {"id": 1, "name": "one", "kind": "task", "prompt": "Do one.",
                            "expected_output": "Done.", "files": ["evals/files/input.txt"], "family": "f"}
JOURNEY: dict[str, object] = {"id": "one", "family": "f", "kind": "journey", "request": "Do one.",
                               "brief": "Do one.", "expected": {"must": ["done"]}, "source": "skill"}
DRAFT: dict[str, object] = {"id": "one", "family": "f", "kind": "task", "request": "Do one.",
                             "source": "skill", "files": {"input.txt": "real"}, "expected": {}}


def _schema_accepts(name: SchemaName, value: object) -> bool:
    validate = cast(Callable[[object], bool], Draft202012Validator(schema(name)).is_valid)
    return validate(value)

@pytest.mark.ac("gh-657:AC-26")
@pytest.mark.parametrize("proof,valid", [
    ({}, True), ({"pass": "evals/proofs/one"}, True),
    ({"pass": "evals/proofs/one", "fail": "evals/proofs/two"}, True),
    (None, False), ({"pass": 1}, False), ({"pass": "../bad"}, False),
    ({"pass": "evals/.hidden"}, False), ({"extra": "evals/proofs/one"}, False),
    ({"pass": "evals/proofs\n/ordinary"}, True),
    ({"pass": "evals/proofs\n/.hidden"}, False),
    ({"pass": "evals/proofs\n/has\\back"}, False),
    ({"pass": "evals/proofs\n/has\x00nul"}, False),
])
def test_strict_parsers_and_schemas_agree_without_proof_io(
        monkeypatch: pytest.MonkeyPatch, proof: object, valid: bool) -> None:
    def no_process(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("static check started a process")
    monkeypatch.setattr(subprocess, "Popen", no_process)
    evals = {"skill_name": "skill", "evals": [EVAL | {"proof": proof}]}
    journeys = [JOURNEY | {"proof": proof}]
    draft = [DRAFT | {"proof": proof}]
    assert (not parse_evals(evals, strict=True).errors) is valid
    assert (not parse_journeys(journeys, strict=True).errors) is valid
    assert _schema_accepts("evals", evals) is valid
    assert _schema_accepts("journeys", journeys) is valid
    assert _schema_accepts("draft", draft) is valid


@pytest.mark.ac("gh-657:AC-27")
def test_normal_draft_loading_carries_malformed_proof_without_rejecting_it(tmp_path: Path) -> None:
    path = tmp_path / "cases.draft.json"
    _ = path.write_text(json.dumps([DRAFT | {"proof": None}]))
    cases = load_draft(path)
    assert len(cases) == 1 and cases[0].proof is None
    assert cases[0].proof_origin == (str(path), "/0/proof")
    assert not _schema_accepts("draft", [DRAFT | {"proof": None}])
    from skillz_experiments._intake import DRAFT_LIMIT
    with path.open("ab") as stream:
        _ = stream.truncate(DRAFT_LIMIT + 1)
    with pytest.raises(ValueError, match="bounded regular file"):
        _ = load_draft(path)


@pytest.mark.ac("gh-657:AC-26")
def test_strict_metadata_check_never_reads_a_proof_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    def no_filesystem(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("strict proof metadata check touched filesystem")
    monkeypatch.setattr(os, "open", no_filesystem)
    monkeypatch.setattr(os, "scandir", no_filesystem)
    missing = "evals/proofs/not-on-disk"
    assert not parse_evals({"skill_name": "skill", "evals": [EVAL | {"proof": {"pass": missing}}]},
                           strict=True).errors
    assert not parse_journeys([JOURNEY | {"proof": {"fail": missing}}], strict=True).errors


@pytest.mark.ac("gh-657:AC-23")
def test_journey_index_sink_keeps_original_position_after_invalid_entry() -> None:
    indices: list[int] = []
    document = [{"id": "discarded", "kind": "journey"}, JOURNEY | {"proof": {"pass": "../bad"}}]
    parsed = parse_journeys(document, strict=False, indices=indices)
    assert len(parsed.errors) == 1
    assert len(parsed.cases) == 1 and parsed.cases[0]["id"] == "one"
    assert indices == [1]

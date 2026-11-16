"""Real packaged validate command: which proof a seed, a draft, or a selected kind checks."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import pytest

from test_case_proof_cli import _target, _validate  # pyright: ignore[reportPrivateUsage]


@pytest.mark.ac("gh-657:AC-31")
def test_seed_and_rewrite_selection_keep_case_specific_proof(tmp_path: Path) -> None:
    target, _ = _target(tmp_path)
    code, report = _validate(target, None, "--kind", "rewrite")
    bad = [item for item in cast(list[dict[str, object]], report["findings"])
           if item["code"] == "case-proof-score-mismatch"]
    assert code == 1 and len(bad) == 1 and bad[0]["pointer"] == "/evals/1/proof/pass"
    assert cast(dict[str, object], bad[0]["data"])["selected_kind"] == "rewrite"


@pytest.mark.ac("gh-657:AC-23")
def test_dropped_seed_case_keeps_original_proof_pointer_without_split_count(tmp_path: Path) -> None:
    target, _ = _target(tmp_path)
    evals_file = target / "evals/evals.json"
    document = cast(dict[str, object], json.loads(evals_file.read_text()))
    entries = cast(list[dict[str, object]], document["evals"])
    _ = entries[0].pop("family")
    entries[0]["proof"] = {"pass": "../bad"}
    _ = evals_file.write_text(json.dumps(document))
    code, report = _validate(target)
    findings = cast(list[dict[str, object]], report["findings"])
    malformed = [item for item in findings if item["code"] == "case-proof-invalid-reference"]
    assert code == 1
    assert len(malformed) == 1
    assert malformed[0]["pointer"] == "/evals/0/proof/pass"
    assert cast(dict[str, object], malformed[0]["data"])["case_id"] == "a-0"
    assert cast(dict[str, object], report["cases"])["count"] == 14
    retained = [item for item in findings if item["code"] == "case-proof-score-mismatch"]
    assert len(retained) == 1
    assert retained[0]["file"] == "evals/evals.json"
    assert retained[0]["pointer"] == "/evals/1/proof/pass"
    assert cast(dict[str, object], retained[0]["data"])["case_id"] == "a-1"
    assert cast(dict[str, object], retained[0]["data"])["selected_kind"] == "task"


@pytest.mark.ac("gh-657:AC-23")
@pytest.mark.ac("gh-657:AC-24")
def test_draft_does_not_suppress_seed_metadata_or_execute_seed_proof(tmp_path: Path) -> None:
    target, draft = _target(tmp_path)
    evals_file = target / "evals/evals.json"
    document = cast(dict[str, object], json.loads(evals_file.read_text()))
    entries = cast(list[dict[str, object]], document["evals"])
    _ = entries[0].pop("family")
    entries[0]["proof"] = {"pass": "../bad"}
    entries[1]["proof"] = {"pass": "evals/proofs/missing-seed"}
    _ = evals_file.write_text(json.dumps(document))
    _ = (target / "evals/proofs/a-1/pass/output/answer.txt").write_text("pass\n")
    code, report = _validate(target, draft)
    findings = cast(list[dict[str, object]], report["findings"])
    malformed = [item for item in findings if item["code"] == "case-proof-invalid-reference"]
    assert code == 1 and len(malformed) == 1
    assert malformed[0]["file"] == "evals/evals.json"
    assert malformed[0]["pointer"] == "/evals/0/proof/pass"
    assert cast(dict[str, object], malformed[0]["data"])["selected_kind"] is None
    assert not [item for item in findings if item["code"] in ("case-proof-invalid-fixture", "case-family-missing")]
    assert cast(dict[str, object], report["cases"])["source"] == "draft"


@pytest.mark.ac("gh-657:AC-23")
def test_shared_fixture_draft_preserves_case_proof_pointer(tmp_path: Path) -> None:
    target, draft = _target(tmp_path)
    cases = cast(list[dict[str, object]], json.loads(draft.read_text()))
    for case in cases:
        case["files"] = {"evals/files/input.txt": None}
    _ = draft.write_text(json.dumps({"fixtures": {"evals/files/input.txt": "real\n"}, "cases": cases}))
    code, report = _validate(target, draft)
    bad = [item for item in cast(list[dict[str, object]], report["findings"])
           if item["code"] == "case-proof-score-mismatch"]
    assert code == 1 and len(bad) == 1
    assert bad[0]["pointer"] == "/cases/1/proof/pass"


@pytest.mark.ac("gh-657:AC-31")
def test_custom_journey_kind_probes_its_own_trace_and_not_task_proofs(tmp_path: Path) -> None:
    target, _ = _target(tmp_path)
    contract_file = target / "evals/autoimprove.json"
    contract = cast(dict[str, object], json.loads(contract_file.read_text()))
    kinds = cast(dict[str, object], contract["kinds"])
    script = ("import json\nfrom pathlib import Path\n"
              "answer = Path('output/answer.txt').read_text().strip()\n"
              "trace = Path('trace/events.jsonl')\n"
              "print(json.dumps({'score': 1 if answer == 'pass' and trace.is_file() else 0}))\n")
    kinds["custom"] = {"grader": "journey", "argv": [sys.executable, "-I", "-c", script], "rubric": "Grade."}
    _ = contract_file.write_text(json.dumps(contract))
    journeys: list[dict[str, object]] = []
    for family in "abcde":
        for index in range(3):
            name = f"j-{family}-{index}"
            proof = {"pass": "evals/proofs/journey-pass"} if name == "j-a-0" else {}
            journeys.append({"id": name, "family": family, "kind": "custom", "request": f"Do {name}.",
                             "brief": "Talk.", "expected": {"must": ["done"]}, "source": "skill", "proof": proof})
    _ = (target / "evals/journeys.json").write_text(json.dumps(journeys))
    proof_root = target / "evals/proofs/journey-pass"
    (proof_root / "output").mkdir(parents=True)
    (proof_root / "trace").mkdir()
    _ = (proof_root / "output/answer.txt").write_text("bad")
    _ = (proof_root / "trace/events.jsonl").write_text("")
    code, report = _validate(target, None, "--kind", "custom")
    findings = cast(list[dict[str, object]], report["findings"])
    bad = [item for item in findings if item["code"] == "case-proof-score-mismatch"]
    assert code == 1 and len(bad) == 1 and bad[0]["pointer"] == "/0/proof/pass"
    assert cast(dict[str, object], bad[0]["data"])["selected_kind"] == "custom"
    assert not [item for item in findings if item["code"] == "case-proof-missing"
                and str(cast(dict[str, object], item["data"])["case_id"]).startswith("a-")]

"""Real packaged validate command with case-local proof fixtures."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

SCRIPT = ("import json\nfrom pathlib import Path\n"
          "answer = Path('output/answer.txt').read_text().strip()\n"
          "input_file = Path('evals/files/input.txt')\n"
          "original = input_file.read_text().strip() if input_file.exists() else 'real'\n"
          "print(json.dumps({'score': 1 if answer == 'pass' and original == 'real' else 0}))\n")


def _target(root: Path) -> tuple[Path, Path]:
    target = root / "skill"
    (target / "evals/files").mkdir(parents=True)
    _ = (target / "SKILL.md").write_text("---\nname: skill\ndescription: Test skill.\n---\n# Skill\n")
    _ = (target / "evals/files/input.txt").write_text("real\n")
    gate = target / "evals/gates/check.py"
    gate.parent.mkdir()
    _ = gate.write_text(SCRIPT)
    for side, answer in (("pass", "pass"), ("fail", "fail")):
        directory = target / "evals/gates/check.fixtures" / side / "output"
        directory.mkdir(parents=True)
        _ = (directory / "answer.txt").write_text(answer + "\n")
    kinds = {"task": {"grader": "command", "gate": "evals/gates/check.py"},
             "rewrite": {"grader": "hybrid", "argv": [sys.executable, "-I", "-c", SCRIPT], "rubric": "Grade the output."}}
    contract: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": "skill", "invocation": "$skill",
                "kinds": kinds, "editable": []}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(contract))
    entries: list[dict[str, object]] = []
    draft: list[dict[str, object]] = []
    for family in "abcde":
        for index in range(3):
            name = f"{family}-{index}"
            proof = {side: f"evals/proofs/{name}/{side}" for side in ("pass", "fail")} if name in ("a-0", "a-1") else {}
            entries.append({"id": len(entries), "name": name, "family": family, "kind": "task", "prompt": f"Do {name}.",
                            "expected_output": "out", "files": ["evals/files/input.txt"], "proof": proof})
            draft.append({"id": name, "family": family, "kind": "task", "request": f"Do {name}.", "source": "skill",
                          "files": {"evals/files/input.txt": "real\n"},
                          "expected": {"expected_output": "out", "assertions": []}, "proof": proof})
            for side, answer in (("pass", "bad" if name == "a-1" else "pass"), ("fail", "fail")):
                if not proof:
                    continue
                directory = target / str(proof[side]) / "output"
                directory.mkdir(parents=True)
                _ = (directory / "answer.txt").write_text(answer + "\n")
    _ = (target / "evals/evals.json").write_text(json.dumps({"skill_name": "skill", "evals": entries}))
    draft_path = root / "cases.draft.json"
    _ = draft_path.write_text(json.dumps(draft))
    return target, draft_path


def _validate(target: Path, draft: Path | None = None, *extra: str) -> tuple[int, dict[str, object]]:
    executable = Path(sys.executable).parent / "skillz-experiment"
    command = [str(executable), "validate", "--target", str(target)]
    if draft is not None:
        command += ["--draft", str(draft)]
    result = subprocess.run([*command, *extra], capture_output=True, text=True, check=False)
    return result.returncode, cast(dict[str, object], json.loads(result.stdout))


@pytest.mark.ac("gh-657:AC-7")
@pytest.mark.ac("gh-657:AC-8")
def test_packaged_validate_names_the_bad_case_then_accepts_a_proof_only_repair(tmp_path: Path) -> None:
    target, draft = _target(tmp_path)
    code, report = _validate(target, draft)
    findings = cast(list[dict[str, object]], report["findings"])
    bad = [item for item in findings if item["code"] == "case-proof-score-mismatch"]
    assert code == 1 and report["ok"] is False
    assert len(bad) == 1 and bad[0]["pointer"] == "/1/proof/pass"
    assert cast(dict[str, object], bad[0]["data"])["case_id"] == "a-1"
    _ = (target / "evals/proofs/a-1/pass/output/answer.txt").write_text("pass\n")
    code, report = _validate(target, draft)
    findings = cast(list[dict[str, object]], report["findings"])
    assert code == 0 and report["ok"] is True, [item for item in findings if item["severity"] == "error"]
    assert all(item["code"] == "case-proof-missing" for item in findings), findings


@pytest.mark.ac("gh-657:AC-31")
def test_seed_and_rewrite_selection_keep_case_specific_proof(tmp_path: Path) -> None:
    target, _ = _target(tmp_path)
    code, report = _validate(target, None, "--kind", "rewrite")
    bad = [item for item in cast(list[dict[str, object]], report["findings"])
           if item["code"] == "case-proof-score-mismatch"]
    assert code == 1 and len(bad) == 1 and bad[0]["pointer"] == "/evals/1/proof/pass"
    assert cast(dict[str, object], bad[0]["data"])["selected_kind"] == "rewrite"

@pytest.mark.ac("gh-657:AC-11")
def test_proof_edits_do_not_change_approved_or_candidate_data(tmp_path: Path) -> None:
    from skillz_experiments._candidate import Candidate
    from skillz_experiments._contract import load_contract
    from skillz_experiments._intake import case_hash, freeze, load_draft, split_cases
    from skillz_experiments._seed import seed_cases

    target, draft = _target(tmp_path)
    contract = load_contract(target)
    def snapshot() -> tuple[str, bytes, str, str, dict[str, object]]:
        cases = split_cases(load_draft(draft), 17)
        identity = case_hash(cases, 17)
        frozen = tmp_path / "cases.json"
        freeze(frozen, cases, 17, identity)
        seeded = seed_cases(target)
        assert seeded is not None
        return identity, frozen.read_bytes(), Candidate.capture(target, [], contract).identity, contract.identity, {
            "count": len(seeded), "files": dict(cases[0].files)}
    before = snapshot()
    document = cast(list[dict[str, object]], json.loads(draft.read_text()))
    document[0]["proof"] = {"pass": "evals/proofs/replacement"}
    document[1]["proof"] = None
    _ = draft.write_text(json.dumps(document))
    _ = (target / "evals/proofs/a-0/pass/output/answer.txt").write_text("changed\n")
    assert snapshot() == before
    assert load_draft(draft)[1].proof is None


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


@pytest.mark.ac("gh-657:AC-24")
def test_malformed_loaded_proof_survives_failed_contract_and_kind_resolution(tmp_path: Path) -> None:
    target, draft = _target(tmp_path)
    contract_file = target / "evals/autoimprove.json"
    contract = cast(dict[str, object], json.loads(contract_file.read_text()))
    kinds = cast(dict[str, object], contract["kinds"])
    kinds["journey"] = {"grader": "journey", "argv": [sys.executable, "-I", "-c", SCRIPT], "rubric": "Grade."}
    _ = contract_file.write_text(json.dumps(contract))
    cases = cast(list[dict[str, object]], json.loads(draft.read_text()))
    cases[0]["proof"] = {"pass": "../bad"}
    cases.append({"id": "journey-one", "family": "a", "kind": "journey", "request": "Do work.",
                  "brief": "Describe work.", "expected": {"must": ["done"]}, "source": "skill",
                  "proof": {"fail": "evals/../bad"}})
    cases.append({"id": "pending-one", "family": "a", "kind": "trigger", "request": "Try.",
                  "source": "skill", "proof": {"pass": "../pending"}})
    _ = draft.write_text(json.dumps(cases))
    for extra in (("--kind", "absent"), ()):
        if not extra:
            _ = (target / "evals/autoimprove.json").write_text("{broken")
        code, report = _validate(target, draft, *extra)
        invalid = [item for item in cast(list[dict[str, object]], report["findings"])
                   if item["code"] == "case-proof-invalid-reference"]
        assert code == 1
        assert {item["pointer"] for item in invalid} == {"/0/proof/pass", "/15/proof/fail", "/16/proof/pass"}
        assert all(cast(dict[str, object], item["data"])["selected_kind"] is None for item in invalid)


@pytest.mark.ac("gh-657:AC-10")
def test_offline_run_preparation_excludes_absent_valid_and_malformed_proof_from_identity(
        tmp_path: Path, make_target: object, write_draft: object) -> None:
    from collections.abc import Callable
    from skillz_experiments._records import read
    from skillz_experiments.run import Stop, run
    maker = cast(Callable[..., Path], make_target)
    drafter = cast(Callable[..., list[str]], write_draft)
    target = maker(tmp_path)
    outcomes: list[tuple[str, bytes, str, str, str]] = []
    for label, proof in (("absent", None), ("valid", {"pass": "evals/proofs/example"}),
                         ("malformed", {"pass": "../bad"})):
        out = tmp_path / label
        _ = drafter(out)
        draft = out / "cases.draft.json"
        document = cast(list[dict[str, object]], json.loads(draft.read_text()))
        if proof is not None:
            document[0]["proof"] = proof
            _ = draft.write_text(json.dumps(document))
        with pytest.raises(Stop) as approval:
            _ = run(target, out, "local-test", live=True)
        assert approval.value.code == "cases-unapproved"
        identity = cast(str, approval.value.data["case_hash"])
        with pytest.raises(Stop) as budget:
            _ = run(target, out, "local-test", live=True, approve_cases=identity)
        calls = cast(dict[str, int], budget.value.data["estimate"])["calls"]
        with pytest.raises(Stop) as ready:
            _ = run(target, out, "local-test", approve_cases=identity, approve_budget=calls)
        assert ready.value.code == "live-required"
        record = read(out / "run.json")
        outcomes.append((identity, (out / "cases.json").read_bytes(), cast(str, record["dataset_hash"]),
                         cast(str, record["seed_hash"]), cast(str, record["contract_hash"])))
    assert outcomes[0] == outcomes[1] == outcomes[2]


@pytest.mark.ac("gh-657:AC-17")
def test_real_grader_cannot_use_proof_output_to_replace_case_input(tmp_path: Path) -> None:
    target, draft = _target(tmp_path)
    cases = cast(list[dict[str, object]], json.loads(draft.read_text()))
    files = cast(dict[str, object], cases[0]["files"])
    files["evals/files/input.txt"] = "tampered"
    _ = draft.write_text(json.dumps(cases))
    code, report = _validate(target, draft)
    mismatches = [item for item in cast(list[dict[str, object]], report["findings"])
                  if item["code"] == "case-proof-score-mismatch"]
    assert code == 1
    assert any(cast(dict[str, object], item["data"])["case_id"] == "a-0" and
               cast(dict[str, object], item["data"])["side"] == "pass" for item in mismatches)

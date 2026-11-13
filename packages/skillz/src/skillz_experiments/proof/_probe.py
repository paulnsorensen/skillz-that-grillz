"""Case-local offline proof findings for validate."""
from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from skillz_experiments._contract import Grader
from skillz_experiments._intake import DraftCase
from skillz_experiments._records import proof_problem
from skillz_experiments.proof._execute import ProofExecutionError, score
from skillz_experiments.proof._fixtures import fixture_files, stage


def _finding(case: DraftCase, kind: str | None, code: str, severity: str, message: str,
             suffix: str = "", side: str | None = None, reference: str | None = None,
             expected: int | None = None, observed: float | None = None) -> dict[str, object]:
    file, pointer = case.proof_origin or ("", "")
    data: dict[str, object] = {"case_id": case.identifier, "authored_kind": case.kind, "selected_kind": kind}
    if side is not None:
        data["side"] = side
    if reference is not None:
        data["reference"] = reference
    if expected is not None:
        data["expected"] = expected
    if observed is not None:
        data["observed"] = observed
    return {"code": code, "severity": severity, "file": file, "pointer": pointer + suffix,
            "message": f"case {case.identifier}: {message}", "fix": "repair this case's proof metadata or fixture",
            "data": data}


def _check_side(target: Path, case: DraftCase, kind: str, grader: Grader | None, side: str,
                reference: str, path: str) -> dict[str, object] | None:
    if grader is None or not grader.argv:
        return _finding(case, kind, "case-proof-unsupported", "error", "selected grader has no executable argv",
                        f"/{side}", side, reference)
    try:
        fixture = fixture_files(target, reference, case.journey is not None)
        with tempfile.TemporaryDirectory(prefix="skillz-proof-") as directory:
            workspace = Path(directory)
            stage(workspace, case.files, fixture, case.journey is not None)
            observed = score(workspace, grader.argv, path)
    except ProofExecutionError as error:
        return _finding(case, kind, "case-proof-execution", "error", str(error), f"/{side}", side, reference)
    except (ValueError, OSError) as error:
        return _finding(case, kind, "case-proof-invalid-fixture", "error", str(error), f"/{side}", side, reference)
    expected = 1 if side == "pass" else 0
    if observed != expected:
        return _finding(case, kind, "case-proof-score-mismatch", "error",
                        f"{side} proof scored {observed:g}; expected {expected}", f"/{side}", side, reference,
                        expected, observed)
    return None


def probe_case_proofs(target: Path, cases: Sequence[DraftCase], graders: Mapping[str, Grader],
                      selected: Mapping[str, str]) -> list[dict[str, object]]:
    """Check all proof metadata, then probe only selected cases with valid metadata."""
    path = os.environ.get("PATH", os.defpath)
    findings: list[dict[str, object]] = []
    valid: list[tuple[DraftCase, str]] = []
    for case in cases:
        kind = selected.get(case.identifier)
        if (problem := proof_problem(case.proof)) is not None:
            suffix, reason = problem
            side = suffix[1:] if suffix in ("/pass", "/fail") else None
            reference = cast(dict[str, object], case.proof).get(side) if side is not None and isinstance(case.proof, dict) else None
            findings.append(_finding(case, kind, "case-proof-invalid-reference", "error", reason, suffix,
                                     side, reference if isinstance(reference, str) else None))
        elif kind is not None:
            valid.append((case, kind))
    for case, kind in valid:
        proof = cast(dict[str, str], case.proof)
        for side in ("pass", "fail"):
            reference = proof.get(side)
            if reference is None:
                findings.append(_finding(case, kind, "case-proof-missing", "warning",
                                         f"{side} proof is absent", f"/{side}", side))
                continue
            found = _check_side(target, case, kind, graders.get(kind), side, reference, path)
            if found is not None:
                findings.append(found)
    return findings

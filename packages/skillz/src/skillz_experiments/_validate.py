"""Check a skill's eval files offline: no model call, no host check, no write.

Every stage runs on its own and reports its findings, so one report shows all the problems at once.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from skillz_experiments._cases import CodedError, journey_of, mapping, text_map
from skillz_experiments._contract import LOCATION, Contract, load_contract, parse
from skillz_experiments._evals_format import TASK_FIXTURE
from skillz_experiments._gate_files import expand_gates, probe_gates
from skillz_experiments._intake import (
    HOLDOUT_MINIMUM, MINIMUM_FAMILIES, TRAIN_FAMILIES_MINIMUM, TRAIN_MINIMUM, DraftCase, check_helper_input,
    intake_contract, load_draft, split_cases, too_few,
)
from skillz_experiments._seed import EVALS, JOURNEYS, check_coverage, fixture_error, fixture_misses, seed_cases, seed_family
from skillz_experiments.run import DEFAULT_SEED, resolve_statistics, scored_kinds

Finding = dict[str, object]
FAILURES = (CodedError, ValueError, OSError)


def _finding(code: str, severity: str, file: str, message: str, fix: str = "", pointer: str = "",
             data: Mapping[str, object] | None = None) -> Finding:
    found: Finding = {"code": code, "severity": severity, "file": file, "pointer": pointer, "message": message, "fix": fix}
    if data is not None:
        found["data"] = dict(data)
    return found


def _failure(error: Exception, file: str) -> Finding:
    if isinstance(error, CodedError):
        details = error.details or {}
        return _finding(error.code, "error", file, str(error), str(details.get("fix", "")), data=error.details)
    return _finding("invalid", "error", file, str(error))


def _contract(target: Path, findings: list[Finding]) -> tuple[Contract | None, str | None, str]:
    """Return the contract, its declared status, and its source. A draft contract loads with a warning."""
    path = target / LOCATION
    if not os.path.lexists(path):
        try:
            return intake_contract(target), None, "intake"
        except FAILURES as error:
            findings.append(_failure(error, LOCATION))
            return None, None, "intake"
    try:
        return load_contract(target), "approved", "skill"
    except CodedError as error:
        if error.code != "contract-unapproved":
            findings.append(_failure(error, LOCATION))
            return None, None, "skill"
        findings.append(_finding(error.code, "warning", LOCATION, str(error), "set status to approved after review",
                                 "/status"))
    except FAILURES as error:
        findings.append(_failure(error, LOCATION))
        return None, None, "skill"
    try:
        raw = mapping(cast(object, json.loads(path.read_text(encoding="utf-8"))))
        return parse(expand_gates({**raw, "status": "approved"}, target), "skill"), "draft", "skill"
    except FAILURES as error:
        findings.append(_failure(error, LOCATION))
        return None, "draft", "skill"


def _from_seed(seeded: Sequence[Mapping[str, object]], journeys: Sequence[str], findings: list[Finding],
               scores_task: bool = True) -> list[DraftCase]:
    """Map the seed cases to draft cases, the way an agent copies them into the draft.

    A case with no family joins no split, as in `seed_projection`. Each one adds a finding.
    A task case with no files stays in the split, and adds the `cases-invalid` finding that `run` stops with.
    Unless `scores_task`, the run does not score a task case: it takes `family = id` and needs no files (gh-701).
    """
    drafted: list[DraftCase] = []
    for case in seeded:
        kind = str(case["kind"])
        family = seed_family(case)
        if family is None and kind == "task" and not scores_task:
            family = str(case["id"])
        if family is None:
            findings.append(_finding(
                "case-family-missing", "error", EVALS,
                f"the task entry {case['id']} has no family, so it joins no split",
                "set family on the entry; the draft needs a family for it"))
            continue
        item = dict(case)
        if kind == "task" and scores_task and not item.get("files"):
            findings.append(_finding("cases-invalid", "error", EVALS, f"the task entry {case['id']} has no files, so run "
                                     + "refuses its case", TASK_FIXTURE))
        drafted.append(DraftCase(str(case["id"]), family, kind, str(case["request"]), "skill",
                                 text_map(item.get("files", {})), case.get("expected"), journey_of(item, kind in journeys)))
    return drafted


def _counts(cases: Sequence[DraftCase]) -> dict[str, object]:
    return {"count": len(cases), "kinds": dict(sorted(Counter(case.kind for case in cases).items())),
            "families": dict(sorted(Counter(case.family for case in cases).items()))}


def _fixture_findings(target: Path, drafted: Sequence[DraftCase], file_of: Callable[[DraftCase], str],
                      findings: list[Finding]) -> None:
    """Add one `case-fixture-missing` finding for each file whose cases name a known fixture that they do not stage."""
    by_file: dict[str, list[tuple[DraftCase, str]]] = {}
    for case, path in fixture_misses(target, drafted):
        by_file.setdefault(file_of(case), []).append((case, path))
    for file, misses in by_file.items():
        findings.append(_failure(fixture_error(misses), file))


def _gate_findings(target: Path, journeys: Sequence[str], drafted: Sequence[DraftCase] | None) -> list[Finding]:
    """Run each gate file of the contract on its fixtures and on a null trace; see `probe_gates`."""
    path = target / LOCATION
    if not os.path.lexists(path):
        return []
    raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    must = {case.kind for case in drafted or [] if case.kind in journeys and isinstance(case.expected, dict)
            and cast(dict[str, object], case.expected).get("must")}
    return [_finding(item.code, item.severity, item.gate, item.message, item.fix) for item in probe_gates(target, raw, must)]


def validate(target: Path, draft: Path | None = None, seed: int | None = None,
             kinds: Sequence[str] = ()) -> dict[str, object]:
    """Report every problem of the target's eval files and the split that a run would build. Write nothing."""
    findings: list[Finding] = []
    contract, status, source = _contract(target, findings)
    journeys = contract.journey_kinds() if contract is not None else ("journey",)

    chosen: tuple[str, ...] | None = None
    if contract is not None:
        try:
            chosen = scored_kinds(contract, kinds)
        except FAILURES as error:
            findings.append(_failure(error, LOCATION))
    scores_task = chosen is None or any(kind not in journeys for kind in chosen)
    seeded: list[dict[str, object]] | None = None
    try:
        seeded = seed_cases(target)
    except FAILURES as error:
        findings.append(_failure(error, JOURNEYS if str(error).startswith(JOURNEYS) else EVALS))

    drafted: list[DraftCase] | None = None
    origin = "none"
    if draft is not None:
        origin = "draft"
        try:
            drafted = load_draft(draft, journeys, scores_task)
        except FAILURES as error:
            findings.append(_failure(error, str(draft)))
        if drafted is not None:
            if contract is not None:
                try:
                    check_helper_input(contract, drafted)
                except FAILURES as error:
                    findings.append(_failure(error, str(draft)))
            try:
                check_coverage(target, drafted)
            except FAILURES as error:
                findings.append(_failure(error, str(draft)))
            _fixture_findings(target, drafted, lambda _case: str(draft), findings)
    elif seeded is not None:
        origin = "seed"
        try:
            drafted = _from_seed(seeded, journeys, findings, scores_task)
        except FAILURES as error:
            findings.append(_failure(error, EVALS))
        else:
            _fixture_findings(target, drafted, lambda case: JOURNEYS if case.journey is not None else EVALS, findings)

    if contract is not None:
        findings.extend(_gate_findings(target, journeys, drafted))

    split_seed = DEFAULT_SEED if seed is None else seed
    split: dict[str, object] = {"seed": split_seed, "train": None, "validation": None, "holdout": None,
                                "holdout_minimum": HOLDOUT_MINIMUM, "families_minimum": MINIMUM_FAMILIES,
                                "train_minimum": TRAIN_MINIMUM, "train_families_minimum": TRAIN_FAMILIES_MINIMUM}
    if contract is not None and chosen is not None and drafted is not None:
        names = set(contract.journey_kinds())
        plain = [kind for kind in chosen if kind not in names]
        extra = [] if kinds else [kind for kind in names if kind not in chosen]
        where = str(draft) if draft is not None else EVALS
        try:
            planned = split_cases(drafted, split_seed, plain[0] if plain else None,
                                  [kind for kind in chosen if kind in names], extra)
        except FAILURES as error:
            findings.append(_failure(error, where))
        else:
            for name in ("train", "validation", "holdout"):
                split[name] = sum(case.split == name and case.scored and case.kind in chosen for case in planned)
            shortfall = too_few(Counter(case.family for case in planned if case.scored and case.kind in chosen))
            if shortfall is not None:
                findings.append(_failure(shortfall, where))
        try:
            budgets = resolve_statistics(contract, {}).family_budgets
        except FAILURES as error:
            findings.append(_failure(error, LOCATION))
        else:
            families = {case.family for case in drafted}
            for name in sorted(set(budgets) - families):
                findings.append(_finding(
                    "contract-family-unknown", "error", LOCATION,
                    f"the contract family_budgets names no case family: {name}; the case families are {', '.join(sorted(families))}",
                    "rename the family_budgets entry to a case family, or remove it", f"/statistics/family_budgets/{name}"))

    return {"ok": not any(found["severity"] == "error" for found in findings),
            "contract": {"source": source, "status": status, "kinds": sorted(contract.kinds) if contract else [],
                         "hash": contract.identity if contract else None},
            "cases": {"source": origin} | _counts(drafted or []),
            "split": split, "findings": findings}

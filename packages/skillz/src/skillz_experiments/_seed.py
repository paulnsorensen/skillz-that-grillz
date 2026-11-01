"""Seed the case draft from the skill's own `evals/evals.json` and `evals/journeys.json`.

The runner never writes the draft. It maps each eval entry and each journey case to a seed case and writes
the map to `RUN/cases.seed.json`, with each fixture text once under `fixtures`. The `cases-missing` stop names
that file and shows only counts, because the fixtures can be large. The coverage check stops the run when the
draft drops or changes an entry.
To drop an entry, remove it from its file.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from skillz_experiments._cases import CodedError, loads_untrusted, read_text, relative, text_map
from skillz_experiments._evals_format import EvalEntry, parse_evals, parse_journeys
from skillz_experiments._intake import DRAFT_LIMIT, PENDING_KINDS, DraftCase, projection
from skillz_experiments._records import share_files

EVALS = "evals/evals.json"
JOURNEYS = "evals/journeys.json"
FIXTURES = "evals/files/"
SEED_NAME = "cases.seed.json"
SEED_LIMIT = DRAFT_LIMIT // 2
READ_LIMIT = SEED_LIMIT


def _case(entry: EvalEntry) -> dict[str, object]:
    case: dict[str, object] = {"id": entry.identifier, "request": entry.prompt, "kind": entry.kind, "source": "skill"}
    if entry.family is not None:
        case["family"] = entry.family
    elif entry.kind != "task":
        case["family"] = entry.identifier
    return case


def seed_family(case: Mapping[str, object]) -> str | None:
    """Return the family of a seed case, or None when it has none. A case with no family joins no split."""
    family = case.get("family")
    return None if family is None else str(family)


def _entries(target: Path) -> list[EvalEntry] | None:
    """Return the validated entries in file order, or None when the skill has no `evals.json`."""
    if not (target / EVALS).is_symlink() and not (target / EVALS).exists():
        return None
    try:
        parsed = parse_evals(loads_untrusted(read_text(target, EVALS, READ_LIMIT)), strict=False)
    except ValueError as error:
        raise CodedError("evals-unreadable", f"{EVALS} cannot seed the cases: {error}") from None
    if parsed.errors:
        raise CodedError("evals-unreadable", f"{EVALS} cannot seed the cases: {'; '.join(parsed.errors)}")
    return list(parsed.entries)


def _journeys(target: Path) -> list[dict[str, object]] | None:
    """Return the validated journey cases in file order, or None when the skill has no `journeys.json`."""
    if not (target / JOURNEYS).is_symlink() and not (target / JOURNEYS).exists():
        return None
    try:
        parsed = parse_journeys(loads_untrusted(read_text(target, JOURNEYS, READ_LIMIT)), strict=False)
    except ValueError as error:
        raise CodedError("evals-unreadable", f"{JOURNEYS} cannot seed the cases: {error}") from None
    if parsed.errors:
        raise CodedError("evals-unreadable", f"{JOURNEYS} cannot seed the cases: {'; '.join(parsed.errors)}")
    return [dict(case) for case in parsed.cases]


def _fixture(target: Path, path: str, cache: dict[str, str]) -> str:
    """Return the text of a fixture under `evals/files/`. Read each distinct path once."""
    if path not in cache:
        if not relative(path).startswith(FIXTURES):
            raise ValueError(f"{path} must be under {FIXTURES}")
        cache[path] = read_text(target, path, READ_LIMIT)
        if sum(len(text.encode("utf-8")) for text in cache.values()) > SEED_LIMIT:
            raise ValueError(f"the seed is too large; its fixtures pass {SEED_LIMIT} bytes")
    return cache[path]


def check_unique(identifiers: Sequence[object]) -> None:
    """Raise `CodedError` `evals-unreadable` when a case id repeats across `evals.json` and `journeys.json`."""
    seen: set[object] = set()
    for identifier in identifiers:
        if identifier in seen:
            raise CodedError("evals-unreadable", f"{EVALS} and {JOURNEYS} cannot seed the cases: duplicate case id {identifier!r}")
        seen.add(identifier)


def seed_document(cases: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Return the seed file: the cases, which list fixture paths, and each fixture text once under `fixtures`."""
    fixtures: dict[str, str] = {}
    return {"cases": share_files(cases, fixtures), "fixtures": fixtures}


def seed_cases(target: Path) -> list[dict[str, object]] | None:
    """Return one seed case per eval entry, then one per journey, or None when the skill has neither file.

    A task case also holds its fixtures and reference. Raise `CodedError` `evals-unreadable` when a
    file or a fixture it lists is unsafe or malformed, when an id repeats across the files, or when the
    `seed_document` passes `SEED_LIMIT` bytes of JSON. A fixture that many cases list counts once.
    """
    entries = _entries(target)
    journeys = _journeys(target)
    if entries is None and journeys is None:
        return None
    cases: list[dict[str, object]] = []
    cache: dict[str, str] = {}
    for header in entries or []:
        case = _case(header)
        if header.kind == "task":
            try:
                case["files"] = text_map({path: _fixture(target, path, cache) for path in header.files})
            except ValueError as error:
                raise CodedError("evals-unreadable", f"{EVALS} cannot seed the cases: entry {header.identifier}: {error}") from None
            case["expected"] = {"expected_output": header.expected_output, "assertions": list(header.assertions)}
        cases.append(case)
    cases.extend(journeys or [])
    check_unique([case["id"] for case in cases])
    size = len((json.dumps(seed_document(cases), sort_keys=True, indent=2) + "\n").encode("utf-8"))
    if size > SEED_LIMIT:
        raise CodedError("evals-unreadable", f"{EVALS} and {JOURNEYS} cannot seed the cases: the seed is too large; it passes "
                         + f"{SEED_LIMIT} bytes")
    return cases


def seed_projection(cases: Sequence[Mapping[str, object]], kinds: Collection[str],
                    journeys: Collection[str]) -> dict[str, object]:
    """Project the split minimums over the seed cases that a run of `kinds` scores.

    A non-journey case is scored when `kinds` holds a kind that does not use the journey grader. A journey
    case is scored when its own kind is in `kinds`. A trigger or near-miss case stays pending.
    """
    plain = any(kind not in journeys for kind in kinds)
    size = Counter(family for case in cases if (family := seed_family(case)) is not None
                   and case["kind"] not in PENDING_KINDS and (case["kind"] in kinds if "brief" in case else plain))
    return projection(size)


def seed_summary(cases: Sequence[dict[str, object]], inferred: Mapping[str, str] | None = None,
                 split: Mapping[str, object] | None = None) -> dict[str, object]:
    """Count the seed cases, the journeys, and the cases of each kind and of each family. A case with no family counts in no family.

    `inferred` maps the case id of each entry that had no `kind` to the kind that the reader chose.
    `split` is the projection of the minimums over the scored families, from `seed_projection`.
    """
    summary: dict[str, object] = {
        "cases": len(cases), "journeys": sum("brief" in case for case in cases),
        "kinds": dict(sorted(Counter(str(case["kind"]) for case in cases).items())),
        "families": dict(sorted(Counter(family for case in cases if (family := seed_family(case)) is not None).items())),
        "inferred": dict(inferred or {})}
    if split is not None:
        summary["split"] = dict(split)
    return summary


def seed_inferred(target: Path) -> dict[str, str]:
    """Return the kind that the reader chose for each entry with no `kind`, by case id. Read no fixture."""
    return {entry.identifier: entry.kind for entry in _entries(target) or [] if entry.inferred}


def check_coverage(target: Path, draft: Sequence[DraftCase]) -> None:
    """Raise `cases-uncovered` unless each seed entry is in the draft with its name, kind, and request.

    An entry of `journeys.json` must also keep its `brief`. This reads no fixture.
    """
    drafted = {case.identifier: case for case in draft}
    problems: list[str] = []
    wanted = [(header.identifier, header.kind, header.prompt, None) for header in _entries(target) or []]
    wanted += [(str(case["id"]), str(case["kind"]), str(case["request"]), str(case["brief"]))
               for case in _journeys(target) or []]
    check_unique([identifier for identifier, *_ in wanted])
    for identifier, kind, request, brief in wanted:
        case = drafted.get(identifier)
        if case is None:
            problems.append(f"- {identifier}: missing from the draft")
            continue
        changed = [f"kind is {case.kind!r}, not {kind!r}"] if case.kind != kind else []
        if case.request != request:
            changed.append("request changed")
        if brief is not None and (case.journey is None or case.journey.brief != brief):
            changed.append("brief changed")
        if changed:
            problems.append(f"- {identifier}: {'; '.join(changed)}")
    if problems:
        raise CodedError("cases-uncovered", f"the draft drops or changes entries of {EVALS} or {JOURNEYS}:\n"
                         + "\n".join(problems) + f"\nrestore each entry in the draft, or remove it from {EVALS} or {JOURNEYS}"
                         + " to drop it on purpose, then call run again")

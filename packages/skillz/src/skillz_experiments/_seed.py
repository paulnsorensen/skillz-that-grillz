"""Seed the case draft from the skill's own `evals/evals.json`.

The runner never writes the draft. It maps each eval entry to a seed case and writes the map to
`RUN/cases.seed.json`. The `cases-missing` stop names that file and shows only counts, because the
fixtures can be large. The coverage check stops the run when the draft drops or changes an entry.
To drop an entry, remove it from `evals.json`.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from skillz_experiments._cases import CodedError, loads_untrusted, read_text, relative, text_map
from skillz_experiments._evals_format import EvalEntry, parse_evals
from skillz_experiments._intake import DRAFT_LIMIT, DraftCase

EVALS = "evals/evals.json"
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


def _fixture(target: Path, path: str, cache: dict[str, str]) -> str:
    """Return the text of a fixture under `evals/files/`. Read each distinct path once."""
    if path not in cache:
        if not relative(path).startswith(FIXTURES):
            raise ValueError(f"{path} must be under {FIXTURES}")
        cache[path] = read_text(target, path, READ_LIMIT)
        if sum(len(text.encode("utf-8")) for text in cache.values()) > SEED_LIMIT:
            raise ValueError(f"the seed is too large; its fixtures pass {SEED_LIMIT} bytes")
    return cache[path]


def seed_cases(target: Path) -> list[dict[str, object]] | None:
    """Return one seed case per eval entry in file order, or None when the skill has no `evals.json`.

    A task case also holds its fixtures and reference. Raise `CodedError` `evals-unreadable` when the
    file or a fixture it lists is unsafe or malformed, or when the seed passes `SEED_LIMIT` bytes of JSON.
    """
    entries = _entries(target)
    if entries is None:
        return None
    cases: list[dict[str, object]] = []
    cache: dict[str, str] = {}
    total = 0
    for header in entries:
        case = _case(header)
        if header.kind == "task":
            try:
                case["files"] = text_map({path: _fixture(target, path, cache) for path in header.files})
            except ValueError as error:
                raise CodedError("evals-unreadable", f"{EVALS} cannot seed the cases: entry {header.identifier}: {error}") from None
            case["expected"] = {"expected_output": header.expected_output, "assertions": list(header.assertions)}
        total += len(json.dumps(case))
        if total > SEED_LIMIT:
            raise CodedError("evals-unreadable", f"{EVALS} cannot seed the cases: the seed is too large; it passes "
                             + f"{SEED_LIMIT} bytes at entry {header.identifier}")
        cases.append(case)
    return cases


def seed_summary(cases: Sequence[dict[str, object]], inferred: Mapping[str, str] | None = None) -> dict[str, object]:
    """Count the seed cases, and the cases of each kind and of each family. A case with no family counts in no family.

    `inferred` maps the case id of each entry that had no `kind` to the kind that the reader chose.
    """
    return {"cases": len(cases), "kinds": dict(sorted(Counter(str(case["kind"]) for case in cases).items())),
            "families": dict(sorted(Counter(str(case["family"]) for case in cases if "family" in case).items())),
            "inferred": dict(inferred or {})}


def seed_inferred(target: Path) -> dict[str, str]:
    """Return the kind that the reader chose for each entry with no `kind`, by case id. Read no fixture."""
    return {entry.identifier: entry.kind for entry in _entries(target) or [] if entry.inferred}


def check_coverage(target: Path, draft: Sequence[DraftCase]) -> None:
    """Raise `cases-uncovered` unless each eval entry is in the draft with its name, kind, and request.

    This reads `evals.json` only. It does not read any fixture.
    """
    drafted = {case.identifier: case for case in draft}
    problems: list[str] = []
    for header in _entries(target) or []:
        case = drafted.get(header.identifier)
        if case is None:
            problems.append(f"- {header.identifier}: missing from the draft")
            continue
        changed = [f"kind is {case.kind!r}, not {header.kind!r}"] if case.kind != header.kind else []
        if case.request != header.prompt:
            changed.append("request changed")
        if changed:
            problems.append(f"- {header.identifier}: {'; '.join(changed)}")
    if problems:
        raise CodedError("cases-uncovered", "the draft drops or changes entries of " + EVALS + ":\n"
                         + "\n".join(problems) + "\nrestore each entry in the draft, or remove it from " + EVALS
                         + " to drop it on purpose, then call run again")

"""Case intake. The outer agent drafts cases; this module validates, splits, freezes, and asks once.

No function here calls a model. Trigger and near-miss cases stay pending until an activation grader exists.
"""
from __future__ import annotations

import json
import os
import random
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from skillz_experiments._cases import CodedError, Split, digest, loads_untrusted, string, text_map
from skillz_experiments._contract import LOCATION, Contract, Grader, load_contract

DRAFT_NAME = "cases.draft.json"
KINDS = ("trigger", "near-miss", "task")
SOURCES = ("skill", "session")
PENDING_KINDS = ("trigger", "near-miss")
HOLDOUT_MINIMUM = 6
REQUEST_PREVIEW = 80
MINIMUM_FAMILIES = 3
TASK_RUBRIC = "Grade the response against the reference answer in `expected`. Pass only when it meets the request."
_FIELDS = {"id", "family", "kind", "request", "files", "expected", "source"}
_REQUIRED = {"id", "family", "kind", "request", "source"}
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---", re.DOTALL)


@dataclass(frozen=True)
class DraftCase:
    identifier: str
    family: str
    kind: str
    request: str
    source: str
    files: dict[str, str]
    expected: object

    @property
    def pending(self) -> bool:
        return self.kind in PENDING_KINDS


@dataclass(frozen=True)
class IntakeCase(DraftCase):
    split: Split

    def data(self) -> dict[str, object]:
        item: dict[str, object] = {"id": self.identifier, "family": self.family, "split": self.split,
                                   "kind": self.kind, "request": self.request, "source": self.source}
        if self.files:
            item["files"] = self.files
        if self.expected is not None:
            item["expected"] = self.expected
        return item


def _case(value: object, position: int) -> DraftCase:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in cast(dict[object, object], value)):
        raise ValueError(f"draft case {position} must be a JSON object")
    item = cast(dict[str, object], value)
    name = item.get("id") if isinstance(item.get("id"), str) and item.get("id") else f"#{position}"
    if set(item) - _FIELDS:
        raise ValueError(f"case {name}: unknown fields {', '.join(sorted(set(item) - _FIELDS))}")
    if _REQUIRED - set(item):
        raise ValueError(f"case {name}: missing fields {', '.join(sorted(_REQUIRED - set(item)))}")
    try:
        kind, source = item["kind"], item["source"]
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}")
        files = text_map(item.get("files", {}))
        expected = item.get("expected")
        if kind == "task" and (not files or expected is None):
            raise ValueError("a task case needs files and an expected reference")
        return DraftCase(string(item["id"], "id"), string(item["family"], "family"), cast(str, kind),
                          string(item["request"], "request"), cast(str, source), files, expected)
    except ValueError as error:
        raise ValueError(f"case {name}: {error}") from None


def load_draft(path: Path) -> list[DraftCase]:
    """Read and strictly validate `cases.draft.json`. Raise `ValueError` that names the bad case."""
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000:
        raise ValueError("draft must be a bounded regular file, not a symlink")
    document = loads_untrusted(path.read_text(encoding="utf-8"))
    if not isinstance(document, list) or not document:
        raise ValueError("draft must be a nonempty list of cases")
    cases = [_case(item, position) for position, item in enumerate(cast(list[object], document), 1)]
    seen: set[str] = set()
    for case in cases:
        if case.identifier in seen:
            raise ValueError(f"duplicate case id {case.identifier}")
        seen.add(case.identifier)
    return cases


def split_cases(cases: Sequence[DraftCase], seed: int, kind: str = "task") -> list[IntakeCase]:
    """Assign splits from `seed`. A family never crosses splits. Scored cases take the grading `kind`.

    Train and validation get one task family each. Holdout takes the next families until it holds
    `HOLDOUT_MINIMUM` cases. The rest alternate between train and validation.
    When the first two shuffled families leave too few cases for holdout, the two smallest families go to
    train and validation instead. Pending-only families get a seeded split.
    """
    rng = random.Random(seed)
    size: dict[str, int] = {}
    for case in cases:
        if not case.pending:
            size[case.family] = size.get(case.family, 0) + 1
    if len(size) < MINIMUM_FAMILIES:
        raise ValueError(f"intake needs at least {MINIMUM_FAMILIES} task families (train, validation, holdout); "
                         + f"the draft has {len(size)}")
    order = sorted(size)
    rng.shuffle(order)
    if sum(size[family] for family in order[2:]) < HOLDOUT_MINIMUM:
        order.sort(key=lambda family: size[family])
    assigned: dict[str, Split] = {order[0]: "train", order[1]: "validation"}
    holdout = 0
    for index, family in enumerate(order[2:]):
        if index == 0 or holdout < HOLDOUT_MINIMUM:
            assigned[family] = "holdout"
            holdout += size[family]
        else:
            assigned[family] = "train" if index % 2 else "validation"
    for family in sorted({case.family for case in cases} - set(assigned)):
        assigned[family] = rng.choice(("train", "validation", "holdout"))
    return [IntakeCase(c.identifier, c.family, c.kind if c.pending else kind, c.request, c.source, c.files,
                       c.expected, assigned[c.family]) for c in cases]


def case_hash(cases: Sequence[IntakeCase], seed: int) -> str:
    """Return a stable sha256 over the seed and the canonical frozen content."""
    return digest({"seed": seed, "cases": [case.data() for case in sorted(cases, key=lambda c: c.identifier)]})


def approval_question(cases: Sequence[IntakeCase], seed: int, contract: Contract | None = None) -> str:
    """Return the one approval question. It lists every case and binds approval to the case hash."""
    lines = [f"Approve these {len(cases)} cases (seed {seed})? Trigger and near-miss cases stay pending, not scored."]
    if contract is not None:
        lines.append(f"contract: source {contract.source}; kinds {', '.join(sorted(contract.kinds))}")
    for case in sorted(cases, key=lambda c: c.identifier):
        status = "pending" if case.pending else "scored"
        request = " ".join(case.request.split())
        if len(request) > REQUEST_PREVIEW:
            request = request[:REQUEST_PREVIEW - 3] + "..."
        lines.append(f"- {case.identifier} | family {case.family} | split {case.split} | kind {case.kind} "
                     + f"| source {case.source} | {status} | {request}")
    lines.append(f"case hash: {case_hash(cases, seed)}")
    return "\n".join(lines)


def freeze(out: Path, cases: Sequence[IntakeCase], seed: int, approved_hash: str) -> None:
    """Write the approved cases to `out` in the `load_cases` format. Pending cases go under `pending`."""
    expected = case_hash(cases, seed)
    if approved_hash != expected:
        raise CodedError("cases-unapproved", "the approved hash does not match the cases; ask for approval again")
    scored: list[dict[str, object]] = []
    for case in cases:
        if not case.pending:
            item = case.data()
            item.update(provenance=case.source, provider_approved=True)
            scored.append(item)
    pending = [case.data() for case in cases if case.pending]
    document = {"schema_version": 1, "seed": seed, "approved_hash": expected, "cases": scored, "pending": pending}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=out.parent, prefix=out.name, suffix=".tmp",
                                     delete=False) as handle:
        _ = handle.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
    os.replace(handle.name, out)


def skill_facts(target: Path) -> dict[str, object]:
    """Return the facts the drafting agent needs: name, description, Markdown files, and helper scripts."""
    skill = target / "SKILL.md"
    front: dict[str, str] = {}
    if skill.is_file():
        match = _FRONTMATTER.match(skill.read_text(encoding="utf-8"))
        for line in (match.group(1).splitlines() if match else []):
            key, _, value = line.partition(":")
            if value and not key.startswith(" "):
                front[key.strip()] = value.strip().strip("\"'")
    files = sorted(path.relative_to(target).as_posix() for path in target.rglob("*")
                   if path.is_file() and not path.is_symlink() and not path.is_relative_to(target / "evals")
                   and not any(part.startswith(".") or part == "__pycache__" for part in path.relative_to(target).parts))
    return {"name": front.get("name", target.name), "description": front.get("description", ""),
            "markdown": [name for name in files if name.endswith(".md")],
            "scripts": [name for name in files if not name.endswith(".md")]}


def intake_contract(target: Path) -> Contract:
    """Return the target's own contract, else one judge-graded `task` kind graded against `expected`.

    Only an absent contract path falls back. A dangling symlink or a directory there stops with `contract-unreadable`.
    """
    if os.path.lexists(target / LOCATION):
        return load_contract(target)
    name = cast(str, skill_facts(target)["name"])
    return Contract(name, f"${name}", {"task": Grader("judge", rubric=TASK_RUBRIC)}, source="intake")

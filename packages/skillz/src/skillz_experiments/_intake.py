"""Case intake. The outer agent drafts cases; this module validates, splits, freezes, and asks once.

No function here calls a model. Trigger and near-miss cases stay pending until an activation grader exists.
"""
from __future__ import annotations

import os
import random
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from skillz_experiments._cases import (
    JOURNEY_FIELDS, CodedError, Journey, Split, digest, journey_of, loads_untrusted, string, text_map,
)
from skillz_experiments._contract import LOCATION, Contract, Grader, load_contract, parse
from skillz_experiments._evals_format import EVAL_KINDS, SOURCES
from skillz_experiments._frontmatter import top_fields
from skillz_experiments._records import write

DRAFT_NAME = "cases.draft.json"
PENDING_KINDS = ("trigger", "near-miss")
HOLDOUT_MINIMUM = 6
REQUEST_PREVIEW = 80
DRAFT_LIMIT = 2_000_000
MINIMUM_FAMILIES = 3
TASK_RUBRIC = ("The answer is correct when it satisfies the request and agrees in substance with `reference`. "
               "Ignore differences in JSON shape, quoting, or whitespace. "
               "Score 100 for a correct answer and 0 for a wrong answer.")
DRAFT_FIELDS = {"id", "family", "kind", "request", "files", "expected", "source"} | JOURNEY_FIELDS
DRAFT_REQUIRED = {"id", "family", "kind", "request", "source"}


@dataclass(frozen=True)
class DraftCase:
    identifier: str
    family: str
    kind: str
    request: str
    source: str
    files: dict[str, str]
    expected: object
    journey: Journey | None = None

    @property
    def pending(self) -> bool:
        return self.kind in PENDING_KINDS


@dataclass(frozen=True)
class IntakeCase(DraftCase):
    split: Split = field(kw_only=True)
    scored: bool = field(default=True, kw_only=True)

    def data(self) -> dict[str, object]:
        item: dict[str, object] = {"id": self.identifier, "family": self.family, "split": self.split,
                                   "kind": self.kind, "request": self.request, "source": self.source}
        if self.files:
            item["files"] = self.files
        if self.expected is not None:
            item["expected"] = self.expected
        if self.journey is not None:
            item.update(self.journey.data())
        if not self.scored and not self.pending:
            item["scored"] = False
        return item


def _case(value: object, position: int, journeys: Collection[str]) -> DraftCase:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in cast(dict[object, object], value)):
        raise ValueError(f"draft case {position} must be a JSON object")
    item = cast(dict[str, object], value)
    name = item.get("id") if isinstance(item.get("id"), str) and item.get("id") else f"#{position}"
    if set(item) - DRAFT_FIELDS:
        raise ValueError(f"case {name}: unknown fields {', '.join(sorted(set(item) - DRAFT_FIELDS))}")
    if DRAFT_REQUIRED - set(item):
        raise CodedError("cases-invalid", f"case {name}: missing fields {', '.join(sorted(DRAFT_REQUIRED - set(item)))}; add each "
                         + "field (a trigger or near-miss seed case takes family = id; a task case needs an explicit family)")
    try:
        kind, source = item["kind"], item["source"]
        allowed = (*EVAL_KINDS, *journeys)
        if kind not in allowed:
            raise ValueError(f"kind must be one of {', '.join(allowed)}")
        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}")
        files = text_map(item.get("files", {}))
        expected = item.get("expected")
        if kind == "task" and (not files or expected is None):
            raise ValueError("a task case needs files and an expected reference")
        return DraftCase(string(item["id"], "id"), string(item["family"], "family"), cast(str, kind),
                          string(item["request"], "request"), cast(str, source), files, expected,
                          journey_of(item, kind in journeys))
    except CodedError as error:
        raise CodedError(error.code, f"case {name}: {error}") from None
    except ValueError as error:
        raise ValueError(f"case {name}: {error}") from None


def load_draft(path: Path, journeys: Collection[str] = ("journey",)) -> list[DraftCase]:
    """Read and strictly validate `cases.draft.json`. Raise `ValueError` that names the bad case.

    `journeys` names the contract kinds that use the journey grader. Their cases take the journey fields.
    """
    if not path.is_file() or path.is_symlink() or path.stat().st_size > DRAFT_LIMIT:
        raise ValueError("draft must be a bounded regular file, not a symlink")
    document = loads_untrusted(path.read_text(encoding="utf-8"))
    if not isinstance(document, list) or not document:
        raise ValueError("draft must be a nonempty list of cases")
    cases = [_case(item, position, journeys) for position, item in enumerate(cast(list[object], document), 1)]
    seen: set[str] = set()
    for case in cases:
        if case.identifier in seen:
            raise ValueError(f"duplicate case id {case.identifier}")
        seen.add(case.identifier)
    return cases


def check_helper_input(contract: Contract, cases: Sequence[DraftCase]) -> None:
    """Raise `cases-invalid` when a task case omits the contract helper input from its `files`."""
    if contract.helper is None:
        return
    needed = contract.helper.input
    missing = [case.identifier for case in cases if case.kind == "task" and needed not in case.files]
    if missing:
        raise CodedError("cases-invalid", f"task case {', '.join(missing)}: files lacks the contract helper input {needed}; "
                         + f"add {needed} to the files of each task case")


def projection(size: Mapping[str, int]) -> dict[str, object]:
    """Project the split minimums over the scored cases per family, without a seed.

    The two smallest families go to train and validation in the worst shuffle. The scored cases of the other
    families are the most that the holdout can hold, so `holdout_capacity` and `deficit` hold for every seed.
    """
    ordered = sorted(size.values())
    capacity = sum(ordered[2:])
    return {"families": dict(sorted(size.items())), "families_minimum": MINIMUM_FAMILIES,
            "holdout_minimum": HOLDOUT_MINIMUM, "holdout_capacity": capacity,
            "deficit": max(0, HOLDOUT_MINIMUM - capacity)}


def too_few(size: Mapping[str, int]) -> CodedError | None:
    """Return the `cases-too-few` error when the scored families cannot fill the three splits, else None."""
    data = projection(size)
    capacity, deficit = cast(int, data["holdout_capacity"]), cast(int, data["deficit"])
    if len(size) >= MINIMUM_FAMILIES and not deficit:
        return None
    smallest = sorted(size.items(), key=lambda item: (item[1], item[0]))[:2]
    names = [name for name, _ in smallest]
    listed = ", ".join(f"{name}:{count}" for name, count in smallest)
    others = " and ".join(names)
    if len(size) < MINIMUM_FAMILIES:
        head = f"intake needs at least {MINIMUM_FAMILIES} scored families (train, validation, holdout); the draft has {len(size)}"
        fix = (f"add scored cases in {MINIMUM_FAMILIES - len(size)} more families, so that the families outside the "
               + f"two smallest hold at least {HOLDOUT_MINIMUM} scored cases")
    else:
        head = f"the holdout can hold {capacity} scored cases; the minimum is {HOLDOUT_MINIMUM}"
        fix = f"add {deficit} scored cases to families other than {others}, or one new family of at least {deficit}"
    message = (f"{head}; the two smallest families ({listed}) go to train and validation; the rest hold {capacity}; "
               + f"the deficit is {deficit}; {fix}")
    return CodedError("cases-too-few", message, data | {"fix": fix})


def split_cases(cases: Sequence[DraftCase], seed: int, kind: str | None = "task", journeys: Collection[str] = (),
                also: Collection[str] = ()) -> list[IntakeCase]:
    """Assign splits from `seed`. A family never crosses splits.

    A non-journey case is scored when `kind` is set, and it takes that grading `kind`. A journey case is scored
    when its own kind is in `journeys`, and it keeps its kind. Any other case is not scored: it stays in the
    draft and keeps its kind. No scored case ever changes between a task kind and a journey kind.
    A journey case of a kind in `also` is frozen and evaluated, but it does not size the split.
    It takes a seeded split, as a journey family does in a task run.

    Train and validation get one scored family each. Holdout takes the next families until it holds
    `HOLDOUT_MINIMUM` scored cases. The rest alternate between train and validation.
    When the first two shuffled families leave too few scored cases for holdout, the two smallest families go
    to train and validation instead. Families with no scored case, such as pending-only families and
    journey families in a task run, get a seeded split.
    """
    rng = random.Random(seed)

    def scores(case: DraftCase) -> bool:
        if case.pending:
            return False
        return case.kind in journeys if case.journey is not None else kind is not None

    size: dict[str, int] = {}
    for case in cases:
        if scores(case):
            size[case.family] = size.get(case.family, 0) + 1
    if len(size) < MINIMUM_FAMILIES:
        raise cast(CodedError, too_few(size))
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
    return [IntakeCase(c.identifier, c.family, kind if scores(c) and c.journey is None and kind is not None else c.kind,
                       c.request, c.source, c.files, c.expected, c.journey, split=assigned[c.family],
                       scored=scores(c) or (not c.pending and c.journey is not None and c.kind in also)) for c in cases]


def case_hash(cases: Sequence[IntakeCase], seed: int, statistics: Mapping[str, object] | None = None) -> str:
    """Return a stable sha256 over the seed, the canonical frozen content, and any resolved statistics."""
    body: dict[str, object] = {"seed": seed, "cases": [case.data() for case in sorted(cases, key=lambda c: c.identifier)]}
    if statistics is not None:
        body["statistics"] = dict(statistics)
    return digest(body)


def approval_question(cases: Sequence[IntakeCase], seed: int, contract: Contract | None = None,
                      statistics: Mapping[str, object] | None = None) -> str:
    """Return the one approval question. It lists every case and binds approval to the case hash."""
    lines = [f"Approve these {len(cases)} cases (seed {seed})? Trigger and near-miss cases stay pending, not scored. "
             + "A case of a kind that this run does not score is marked not scored."]
    if statistics is not None:
        lines.append(f"statistics: {', '.join(f'{name} {value}' for name, value in statistics.items())}")
    if contract is not None:
        lines.append(f"contract: source {contract.source}; kinds {', '.join(sorted(contract.kinds))}")
    for case in sorted(cases, key=lambda c: c.identifier):
        status = "pending" if case.pending else "scored" if case.scored else "not scored"
        request = " ".join(case.request.split())
        if len(request) > REQUEST_PREVIEW:
            request = request[:REQUEST_PREVIEW - 3] + "..."
        lines.append(f"- {case.identifier} | family {case.family} | split {case.split} | kind {case.kind} "
                     + f"| source {case.source} | {status} | {request}")
    lines.append(f"case hash: {case_hash(cases, seed, statistics)}")
    return "\n".join(lines)


def freeze(out: Path, cases: Sequence[IntakeCase], seed: int, approved_hash: str,
           statistics: Mapping[str, object] | None = None) -> None:
    """Write the approved cases to `out` in the `load_cases` format. Pending cases go under `pending`."""
    expected = case_hash(cases, seed, statistics)
    if approved_hash != expected:
        raise CodedError("cases-unapproved", "the approved hash does not match the cases; ask for approval again")
    scored: list[dict[str, object]] = []
    for case in cases:
        if case.scored:
            item = case.data()
            item.update(provenance=case.source, provider_approved=True)
            scored.append(item)
    pending = [case.data() for case in cases if case.pending]
    document: dict[str, object] = {"schema_version": 1, "seed": seed, "approved_hash": expected, "cases": scored,
                                   "pending": pending}
    unscored = [case.data() for case in cases if not case.scored and not case.pending]
    if unscored:
        document["not_scored"] = unscored
    write(out, document)


def skill_frontmatter(target: Path) -> dict[str, str]:
    """Return the top-level frontmatter fields of `<target>/SKILL.md`. Read only that file."""
    skill = target / "SKILL.md"
    fields = top_fields(skill.read_text(encoding="utf-8")) if skill.is_file() else None
    return {key: value for key, value in (fields or {}).items() if value}


def skill_facts(target: Path) -> dict[str, object]:
    """Return the facts the drafting agent needs: name, description, Markdown files, and helper scripts."""
    front = skill_frontmatter(target)
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
    built = Contract(name, f"${name}", {"task": Grader("judge", rubric=TASK_RUBRIC)}, source="intake")
    return parse(built.data(), "intake")

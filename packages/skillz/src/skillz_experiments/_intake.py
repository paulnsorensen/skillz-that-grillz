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
    JOURNEY_FIELDS, CodedError, Journey, Split, case_criteria, digest, journey_of, loads_untrusted, string, text_map,
)
from skillz_experiments._contract import LOCATION, RUBRIC_GRADERS, Contract, Grader, load_contract, parse
from skillz_experiments._evals_format import EVAL_KINDS, SOURCES
from skillz_experiments._frontmatter import read_frontmatter
from skillz_experiments._records import write

DRAFT_NAME = "cases.draft.json"
PENDING_KINDS = ("trigger", "near-miss")
HOLDOUT_MINIMUM = 6
TRAIN_MINIMUM = 4
TRAIN_FAMILIES_MINIMUM = 2
REQUEST_PREVIEW = 80
DRAFT_LIMIT = 2_000_000
MINIMUM_FAMILIES = TRAIN_FAMILIES_MINIMUM + 2
"""Train needs two families, and validation and holdout need one family each."""
SPLITS: tuple[Split, ...] = ("train", "validation", "holdout")
TASK_RUBRIC = ("Grade the answer on each criterion. `reference` holds the expected output and the assertions that the "
               "criteria come from. Ignore differences in JSON shape, quoting, or whitespace.")
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


def _judged(contract: Contract | None, case: IntakeCase) -> bool:
    return contract is not None and case.scored and contract.grader(case.kind).type in RUBRIC_GRADERS


def check_criteria(contract: Contract, cases: Sequence[IntakeCase]) -> None:
    """Raise `case-criteria-missing` or `case-criteria-invalid` for a scored case of a judge, hybrid, or journey kind."""
    for case in cases:
        if _judged(contract, case):
            _ = case_criteria(case.expected, case.identifier)


def _train_set(families: Sequence[str], size: Mapping[str, int]) -> tuple[str, ...] | None:
    """Return the set of `families` with the fewest scored cases that meets the train minimums, else None.

    The search keeps the first set that it finds for each case total and family count, so a tie follows the
    order of `families`.
    """
    found: dict[tuple[int, int], tuple[str, ...]] = {(0, 0): ()}
    for family in families:
        for (total, count), chosen in list(found.items()):
            if count >= TRAIN_FAMILIES_MINIMUM and total >= TRAIN_MINIMUM:
                continue
            _ = found.setdefault((total + size[family], min(count + 1, TRAIN_FAMILIES_MINIMUM)), (*chosen, family))
    valid = [chosen for (total, count), chosen in found.items()
             if count >= TRAIN_FAMILIES_MINIMUM and total >= TRAIN_MINIMUM]
    return min(valid, key=lambda chosen: sum(size[family] for family in chosen), default=None)


def _placement(order: Sequence[str], size: Mapping[str, int]) -> tuple[str | None, tuple[str, ...] | None]:
    """Return the validation family and the train families that leave the largest holdout.

    Validation takes the first smallest family in `order`. Train takes the smallest set of the other families
    that meets the train minimums, or None when no such set exists.
    """
    if not order:
        return None, None
    validation = min(order, key=lambda family: size[family])
    return validation, _train_set([family for family in order if family != validation], size)


def _fallback(order: Sequence[str], size: Mapping[str, int]) -> dict[str, Split]:
    """Return the fallback assignment when a split exists that meets every split minimum.

    Validation takes the smallest family that holds at least 2 scored cases and still leaves a qualifying train set
    and the holdout minimum. A validation split of 1 case gives 1 paired delta, and a promotion needs 2. When no
    such family exists, validation takes the first smallest family. Train takes the smallest qualifying set of the
    other families, and the holdout takes the rest.
    """
    smallest, train = _placement(order, size)
    validation, total = cast(str, smallest), sum(size[family] for family in order)
    for family in sorted(order, key=lambda family: size[family]):
        rest = _train_set([other for other in order if other != family], size) if size[family] >= 2 else None
        if rest is not None and total - size[family] - sum(size[other] for other in rest) >= HOLDOUT_MINIMUM:
            validation, train = family, rest
            break
    chosen = set(cast(tuple[str, ...], train))
    return {family: "validation" if family == validation else "train" if family in chosen else "holdout"
            for family in order}


def _layout(size: Mapping[str, int]) -> tuple[str | None, tuple[str, ...] | None, tuple[str, ...]]:
    """Return the validation family, the qualifying train set or None, and the families that train takes.

    The families go smallest first. When no train set qualifies, train takes all the families except validation.
    """
    order = sorted(size, key=lambda family: (size[family], family))
    validation, train = _placement(order, size)
    return validation, train, train if train is not None else tuple(family for family in order if family != validation)


def projection(size: Mapping[str, int]) -> dict[str, object]:
    """Project the split minimums over the scored cases per family, without a seed.

    The smallest family goes to validation, and the smallest set of the other families that holds
    `TRAIN_MINIMUM` scored cases from `TRAIN_FAMILIES_MINIMUM` families goes to train. The scored cases of the
    rest are the most that the holdout can hold in a split that meets the train minimums, so `holdout_capacity`
    and `deficit` hold for every seed. When no train set exists, train takes all the other families and the
    holdout holds 0.
    """
    validation, _, chosen = _layout(size)
    train_cases = sum(size[family] for family in chosen)
    capacity = sum(size[family] for family in size if family != validation and family not in chosen)
    deficit = max(0, HOLDOUT_MINIMUM - capacity)
    return {"families": dict(sorted(size.items())), "families_minimum": MINIMUM_FAMILIES,
            "holdout_minimum": HOLDOUT_MINIMUM, "train_minimum": TRAIN_MINIMUM,
            "train_families_minimum": TRAIN_FAMILIES_MINIMUM, "holdout_capacity": capacity, "deficit": deficit,
            "shortfall": {"train": {"cases": max(0, TRAIN_MINIMUM - train_cases),
                                     "families": max(0, TRAIN_FAMILIES_MINIMUM - len(chosen))},
                          "validation": {"families": 0 if validation is not None else 1},
                          "holdout": {"cases": deficit}}}


def too_few(size: Mapping[str, int]) -> CodedError | None:
    """Return the `cases-too-few` error when no whole-family split meets every split minimum, else None."""
    data = projection(size)
    capacity, deficit = cast(int, data["holdout_capacity"]), cast(int, data["deficit"])
    validation, train, chosen = _layout(size)
    if train is not None and not deficit:
        return None
    needs = (f"each run needs train {TRAIN_MINIMUM} scored cases from {TRAIN_FAMILIES_MINIMUM} families, "
             + f"validation 1 family, and holdout {HOLDOUT_MINIMUM} scored cases")
    if len(size) < MINIMUM_FAMILIES:
        head = (f"intake needs at least {MINIMUM_FAMILIES} scored families ({TRAIN_FAMILIES_MINIMUM} train, "
                + f"1 validation, 1 holdout); the draft has {len(size)}")
    elif train is None:
        head = (f"the families outside the smallest cannot give train {TRAIN_MINIMUM} scored cases from "
                + f"{TRAIN_FAMILIES_MINIMUM} families")
    else:
        head = f"the holdout can hold {capacity} scored cases; the minimum is {HOLDOUT_MINIMUM}"
    goal = (f"train holds {TRAIN_MINIMUM} scored cases from {TRAIN_FAMILIES_MINIMUM} families, validation holds "
            + f"1 family, and the holdout holds {HOLDOUT_MINIMUM} scored cases")
    if len(size) < MINIMUM_FAMILIES:
        missing = MINIMUM_FAMILIES - len(size)
        fix = f"add scored cases in {missing} more {'family' if missing == 1 else 'families'}, so that {goal}"
    elif train is None:
        fix = f"add scored cases in new or existing families until {goal}"
    elif capacity:
        others = " and ".join([cast(str, validation), *chosen])
        fix = f"add {deficit} scored cases to families other than {others}, or one new family of at least {deficit}"
    else:
        fix = f"add one new family of at least {deficit} scored cases"
    placed = ", ".join(f"{family}:{size[family]}" for family in chosen) or "no family"
    layout = (f"the smallest family ({validation}:{size[validation]}) goes to validation; train takes {placed}; "
              if validation is not None else "")
    message = f"{head}; {needs}; {layout}the rest hold {capacity}; the deficit is {deficit}; {fix}"
    return CodedError("cases-too-few", message, data | {"fix": fix})


def _seeded(order: Sequence[str], size: Mapping[str, int]) -> tuple[dict[str, Split], bool]:
    """Assign families in `order`. Return the assignment and whether it meets every split minimum.

    Train and validation get the first two families. Holdout takes the next families until it holds
    `HOLDOUT_MINIMUM` scored cases. The rest alternate between validation and train. When the first two families
    leave too few scored cases for holdout, the families go in order of size instead.
    """
    if sum(size[family] for family in order[2:]) < HOLDOUT_MINIMUM:
        order = sorted(order, key=lambda family: size[family])
    assigned: dict[str, Split] = dict(zip(order, ("train", "validation")))
    holdout = 0
    for index, family in enumerate(order[2:]):
        if index == 0 or holdout < HOLDOUT_MINIMUM:
            assigned[family] = "holdout"
            holdout += size[family]
        else:
            assigned[family] = "train" if index % 2 else "validation"
    train = [family for family, given in assigned.items() if given == "train"]
    met = (holdout >= HOLDOUT_MINIMUM and sum(size[family] for family in train) >= TRAIN_MINIMUM
           and len(train) >= TRAIN_FAMILIES_MINIMUM and "validation" in assigned.values())
    return assigned, met


def split_cases(cases: Sequence[DraftCase], seed: int, kind: str | None = "task", journeys: Collection[str] = (),
                also: Collection[str] = ()) -> list[IntakeCase]:
    """Assign splits from `seed`. A family never crosses splits.

    A non-journey case is scored when `kind` is set, and it takes that grading `kind`. A journey case is scored
    when its own kind is in `journeys`, and it keeps its kind. Any other case is not scored: it stays in the
    draft and keeps its kind. No scored case ever changes between a task kind and a journey kind.
    A journey case of a kind in `also` is frozen and evaluated, but it does not size the split.
    It takes a seeded split, as a journey family does in a task run.

    The split gives train at least `TRAIN_MINIMUM` scored cases from `TRAIN_FAMILIES_MINIMUM` families, gives
    validation at least 1 family, and gives the holdout at least `HOLDOUT_MINIMUM` scored cases, when any
    whole-family split meets these minimums. The seeded assignment comes first. When it misses a minimum, a
    deterministic fallback assignment replaces it. A draft with fewer than `MINIMUM_FAMILIES` scored families
    stops with `cases-too-few`. When no split meets every minimum, the seeded assignment stays, and the caller's
    `too_few` check stops the intake. Families with no scored case, such as pending-only families and journey
    families in a task run, get a seeded split.
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
    stop = too_few(size)
    if stop is not None and len(size) < MINIMUM_FAMILIES:
        raise stop
    order = sorted(size)
    rng.shuffle(order)
    assigned: dict[str, Split]
    assigned, met = _seeded(order, size)
    if not met and stop is None:
        assigned = _fallback(order, size)
    for family in sorted({case.family for case in cases} - set(assigned)):
        assigned[family] = rng.choice(SPLITS)
    return [IntakeCase(c.identifier, c.family, kind if scores(c) and c.journey is None and kind is not None else c.kind,
                       c.request, c.source, c.files, c.expected, c.journey, split=assigned[c.family],
                       scored=scores(c) or (not c.pending and c.journey is not None and c.kind in also)) for c in cases]


def case_hash(cases: Sequence[IntakeCase], seed: int, statistics: Mapping[str, object] | None = None) -> str:
    """Return a stable sha256 over the seed, the canonical frozen content, and any resolved statistics."""
    body: dict[str, object] = {"seed": seed, "cases": [case.data() for case in sorted(cases, key=lambda c: c.identifier)]}
    if statistics is not None:
        body["statistics"] = dict(statistics)
    return digest(body)


def _totals(cases: Sequence[IntakeCase], split: Split) -> str:
    """Return the scored case total and family total of one split, for the approval question.

    In a task run, the total names the journey cases in it, because the split minimums count only the task cases.
    A journey run scores only journey cases, so its totals have no journey part.
    """
    scored = [case for case in cases if case.split == split and case.scored]
    count, families = len(scored), len({case.family for case in scored})
    journeys = sum(case.journey is not None for case in scored)
    task_run = any(case.scored and case.journey is None for case in cases)
    part = f" ({journeys} journey)" if journeys and task_run else ""
    return (f"{split} {count} {'case' if count == 1 else 'cases'}{part} from {families} "
            + f"{'family' if families == 1 else 'families'}")


def approval_question(cases: Sequence[IntakeCase], seed: int, contract: Contract | None = None,
                      statistics: Mapping[str, object] | None = None) -> str:
    """Return the one approval question. It lists every case and binds approval to the case hash."""
    lines = [f"Approve these {len(cases)} cases (seed {seed})? Trigger and near-miss cases stay pending, not scored. "
             + "A case of a kind that this run does not score is marked not scored."]
    if statistics is not None:
        lines.append(f"statistics: {', '.join(f'{name} {value}' for name, value in statistics.items())}")
    if contract is not None:
        lines.append(f"contract: source {contract.source}; kinds {', '.join(sorted(contract.kinds))}")
    lines.append("scored totals: " + ", ".join(_totals(cases, name) for name in SPLITS))
    for case in sorted(cases, key=lambda c: c.identifier):
        status = "pending" if case.pending else "scored" if case.scored else "not scored"
        request = " ".join(case.request.split())
        if len(request) > REQUEST_PREVIEW:
            request = request[:REQUEST_PREVIEW - 3] + "..."
        lines.append(f"- {case.identifier} | family {case.family} | split {case.split} | kind {case.kind} "
                     + f"| source {case.source} | {status} | {request}")
        if _judged(contract, case):
            lines.extend(f"  - criterion {criterion.id} | must {str(criterion.must).lower()} "
                         + f"| from {criterion.source or '-'} | {' '.join(criterion.text.split())}"
                         for criterion in case_criteria(case.expected, case.identifier))
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
    """Return the nonempty top-level frontmatter fields of `<target>/SKILL.md`. Read only that file.

    Return no fields when the file is absent or its frontmatter is unreadable.
    """
    skill = target / "SKILL.md"
    try:
        fields = read_frontmatter(skill.read_text(encoding="utf-8")) if skill.is_file() else {}
    except ValueError:
        return {}
    return {key: value for key, value in fields.items() if value}


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

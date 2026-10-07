from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments import _harness
from skillz_experiments._cases import CodedError, load_cases
from skillz_experiments._intake import (
    DRAFT_NAME, approval_question, case_hash, freeze, intake_contract, load_draft, skill_facts, split_cases,
)


def draft(families: int = 5, per_family: int = 2) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for family in range(families):
        for index in range(per_family):
            cases.append({"id": f"t{family}-{index}", "family": f"f{family}", "kind": "task",
                          "request": f"Do thing {family}.{index}", "files": {"input.txt": "hello\n"},
                          "expected": {"answer": f"{family}"}, "source": "skill"})
    cases.append({"id": "trig-1", "family": "f0", "kind": "trigger", "request": "Please run the skill",
                  "source": "session"})
    cases.append({"id": "near-1", "family": "f1", "kind": "near-miss", "request": "Something similar",
                  "source": "session"})
    return cases


def write_draft(tmp_path: Path, cases: list[dict[str, object]]) -> Path:
    path = tmp_path / DRAFT_NAME
    _ = path.write_text(json.dumps(cases), encoding="utf-8")
    return path


def test_draft_assigns_seeded_family_disjoint_splits_and_one_question_without_model_calls(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("intake must not construct a provider")

    monkeypatch.setattr(_harness.Role, "create", forbidden)
    cases = load_draft(write_draft(tmp_path, draft()))
    first = split_cases(cases, 7)
    assert [case.split for case in first] == [case.split for case in split_cases(cases, 7)]
    assert any(case.split != other.split for case, other in zip(first, split_cases(cases, 8)))
    seen: dict[str, str] = {}
    for case in first:
        assert seen.setdefault(case.family, case.split) == case.split
    assert {case.split for case in first} == {"train", "validation", "holdout"}
    assert sum(case.split == "holdout" for case in first) >= 6
    question = approval_question(first, 7)
    assert isinstance(question, str) and case_hash(first, 7) in question
    for case in first:
        assert case.identifier in question and case.split in question and case.kind in question
    assert {"trigger", "near-miss"} <= {case.kind for case in first}

    out = tmp_path / "cases.json"
    freeze(out, first, 7, case_hash(first, 7))
    loaded = load_cases(out, {"task": "judge"})
    assert sorted(case.identifier for case in loaded) == sorted(c.identifier for c in first if c.kind == "task")
    assert all(case.eligible for case in loaded)
    document = cast(dict[str, object], json.loads(out.read_text(encoding="utf-8")))
    assert document["seed"] == 7 and document["approved_hash"] == case_hash(first, 7)
    pending = cast(list[dict[str, object]], document["pending"])
    assert sorted(str(item["id"]) for item in pending) == ["near-1", "trig-1"]


def test_hash_mismatch_raises_cases_unapproved_and_writes_nothing(tmp_path: Path) -> None:
    cases = split_cases(load_draft(write_draft(tmp_path, draft())), 1)
    out = tmp_path / "cases.json"
    with pytest.raises(CodedError) as caught:
        freeze(out, cases, 1, case_hash(cases, 2))
    assert caught.value.code == "cases-unapproved"
    assert not out.exists()


def test_too_few_families_names_the_minimum(tmp_path: Path) -> None:
    cases = load_draft(write_draft(tmp_path, draft(families=2)))
    with pytest.raises(ValueError, match="at least 3 task families"):
        _ = split_cases(cases, 1)


MISSING = object()


@pytest.mark.parametrize(("index", "key", "value", "message"), [
    (0, "extra", 1, "t0-0.*unknown"),
    (0, "request", MISSING, "t0-0.*missing"),
    (1, "id", "t0-0", "duplicate.*t0-0"),
    (0, "kind", "other", "t0-0.*kind"),
    (0, "source", "web", "t0-0.*source"),
    (0, "expected", MISSING, "t0-0.*expected"),
])
def test_draft_validation_rejects_bad_cases(tmp_path: Path, index: int, key: str, value: object, message: str) -> None:
    cases = draft()
    if value is MISSING:
        del cases[index][key]
    else:
        cases[index][key] = value
    with pytest.raises(ValueError, match=message):
        _ = load_draft(write_draft(tmp_path, cases))


def test_draft_must_be_a_list(tmp_path: Path) -> None:
    path = tmp_path / DRAFT_NAME
    _ = path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="list"):
        _ = load_draft(path)


def make_skill(tmp_path: Path, contract: bool = False) -> Path:
    target = tmp_path / "demo-skill"
    (target / "scripts").mkdir(parents=True)
    _ = (target / "SKILL.md").write_text("---\nname: demo-skill\ndescription: Does a demo.\n---\n# Demo\n", encoding="utf-8")
    _ = (target / "notes.md").write_text("notes\n", encoding="utf-8")
    _ = (target / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    if contract:
        (target / "evals").mkdir()
        _ = (target / "evals" / "autoimprove.json").write_text(json.dumps({
            "schema_version": 1, "status": "approved", "skill": "demo-skill", "invocation": "$demo-skill",
            "kinds": {"exact": {"grader": "exact-json"}}, "editable": []}), encoding="utf-8")
    return target


def test_skill_facts_lists_frontmatter_markdown_and_helpers(tmp_path: Path) -> None:
    facts = skill_facts(make_skill(tmp_path))
    assert facts == {"name": "demo-skill", "description": "Does a demo.",
                     "markdown": ["SKILL.md", "notes.md"], "scripts": ["scripts/run.py"]}


def test_intake_contract_prefers_the_skill_contract_else_builds_a_judge_task(tmp_path: Path) -> None:
    own = intake_contract(make_skill(tmp_path / "a", contract=True))
    assert own.source == "skill" and set(own.kinds) == {"exact"}
    built = intake_contract(make_skill(tmp_path / "b"))
    assert built.source == "intake" and built.skill == "demo-skill"
    assert set(built.kinds) == {"task"} and built.kinds["task"].type == "judge" and built.kinds["task"].rubric


def sized(sizes: list[int]) -> list[dict[str, object]]:
    return [{"id": f"c{family}-{index}", "family": f"f{family}", "kind": "task", "request": "Do it",
             "files": {"input.txt": "x\n"}, "expected": "y", "source": "skill"}
            for family, size in enumerate(sizes) for index in range(size)]


@pytest.mark.parametrize("sizes", [[6, 6, 1, 1], [1, 1, 6, 6], [5, 1, 1, 4], [2, 2, 2, 2, 2]])
def test_holdout_reaches_the_minimum_when_the_draft_allows_it(tmp_path: Path, sizes: list[int]) -> None:
    cases = load_draft(write_draft(tmp_path, sized(sizes)))
    for seed in range(40):
        split = split_cases(cases, seed)
        counts = {name: sum(case.split == name for case in split) for name in ("train", "validation", "holdout")}
        assert counts["holdout"] >= 6, (seed, counts)
        assert counts["train"] >= 1 and counts["validation"] >= 1
        assert split == split_cases(cases, seed)


def test_approval_question_shows_family_request_and_contract(tmp_path: Path) -> None:
    raw = draft()
    raw[0]["request"] = "Summarize " + "long " * 40
    cases = split_cases(load_draft(write_draft(tmp_path, raw)), 3)
    question = approval_question(cases, 3, intake_contract(make_skill(tmp_path)))
    assert "Summarize long long" in question and "family f0" in question
    assert "long " * 40 not in question and "..." in question
    assert "contract: source intake; kinds task" in question
    assert "Please run the skill" in question


def test_grading_kind_is_covered_by_the_hash_and_written_per_case(tmp_path: Path) -> None:
    draft_cases = load_draft(write_draft(tmp_path, draft()))
    default, custom = split_cases(draft_cases, 1), split_cases(draft_cases, 1, kind="echo")
    assert case_hash(default, 1) != case_hash(custom, 1)
    out = tmp_path / "cases.json"
    freeze(out, custom, 1, case_hash(custom, 1))
    assert {case.kind for case in load_cases(out, {"echo": "judge"})} == {"echo"}
    assert [path.name for path in tmp_path.iterdir() if path.suffix == ".tmp"] == []

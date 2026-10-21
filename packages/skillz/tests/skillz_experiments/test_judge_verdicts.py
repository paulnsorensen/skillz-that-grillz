"""The judge grades each criterion of a case with a named level, and code computes the score (judge-criteria-verdicts)."""
# pyright: reportAny=false, reportUnknownMemberType=false
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from jsonschema import Draft202012Validator

from skillz_experiments import _intake, _scaffold
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._contract import Grader, parse
from skillz_experiments._graders import Verdict, judge
from skillz_experiments._records import read, write
from skillz_experiments._schemas import schema
from skillz_experiments.run import export

if TYPE_CHECKING:
    from .conftest import JudgedRun

ROOT = Path(__file__).resolve().parents[4]
CRITERIA = [{"id": "answer", "text": "THE answer SHALL echo the request."},
            {"id": "short", "text": "WHEN the request is one line THE answer SHALL be one line.", "from": "assertion:0"},
            {"id": "safe", "text": "THE answer SHALL not run a command.", "must": True}]
IDS = ["answer", "short", "safe"]
GRADERS = [Grader("judge", rubric="Grade the echo."), Grader("hybrid", ("true",), "Grade the echo."),
           Grader("journey", ("true",), "Grade the echo.")]


class Judge:
    """A judge transport that records each prompt and schema and answers `reply`."""

    def __init__(self, reply: object) -> None:
        self.reply: object = reply
        self.prompts: list[str] = []
        self.schemas: list[dict[str, object]] = []

    def invoke(self, prompt: str, *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del holdout
        assert schema is not None
        self.prompts.append(prompt)
        self.schemas.append(schema)
        return {"answer": self.reply}


def levels(**given: int) -> dict[str, object]:
    return {"criteria": {name: {"level": level, "reason": f"{name} at {level}"} for name, level in given.items()}}


def case(criteria: list[dict[str, object]] | None = None) -> Case:
    return Case("c1", "f1", "train", "Echo this request.", {"input.txt": "hello"},
                {"assertions": ["one line"], "criteria": CRITERIA if criteria is None else criteria}, "synthetic", True)


def grade(reply: object, grader: Grader = GRADERS[0], criteria: list[dict[str, object]] | None = None) -> tuple[float, dict[str, Verdict], Judge]:
    transport = Judge(reply)
    score, verdicts, _ = judge(transport, grader, case(criteria), {"text": "hello"}, None, holdout=False)
    return score, verdicts, transport


@pytest.mark.ac("judge-criteria-verdicts:AC-1")
@pytest.mark.parametrize("grader", GRADERS, ids=[grader.type for grader in GRADERS])
def test_ac1_the_judge_is_asked_for_one_level_per_criterion_id(grader: Grader) -> None:
    score, verdicts, transport = grade(levels(answer=4, short=3, safe=4), grader)
    sent = transport.schemas[0]
    entries = cast(dict[str, dict[str, object]], cast(dict[str, object], sent["properties"])["criteria"])
    assert sent["required"] == ["criteria"] and sent["additionalProperties"] is False
    assert entries["required"] == IDS and entries["additionalProperties"] is False
    level = cast(dict[str, dict[str, dict[str, object]]], entries["properties"])["answer"]["properties"]["level"]
    assert level == {"type": "integer", "enum": [1, 2, 3, 4]}
    assert verdicts == {"answer": Verdict(4, "answer at 4"), "short": Verdict(3, "short at 3"), "safe": Verdict(4, "safe at 4")}
    assert score == pytest.approx((1 + 2 / 3 + 1) / 3)


@pytest.mark.ac("judge-criteria-verdicts:AC-1")
@pytest.mark.parametrize("grader", GRADERS, ids=[grader.type for grader in GRADERS])
@pytest.mark.parametrize("reply", [
    levels(answer=4, short=3),
    levels(answer=4, short=3, safe=4, extra=4),
    levels(answer=4, short=3, safe=0),
    levels(answer=4, short=3, safe=5),
    {"criteria": {"answer": {"level": "4", "reason": "r"}, "short": {"level": 3, "reason": "r"}, "safe": {"level": 4, "reason": "r"}}},
    {"criteria": {"answer": {"level": True, "reason": "r"}, "short": {"level": 3, "reason": "r"}, "safe": {"level": 4, "reason": "r"}}},
    {"criteria": {"answer": {"level": 4}, "short": {"level": 3, "reason": "r"}, "safe": {"level": 4, "reason": "r"}}},
    {"score_percent": 80},
    levels(answer=4, short=3, safe=4) | {"score_percent": 80},
    {"criteria": {"answer": {"level": 4, "reason": "r", "extra": 1}, "short": {"level": 3, "reason": "r"}, "safe": {"level": 4, "reason": "r"}}},
    {"criteria": [{"level": 4, "reason": "r"}]},
    {"criteria": {"answer": {"level": 4, "reason": 5}, "short": {"level": 3, "reason": "r"}, "safe": {"level": 4, "reason": "r"}}},
], ids=["omitted-id", "added-id", "level-0", "level-5", "level-text", "level-bool", "no-reason", "old-shape", "extra-key",
         "extra-key-in-entry", "criteria-not-a-dict", "reason-not-text"])
def test_ac1_a_reply_that_omits_adds_or_misranges_a_level_is_an_invalid_judge_response(grader: Grader, reply: object) -> None:
    with pytest.raises(ValueError, match="^invalid judge response$"):
        _ = grade(reply, grader)


@pytest.mark.ac("judge-criteria-verdicts:AC-2")
@pytest.mark.parametrize("level", [1, 2])
def test_ac2_a_must_criterion_at_level_1_or_2_scores_the_outcome_0(level: int) -> None:
    score, verdicts, _ = grade(levels(answer=4, short=4, safe=level))
    assert score == 0.0 and verdicts["safe"].level == level


@pytest.mark.ac("judge-criteria-verdicts:AC-2")
def test_ac2_a_graded_criterion_at_level_1_does_not_zero_the_outcome() -> None:
    score, _, _ = grade(levels(answer=1, short=1, safe=4))
    assert score == pytest.approx(1 / 3)


@pytest.mark.ac("judge-criteria-verdicts:AC-3")
@pytest.mark.parametrize(("given", "expected"), [
    ({"answer": 4, "short": 4, "safe": 4}, 1.0),
    ({"answer": 1, "short": 2, "safe": 3}, (0 + 1 / 3 + 2 / 3) / 3),
    ({"answer": 2, "short": 4, "safe": 3}, (1 / 3 + 1 + 2 / 3) / 3),
])
def test_ac3_with_every_must_at_3_or_4_the_score_is_the_mean_over_all_criteria(given: dict[str, int], expected: float) -> None:
    score, _, _ = grade(levels(**given))
    assert score == pytest.approx(expected)


@pytest.mark.ac("judge-criteria-verdicts:AC-3")
def test_ac3_a_case_without_a_must_criterion_scores_the_plain_mean() -> None:
    only: list[dict[str, object]] = [{"id": "answer", "text": "THE answer SHALL echo the request."}]
    assert grade(levels(answer=3), criteria=only)[0] == pytest.approx(2 / 3)
    assert grade(levels(answer=1), criteria=only)[0] == 0.0


@pytest.mark.ac("judge-criteria-verdicts:AC-5")
@pytest.mark.parametrize("grader", GRADERS, ids=[grader.type for grader in GRADERS])
def test_ac5_the_judge_prompt_names_the_four_levels_and_lists_each_criterion(grader: Grader) -> None:
    _, _, transport = grade(levels(answer=4, short=3, safe=4), grader)
    prompt = transport.prompts[0].split("UNTRUSTED DATA (JSON):", 1)[0]
    assert "1 not met, 2 partly met, 3 mostly met, 4 fully met" in prompt
    for item in CRITERIA:
        assert f"- {item['id']}: {item['text']}" in prompt
    assert "score_percent" not in prompt and "0 to 100" not in prompt


CONTRACT: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": "demo", "invocation": "/demo",
                               "kinds": {"task": {"grader": "judge", "rubric": "Grade it.", "pillars": ["accuracy", "style"]}}}


@pytest.mark.ac("judge-criteria-verdicts:AC-14")
def test_ac14_a_kind_with_pillars_stops_at_contract_load_and_says_to_move_them_into_criteria() -> None:
    with pytest.raises(CodedError) as stopped:
        _ = parse(json.loads(json.dumps(CONTRACT)), "skill")
    assert stopped.value.code == "contract-pillars-removed"
    assert "kind task" in str(stopped.value) and "expected.criteria" in str(stopped.value)


@pytest.mark.ac("judge-criteria-verdicts:AC-14")
def test_ac14_the_committed_contract_schema_rejects_pillars() -> None:
    committed = json.loads((ROOT / "skills/skillz/references/schemas/autoimprove.schema.json").read_text(encoding="utf-8"))
    assert committed == schema("contract")
    validator = Draft202012Validator(committed)
    without = json.loads(json.dumps(CONTRACT))
    del without["kinds"]["task"]["pillars"]
    assert validator.is_valid(without)
    assert not validator.is_valid(CONTRACT)  # pyright: ignore[reportArgumentType]


# Score arithmetic in a rubric: a number, or a word that tells the judge to compute a score.
ARITHMETIC = re.compile(r"\d|\b(?:score|scores|subtract|add|points?|percent|mean|average|sum)\b|%", re.I)


def rubrics() -> dict[str, str]:
    found = {"intake TASK_RUBRIC": _intake.TASK_RUBRIC, "scaffold RUBRIC": _scaffold.RUBRIC}
    shipped = json.loads((ROOT / "skills/skillz/evals/autoimprove.json").read_text(encoding="utf-8"))
    for kind, entry in cast(dict[str, dict[str, object]], shipped["kinds"]).items():
        if "rubric" in entry:
            found[f"skills/skillz {kind}"] = cast(str, entry["rubric"])
    for path in sorted((ROOT / "packages/skillz/journey-kits").glob("*/journeys.py")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^RUBRIC = \((.*?)\)\n", text, re.S | re.M)
        assert match, path
        found[str(path.relative_to(ROOT))] = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', match[1]))
    return found


@pytest.mark.ac("judge-criteria-verdicts:AC-16")
def test_ac16_no_shipped_or_default_rubric_holds_score_arithmetic_or_a_questions_or_final_stop_rule() -> None:
    found = rubrics()
    assert {"intake TASK_RUBRIC", "scaffold RUBRIC", "skills/skillz task", "skills/skillz journey"} <= set(found)
    assert any(name.startswith("packages/skillz/journey-kits/") for name in found)
    for name, rubric in found.items():
        assert rubric.strip(), name
        assert not ARITHMETIC.search(rubric), (name, rubric)
        assert "questions" not in rubric and "final_stop" not in rubric, (name, rubric)
        assert not re.search(r"\b(?:hash|budget)\b", rubric, re.I), (name, rubric)  # the approval rule is the gate's


@pytest.mark.ac("judge-criteria-verdicts:AC-16")
def test_ac16_the_arithmetic_check_catches_the_old_rubrics() -> None:
    old = ["Score 100 for a correct answer and 0 for a wrong answer.",
           "Start at 100. Subtract 20 for each item in `reference.must` that the transcript does not show.",
           "Return the mean of the criterion scores."]
    assert all(ARITHMETIC.search(rubric) for rubric in old)


STALE = "0" * 64


@pytest.mark.ac("judge-criteria-verdicts:AC-17")
def test_ac17_a_run_frozen_by_an_older_evaluator_refuses_to_resume(judged: JudgedRun) -> None:
    with pytest.raises(CodedError) as stopped:
        _ = judged.start(live=False)
    assert stopped.value.code == "live-required"
    record = read(judged.out / "run.json")
    assert record["engine_hash"] != STALE
    write(judged.out / "run.json", record | {"engine_hash": STALE})
    with pytest.raises(ValueError, match="^evaluator differs from the frozen record; create a new run$"):
        _ = judged.start()
    assert not judged.log.exists()


@pytest.mark.ac("judge-criteria-verdicts:AC-17")
def test_ac17_a_run_frozen_by_an_older_evaluator_refuses_to_export(judged: JudgedRun, tmp_path: Path) -> None:
    assert judged.start()["phase"] == "complete"
    _ = export(judged.out, tmp_path / "fresh")
    write(judged.out / "run.json", read(judged.out / "run.json") | {"engine_hash": STALE})
    with pytest.raises(ValueError, match="^evaluator differs from the frozen record; create a new run$"):
        _ = export(judged.out, tmp_path / "stale")
    assert not (tmp_path / "stale").exists()

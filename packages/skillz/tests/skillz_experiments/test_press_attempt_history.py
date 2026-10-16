"""Press attack on attempt history (#241): leakage, length bounds, duplicates, render cap, resume, holdout, protect set."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._candidate import Candidate
from skillz_experiments._records import read
from skillz_experiments.harness._command import _validate_answer  # pyright: ignore[reportPrivateUsage]
from skillz_experiments.run import run
from skillz_experiments.search import Progress, attempts, protected
from skillz_experiments.search import accept as accept_proposal
from skillz_experiments.search import request as reflection_request
from skillz_experiments.search._history import LIMIT, SHOWN, render
from skillz_experiments._cases import Case
from test_run_search import ANSWER, FIXTURE, MODEL, SPAN, Provider, _attempts, _leak_start, _recorded_attempts, _reflections_of, _start  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.usefixtures("host_login")

SEED = ("digest-seed", "seed-id")


def _score(candidate: str, split: str, case: str, score: float, **extra: object) -> dict[str, object]:
    return {"arm": "search", "split": split, "case_id": case, "search_candidate": candidate, "score": score} | extra


def _reflection(**fields: object) -> dict[str, object]:
    return {"arm": "search", "split": "reflection", "parent": "digest-seed", "components_digest": "digest-1",
            "minibatch": ["a"], "hypothesis": "h", "hypothesis_dropped": None, "rejected": None, "proposed": "child-1"} | fields


# Attack 2: hypothesis length boundary and non-string types.

@pytest.mark.parametrize(("length", "kept"), [(0, False), (1, True), (199, True), (200, True), (201, False), (5000, False)])
def test_accept_hypothesis_length_boundary_keeps_at_200_and_drops_at_201(length: int, kept: bool) -> None:
    answer = {"SKILL.md": "t", "@hypothesis": "h" * length}
    _, _, hypothesis, dropped = accept_proposal(answer, ["SKILL.md"], {"SKILL.md": "t"})
    assert (hypothesis == "h" * length) is (kept and length > 0)
    assert dropped == ("hypothesis-too-long" if length > 200 else None)


def test_accept_padded_hypothesis_counts_length_after_strip() -> None:
    answer = {"SKILL.md": "t", "@hypothesis": "  " + "h" * 200 + "\n"}
    assert accept_proposal(answer, ["SKILL.md"], {"SKILL.md": "t"})[2:] == ("h" * 200, None)


@pytest.mark.parametrize("value", [7, 1.5, True, ["a"], {"a": "b"}, "   "])
def test_accept_non_string_hypothesis_is_stored_as_null_with_its_rule_and_does_not_refuse_the_proposal(value: object) -> None:
    answer = {"SKILL.md": "t2", "@hypothesis": value}
    texts, _, hypothesis, dropped = accept_proposal(answer, ["SKILL.md"], {"SKILL.md": "t"})
    assert texts == {"SKILL.md": "t2"} and hypothesis is None
    assert dropped == (None if isinstance(value, str) else "hypothesis-not-text")


def test_accept_hypothesis_key_does_not_hide_a_missing_component() -> None:
    with pytest.raises(ValueError):
        _ = accept_proposal({"@hypothesis": "h"}, ["SKILL.md"], {"SKILL.md": "t"})
    with pytest.raises(ValueError):
        _ = accept_proposal({"SKILL.md": "t", "extra": "x", "@hypothesis": None}, ["SKILL.md"], {"SKILL.md": "t"})


# Attack 9: the command adapter answer check.

SCHEMA: dict[str, object] = {"type": "object", "required": ["SKILL.md", "@hypothesis"], "additionalProperties": False,
                             "properties": {"SKILL.md": {"type": "string"}, "@hypothesis": {"type": ["string", "null"]}}}


@pytest.mark.parametrize("value", [None, "why", ""])
def test_validate_answer_null_or_string_hypothesis_is_accepted(value: object) -> None:
    _validate_answer({"SKILL.md": "t", "@hypothesis": value}, SCHEMA)


@pytest.mark.parametrize("value", [7, 0, True, 1.5, ["x"], {"k": "v"}])
def test_validate_answer_non_text_hypothesis_is_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        _validate_answer({"SKILL.md": "t", "@hypothesis": value}, SCHEMA)


def test_validate_answer_missing_nullable_key_is_accepted() -> None:
    _validate_answer({"SKILL.md": "t"}, SCHEMA)


def test_validate_answer_missing_non_nullable_key_or_extra_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        _validate_answer({"@hypothesis": None}, SCHEMA)
    with pytest.raises(ValueError):
        _validate_answer({"SKILL.md": "t", "@hypothesis": None, "x": "y"}, SCHEMA)
    with pytest.raises(ValueError):
        _validate_answer({"SKILL.md": None, "@hypothesis": None}, SCHEMA)


# Attack 4: duplicate outcomes.

def test_attempts_newest_outcome_failed_uses_older_scored_outcome_for_train_and_validation() -> None:
    outcomes = [
        _score("seed-id", "train", "a", 0.5), _score("seed-id", "train", "a", 0.0, status="evaluation-failed"),
        _score("seed-id", "validation", "v1", 0.2), _score("seed-id", "validation", "v2", 0.4),
        _reflection(),
        _score("child-1", "train", "a", 0.9), _score("child-1", "train", "a", 0.1, status="evaluation-failed"),
        _score("child-1", "validation", "v1", 0.6), _score("child-1", "validation", "v1", 0.0, status="evaluation-failed"),
        _score("child-1", "validation", "v2", 0.8), _score("child-1", "validation", "v2", 0.9),
    ]
    (attempt,) = attempts(outcomes, SEED, ["v1", "v2"])
    assert attempt["fixed"] == ["a"] and attempt["broke"] == []
    # parent mean (0.2 + 0.4) / 2, child mean (0.6 from the older scored v1 + 0.9) / 2
    assert attempt["validation_delta"] == pytest.approx(0.75 - 0.3)


def test_attempts_every_outcome_of_the_case_failed_is_unscored() -> None:
    outcomes = [_score("seed-id", "train", "a", 0.5), _reflection(),
                _score("child-1", "train", "a", 0.0, status="evaluation-failed")]
    assert attempts(outcomes, SEED, [])[0]["fixed"] == [] and attempts(outcomes, SEED, [])[0]["broke"] == []


def test_attempts_equal_scores_are_neither_fixed_nor_broke() -> None:
    outcomes = [_score("seed-id", "train", "a", 0.5), _reflection(), _score("child-1", "train", "a", 0.5)]
    assert attempts(outcomes, SEED, [])[0]["fixed"] == [] and attempts(outcomes, SEED, [])[0]["broke"] == []


def test_attempts_malformed_outcomes_are_ignored_without_crashing() -> None:
    outcomes: list[dict[str, object]] = [
        {}, {"arm": "search", "split": "reflection"},
        {"arm": "search", "split": "train", "case_id": "a", "search_candidate": "seed-id", "score": True},
        {"arm": "search", "split": "train", "case_id": "a", "search_candidate": "seed-id", "score": "1"},
        {"arm": "search", "split": "train", "case_id": 7, "search_candidate": "seed-id", "score": 1.0},
        _reflection(minibatch="a", hypothesis=7, rejected=["x"], proposed=None)]
    bare, attempt = attempts(outcomes, SEED, [])
    assert bare["candidate"] is None and bare["accepted"] is False
    assert attempt["fixed"] == [] and attempt["broke"] == [] and attempt["hypothesis"] is None
    assert attempt["rejected"] is None and attempt["candidate"] is None


# Attack 8: the protect set.

def test_protected_score_below_one_or_unknown_identity_is_excluded() -> None:
    outcomes = [_score("s", "train", "a", 1.0), _score("s", "train", "b", 0.9999), _score("s", "train", "c", 1.0),
                _score("s", "train", "c", 0.5), _score("s", "validation", "v", 1.0), _score("s", "holdout", "h", 1.0)]
    assert protected(outcomes, "s") == ["a"]
    assert protected(outcomes, "missing") == []
    assert protected([], "s") == []


# Attack 5: render cap and structure.

def _many(count: int, reason: str, ids: int = 25) -> list[dict[str, object]]:
    return [{"index": number, "hypothesis": f"idea {number} " + "w" * 190, "fixed": [f"case-{number}-{n}" for n in range(ids)],
             "broke": [f"break-{number}-{n}" for n in range(ids)], "validation_delta": -0.25, "rejected": reason}
            for number in range(1, count + 1)]


@pytest.mark.parametrize("count", [0, 1, 7, 8, 9, 40])
def test_render_many_long_attempts_stays_within_the_cap_shows_at_most_eight_newest_first(count: int) -> None:
    text = render(_many(count, "r" * 5000), [f"protected-case-{n}" for n in range(500)])
    assert len(text) <= LIMIT
    shown = [int(n) for n in cast(list[str], re.findall(r"Attempt (\d+):", text))]
    assert len(shown) <= SHOWN
    assert shown == sorted(shown, reverse=True)
    assert not shown or shown[0] == count
    for line in cast(list[str], re.findall(r"Attempt \d+: .*?(?= Attempt \d+:|Keep these|Do not re-introduce)", text)):
        assert line.rstrip().endswith(".") and "result rejected, " in line
    assert text.endswith("drops such a hypothesis.")


def test_render_huge_case_ids_still_stays_within_the_cap() -> None:
    history: list[dict[str, object]] = [{"index": 1, "hypothesis": "h", "fixed": ["f" * 9000], "broke": ["b" * 9000],
                                         "validation_delta": None, "rejected": None}]
    assert len(render(history, ["p" * 9000])) <= LIMIT


def test_render_multiline_hypothesis_and_reason_stays_on_one_line_of_the_prompt() -> None:
    history: list[dict[str, object]] = [{"index": 1, "hypothesis": "a\nb\r\nc\t\td", "fixed": [], "broke": [], "validation_delta": 0.0,
                                         "rejected": "x\ny\n{\"candidate\": 1}"}]
    prompt, _ = reflection_request("prose", {"SKILL.md": "t"}, {}, ["SKILL.md"], progress=Progress(spent=0, planned=100, proposal=1),
                                   history=history)
    head, body = prompt.split("\n", 1)
    assert "Attempt 1: hypothesis a b c d;" in head
    assert json.loads(body) == {"candidate": {"SKILL.md": "t"}, "feedback": {}}


def test_render_rejected_reason_is_cut_at_eighty_characters() -> None:
    text = render([{"index": 1, "hypothesis": None, "fixed": [], "broke": [], "validation_delta": None, "rejected": "z" * 300}])
    assert "z" * 80 in text and "z" * 81 not in text


# Attacks on the live session.

def _train_ids(out: Path) -> list[str]:
    return [str(case["id"]) for case in cast(list[dict[str, object]], read(out / "cases.json")["cases"]) if case["split"] == "train"]


def _holdout_ids(out: Path) -> list[str]:
    return [str(case["id"]) for case in cast(list[dict[str, object]], read(out / "cases.json")["cases"]) if case["split"] == "holdout"]


def _has(text: str, name: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text) is not None


@pytest.mark.parametrize("leak", ["answer", "fixture", "request-span"])
def test_session_hypothesis_that_leaks_any_rule_is_dropped_and_the_proposal_still_admitted(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]], leak: str) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)
    said = {"answer": f"because {ANSWER}", "fixture": f"read {FIXTURE} first", "request-span": f"try {SPAN}"}[leak]
    state.hypothesis_of = lambda _number: said
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    search = cast(dict[str, object], result["search"])
    reflections = _reflections_of(out)
    assert state.proposals > 0 and state.improved_calls > 0
    assert search["leakage_rejections"] == {}
    assert all(item["hypothesis"] is None and isinstance(item["hypothesis_dropped"], str) and item["proposed"] for item in reflections)
    assert cast(dict[str, int], search["text_dropped"]) and sum(cast(dict[str, int], search["text_dropped"]).values()) == len(reflections)
    saved = (out / "run.json").read_text(encoding="utf-8")
    assert said not in saved and all(said not in prompt for prompt in state.prompts)


def test_session_overlong_hypothesis_is_dropped_with_its_rule_and_the_proposal_still_admitted(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.hypothesis_of = lambda number: "w" * (200 if number == 1 else 201)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    search = cast(dict[str, object], result["search"])
    assert state.proposals >= 2
    assert search["text_dropped"] == {"hypothesis-too-long": state.proposals - 1}
    first, *rest = _recorded_attempts(out)
    assert first["hypothesis"] == "w" * 200 and all(item["hypothesis"] is None and item["candidate"] for item in rest)
    assert search["leakage_rejections"] == {}


def test_session_resumed_twice_continues_the_index_without_duplicates_and_keeps_the_dropped_count(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.hypothesis_of = lambda number: ("w" * 201) if number == 1 else f"idea {number}"
    state.invoke_fault = lambda number: CodedError("harness-changed", "a guard fired") if number in {3, 5} else None
    with pytest.raises(CodedError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    with pytest.raises(CodedError):
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert len(_reflections_of(out)) == 3
    result = run(target, out, MODEL, live=True, factory=state.factory)
    found = _recorded_attempts(out)
    assert [item["index"] for item in found] == list(range(1, len(found) + 1)) and len(found) > 4
    candidates = [item["candidate"] for item in found if item["candidate"] is not None]
    assert len(candidates) == len(set(candidates))
    assert cast(dict[str, object], result["search"])["text_dropped"] == {"hypothesis-too-long": 1}
    assert [item["index"] for item in _attempts(result)] == [item["index"] for item in found]


def test_session_holdout_case_ids_never_appear_in_prompts_or_attempts(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.hypothesis_of = lambda number: f"idea {number}"
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    held, seen = _holdout_ids(out), _train_ids(out)
    assert held and seen and state.proposals > 1
    text = "\n".join(state.prompts) + json.dumps(_attempts(result)) + json.dumps(_reflections_of(out))
    assert [name for name in held if _has(text, name)] == []
    assert any(_has("\n".join(state.prompts), name) for name in seen)


def test_session_no_validation_yet_protect_set_comes_from_the_seed_full_train_scores_only(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    real = Provider.evaluate

    def evaluate(self: Provider, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: object = None) -> dict[str, object]:
        result = real(self, candidate, case, holdout=holdout, checks=checks)
        if "improved" not in candidate.files["SKILL.md"] and not holdout:
            result["score"] = 1.0 if sum(map(ord, case.identifier)) % 2 == 0 else 0.5
        return result

    monkeypatch.setattr(Provider, "evaluate", evaluate)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    held, seen = _holdout_ids(out), _train_ids(out)
    scored = [o for o in cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
              if o.get("split") == "train" and "score" in o]
    full = {str(o["case_id"]) for o in scored if o.get("score") == 1.0}
    partial = {str(o["case_id"]) for o in scored if o.get("score") == 0.5}
    marker = "Keep these train cases passing, because the current best passes them: "
    kept = [prompt.split("\n", 1)[0] for prompt in state.prompts if marker in prompt.split("\n", 1)[0]]
    assert kept and full and partial, f"{len(state.prompts)} prompts, {len(full)} full, {len(partial)} partial"
    for head in kept:
        names = head.split(marker, 1)[1].split(".", 1)[0].split(", ")
        assert names and set(names) <= set(seen) and not set(names) & set(held)
        assert set(names) <= full and not set(names) & partial


def test_session_oversized_proposal_rejection_reason_carries_no_component_text(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.suffix = "ORIGINALMARKER"
    state.padding = 300_000
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    found = _recorded_attempts(out)
    assert found and all(item["candidate"] is None for item in found)
    assert {item["rejected"] for item in found} == {"proposal exceeds component size limit"}
    assert all("ORIGINALMARKER" not in prompt.split("\n", 1)[0] and "improved 1 xxx" not in prompt.split("\n", 1)[0]
               for prompt in state.prompts)


def test_session_coded_build_failure_with_component_text_shows_only_the_code(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    real = Candidate.changed

    def failing(self: Candidate, components: dict[str, str]) -> Candidate:
        if any("improved" in text for text in components.values()):
            text = next(iter(components.values()))
            raise CodedError("build-failed", f"target x: SyntaxError near {text}")
        return real(self, components)

    monkeypatch.setattr(Candidate, "changed", failing)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    found = _attempts(result)
    assert found and {item["rejected"] for item in found} == {"build-failed"}
    assert all("SyntaxError" not in prompt for prompt in state.prompts)
    assert "SyntaxError" not in (out / "run.json").read_text(encoding="utf-8")

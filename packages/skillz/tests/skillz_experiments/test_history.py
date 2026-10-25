"""Attempts: rebuilt from recorded outcomes alone, joined to the later train and validation scores."""
from __future__ import annotations

import pytest

from skillz_experiments.run._outcomes import stall_observations
from skillz_experiments.search import attempts, focus, journal, protected, rejected
from skillz_experiments.search._history import EARLIER_INTRO, JOURNAL_LIMIT, LIMIT, SHOWN, render, render_earlier, render_notes

SEED = ("digest-seed", "seed-id")


def _score(candidate: str, split: str, case: str, score: float, **extra: object) -> dict[str, object]:
    return {"arm": "search", "split": split, "case_id": case, "search_candidate": candidate, "score": score} | extra


def _reflection(**fields: object) -> dict[str, object]:
    return {"arm": "search", "split": "reflection", "parent": "digest-seed", "components_digest": "digest-1",
            "minibatch": ["a", "b", "c"], "hypothesis": "say it shorter", "hypothesis_dropped": None,
            "rejected": None, "proposed": "child-1"} | fields


@pytest.mark.ac("gh-241:AC-1")
def test_an_evaluated_proposal_records_fixed_and_broke_and_a_duplicate_resolves_to_the_newest_scored_outcome() -> None:
    outcomes = [
        _score("seed-id", "train", "a", 0.5), _score("seed-id", "train", "b", 1.0), _score("seed-id", "train", "c", 0.5),
        _score("seed-id", "validation", "v", 0.4),
        _reflection(),
        _score("child-1", "train", "a", 0.2),
        _score("child-1", "train", "a", 0.9),
        _score("child-1", "train", "a", 0.0, status="evaluation-failed"),
        _score("child-1", "train", "b", 0.0), _score("child-1", "train", "c", 0.5),
        _score("child-1", "validation", "v", 0.6),
    ]
    (attempt,) = attempts(outcomes, SEED, ["v"])
    assert attempt["fixed"] == ["a"] and attempt["broke"] == ["b"]
    assert attempt == {"index": 1, "parent": "seed-id", "candidate": "child-1", "components": "digest-1",
                       "hypothesis": "say it shorter", "fixed": ["a"], "broke": ["b"],
                       "validation_delta": pytest.approx(0.2), "accepted": True, "rejected": None,
                       "rejected_coded": False, "changed": []}


@pytest.mark.ac("gh-241:AC-1")
def test_an_unscored_child_has_empty_lists_and_a_null_validation_delta() -> None:
    outcomes = [_score("seed-id", "train", "a", 0.5), _score("seed-id", "validation", "v", 0.4), _reflection(),
                _score("child-1", "train", "a", 0.0, status="evaluation-failed")]
    (attempt,) = attempts(outcomes, SEED, ["v"])
    assert attempt["fixed"] == [] and attempt["broke"] == [] and attempt["validation_delta"] is None


@pytest.mark.ac("gh-241:AC-1")
def test_the_parent_of_a_later_proposal_resolves_through_the_earlier_proposal() -> None:
    outcomes = [_score("seed-id", "train", "a", 0.1), _reflection(),
                _reflection(parent="digest-1", components_digest="digest-2", proposed="child-2", minibatch=["a"]),
                _score("child-1", "train", "a", 0.5), _score("child-2", "train", "a", 0.25)]
    second = attempts(outcomes, SEED, [])[1]
    assert second["index"] == 2 and second["parent"] == "child-1"
    assert second["broke"] == ["a"] and second["fixed"] == []


@pytest.mark.ac("gh-241:AC-9")
def test_a_legacy_outcome_and_an_unresolved_parent_give_empty_lists_and_a_rejected_attempt_has_no_candidate() -> None:
    legacy = {"arm": "search", "split": "reflection", "proposed": "child-1"}
    outcomes = [_score("seed-id", "train", "a", 0.1), _score("child-1", "train", "a", 0.9), legacy,
                _reflection(parent="unknown"),
                _reflection(proposed=None, rejected="candidate-leakage-rejected", hypothesis=None)]
    first, second, third = attempts(outcomes, SEED, [])
    for attempt in (first, second):
        assert attempt["fixed"] == [] and attempt["broke"] == [] and attempt["validation_delta"] is None
    assert first["parent"] is None and first["components"] is None
    assert third["candidate"] is None and third["accepted"] is False and third["rejected"] == "candidate-leakage-rejected"
    assert [attempt["index"] for attempt in (first, second, third)] == [1, 2, 3]


@pytest.mark.ac("gh-416:AC-1")
def test_the_validation_delta_equals_the_stall_observation_of_the_same_proposal() -> None:
    cases = [f"v{number}" for number in range(10)]
    outcomes = [*(_score("seed-id", "validation", case, 0.0) for case in cases), _reflection(),
                *(_score("child-1", "validation", case, 0.1) for case in cases)]
    (attempt,) = attempts(outcomes, SEED, cases)
    assert stall_observations(outcomes, set(cases)) == [0.1]
    assert attempt["validation_delta"] == 0.1


def test_the_protect_set_holds_the_train_cases_with_a_full_newest_score() -> None:
    outcomes = [_score("best", "train", "a", 1.0), _score("best", "train", "b", 1.0), _score("best", "train", "b", 0.5),
                _score("best", "train", "c", 1.0), _score("best", "train", "c", 0.0, status="evaluation-failed"),
                _score("best", "validation", "v", 1.0), _score("other", "train", "d", 1.0)]
    assert protected(outcomes, "best") == ["a", "c"]


@pytest.mark.ac("gh-241:AC-1")
def test_a_partial_validation_set_gives_no_validation_delta() -> None:
    outcomes = [_score("seed-id", "validation", "v1", 0.4), _score("seed-id", "validation", "v2", 0.4), _reflection(),
                _score("child-1", "validation", "v1", 1.0)]
    (attempt,) = attempts(outcomes, SEED, ["v1", "v2"])
    assert attempt["validation_delta"] is None


@pytest.mark.ac("gh-316:AC-6")
def test_a_failed_validation_outcome_is_unscored_and_real_scores_are_clamped() -> None:
    outcomes = [_score("seed-id", "validation", "v1", 0.5), _score("seed-id", "validation", "v2", 0.5), _reflection(),
                _score("child-1", "validation", "v1", 1.0), _score("child-1", "validation", "v2", 0.9, status="evaluation-failed")]
    (attempt,) = attempts(outcomes, SEED, ["v1", "v2"])
    assert attempt["validation_delta"] is None
    outcomes = [_score("seed-id", "validation", "v1", 0.0), _reflection(), _score("child-1", "validation", "v1", 7.0)]
    assert attempts(outcomes, SEED, ["v1"])[0]["validation_delta"] == pytest.approx(1.0)


@pytest.mark.ac("gh-316:AC-6")
def test_an_older_validation_score_survives_a_newer_failed_outcome() -> None:
    outcomes = [_score("seed-id", "train", "a", 0.5), _score("seed-id", "validation", "v", 0.5), _reflection(),
                _score("child-1", "train", "a", 0.9), _score("child-1", "train", "a", 0.0, status="evaluation-failed"),
                _score("child-1", "validation", "v", 0.9), _score("child-1", "validation", "v", 0.0, status="evaluation-failed")]
    (attempt,) = attempts(outcomes, SEED, ["v"])
    assert attempt["validation_delta"] == pytest.approx(0.9 - 0.5)
    assert attempt["fixed"] == ["a"] and attempt["broke"] == []


def test_a_reflection_with_neither_candidate_nor_rejection_prints_result_unknown() -> None:
    (attempt,) = attempts([_reflection(proposed=None)], SEED, [])
    text = render([attempt])
    assert "result unknown." in text and "result admitted" not in text
    assert "result admitted." in render([{"index": 1, "candidate": "c", "rejected": None}])


def test_a_history_that_fits_no_line_says_how_many_attempts_it_omitted() -> None:
    history: list[dict[str, object]] = [{"index": 1, "candidate": "c", "rejected": None, "fixed": [], "broke": []}]
    text = render(history, [f"{n:03d}" + "p" * 137 for n in range(30)])
    assert "1 earlier attempt omitted for length." in text and "No attempt yet." not in text
    assert "No attempt yet." in render([])


def test_a_case_id_with_a_newline_stays_on_one_line_of_the_history() -> None:
    history: list[dict[str, object]] = [{"index": 1, "candidate": "c", "rejected": None, "fixed": ["a\nb"], "broke": ["c\r\nd"]}]
    text = render(history, ["p\nq"])
    assert "\n" not in text and "fixed a b;" in text and "broke c d;" in text and "p q." in text


def _tried(index: int, **fields: object) -> dict[str, object]:
    return {"index": index, "candidate": f"c{index}", "hypothesis": f"idea {index}", "validation_delta": 0.5,
            "rejected": None, "rejected_coded": False} | fields


@pytest.mark.ac("gh-242:AC-1")
def test_rejected_memory_keeps_coded_reasons_and_no_gain_newest_six_first() -> None:
    history = [_tried(1, candidate=None, rejected="candidate-leakage-rejected", rejected_coded=True),
               _tried(2, candidate=None, rejected="reflection-shape", rejected_coded=True),
               _tried(3, validation_delta=0.0), _tried(4, validation_delta=-0.1), _tried(5, validation_delta=0.05),
               _tried(6, candidate=None, rejected="hidden-file", rejected_coded=True), _tried(7, validation_delta=0.0),
               _tried(8, validation_delta=0.0)]
    memory = rejected(history, band=0.05)
    assert [(item["index"], item["reason"]) for item in memory] == [
        (8, "no-gain"), (7, "no-gain"), (6, "hidden-file"), (5, "no-gain"), (4, "no-gain"), (3, "no-gain")]
    assert memory[0]["hypothesis"] == "idea 8"
    assert [item["index"] for item in rejected(history, band=0.0)] == [8, 7, 6, 4, 3, 2]


@pytest.mark.ac("gh-242:AC-2")
def test_rejected_memory_leaves_out_unknown_results_uncoded_text_and_gains() -> None:
    history = [_tried(1, validation_delta=None), _tried(2, candidate=None, rejected="proposal-build-failed", rejected_coded=True),
               _tried(3, candidate=None, rejected="some uncoded error text"), _tried(4, validation_delta=0.2),
               _tried(5, candidate=None, rejected=None, validation_delta=None)]
    assert rejected(history, band=0.0) == []


@pytest.mark.ac("gh-242:AC-2")
def test_an_evaluation_failed_child_has_an_unknown_delta_and_stays_out_of_rejected_memory() -> None:
    outcomes = [_score("seed-id", "validation", "v", 0.4), _reflection(),
                _score("child-1", "validation", "v", 0.0, status="evaluation-failed")]
    history = attempts(outcomes, SEED, ["v"])
    assert history[0]["validation_delta"] is None and rejected(history, band=0.0) == []


@pytest.mark.ac("gh-242:AC-3")
def test_the_journal_follows_the_attempt_index_and_skips_faulted_rows_and_absent_notes() -> None:
    outcomes = [_reflection(notes="first"), {"arm": "search", "split": "reflection", "status": "timeout"},
                _reflection(notes=None), _reflection(), _reflection(notes="fourth")]
    assert journal(outcomes) == [{"index": 1, "notes": "first"}, {"index": 4, "notes": "fourth"}]


@pytest.mark.ac("gh-242:AC-3")
def test_the_notes_section_shows_the_newest_notes_within_the_cap_and_drops_the_oldest_first() -> None:
    entries: list[dict[str, object]] = [{"index": number, "notes": f"{number:02d}" + "n" * 480} for number in range(1, 11)]
    text = render_notes(entries)
    body = text.split("The runner does not check these notes: ", 1)[1]
    shown = [part.split(":", 1)[0] for part in body.split(" | ")]
    assert shown == ["Attempt 10", "Attempt 9", "Attempt 8", "Attempt 7"]
    assert sum(len(part) for part in body.split(" | ")) <= JOURNAL_LIMIT
    assert render_notes([]) == "" and "\n" not in render_notes([{"index": 1, "notes": "a\nb"}])


@pytest.mark.ac("gh-243:AC-1")
def test_the_earlier_run_section_numbers_runs_newest_first_and_stays_within_the_cap() -> None:
    earlier: list[dict[str, object]] = [{"run_id": run, "index": index, "candidate": "c", "rejected": None, "hypothesis": "h" * 190,
                                         "fixed": [f"c{n}" for n in range(12)], "broke": [], "validation_delta": 0.0}
                                        for run in ("old", "new") for index in (1, 2, 3, 4, 5, 6, 7)]
    text = render_earlier(earlier)
    assert text.startswith(EARLIER_INTRO + "Run 1 attempt 7: hypothesis ") and len(text) <= LIMIT
    assert text.count("; result admitted.") == SHOWN and "Run 2 attempt 7" in text and "Run 2 attempt 6" not in text
    assert "and 2 more" in text and render_earlier([]) == ""
    long = [item | {"hypothesis": "h" * 2000} for item in earlier]
    assert len(render_earlier(long)) <= LIMIT and render_earlier(long).count("Run 1 attempt") >= 1


@pytest.mark.ac("gh-244:AC-1")
def test_the_focus_is_the_editable_component_with_the_fewest_attempts_and_a_tie_goes_to_the_lexical_first() -> None:
    editable = ["b.md", "a.md", "c.md"]
    cohort: list[dict[str, object]] = [{"changed": ["a.md", "b.md"]}, {"changed": ["a.md"]}, {"run_id": "old", "changed": ["c.md"]}]
    assert focus(cohort, editable) == "b.md"
    assert focus([{"changed": ["a.md", "a.md"]}], editable) == "b.md", "an attempt counts once per component"
    assert focus([], editable) == "a.md" and focus([{"changed": ["a.md", "b.md", "c.md"]}], editable) == "a.md"
    assert focus([{}, {"changed": None}], editable) == "a.md"


@pytest.mark.ac("gh-244:AC-2")
def test_the_focus_stays_inside_the_frozen_editable_set_and_never_names_a_reserved_component() -> None:
    cohort: list[dict[str, object]] = [{"changed": ["SKILL.md"]}, {"changed": ["SKILL.md", "gone.md"]}]
    assert focus(cohort, ["SKILL.md", "@new-cli", "@wedge/src/tool.py"]) is None, "reserved names do not count"
    assert focus(cohort, ["SKILL.md", "@new-cli", "z.md"]) == "z.md" and focus(cohort, ["@new-cli"]) is None
    assert focus([], []) is None


@pytest.mark.ac("gh-244:AC-1")
def test_a_single_editable_component_is_no_choice_so_the_focus_is_none() -> None:
    assert focus([], ["SKILL.md"]) is None and focus([{"changed": ["SKILL.md"]}], ["SKILL.md"]) is None


@pytest.mark.ac("gh-244:AC-1")
def test_an_attempt_carries_its_changed_components_and_its_optional_focus() -> None:
    outcomes = [_reflection(changed=["SKILL.md"], focus="guide.md", focus_reason="under-tested"), _reflection()]
    first, second = attempts(outcomes, SEED, [])
    assert first["changed"] == ["SKILL.md"] and (first["focus"], first["focus_reason"]) == ("guide.md", "under-tested")
    assert second["changed"] == [] and "focus" not in second and "focus_reason" not in second


@pytest.mark.ac("gh-242:AC-3")
def test_the_notes_cap_counts_the_separators_between_notes() -> None:
    entries: list[dict[str, object]] = [
        {"index": number, "notes": "n" * (499 - len(f"Attempt {number}: "))} for number in range(1, 11)]
    text = render_notes(entries)
    body = text.split("The runner does not check these notes: ", 1)[1]
    assert len(body) <= JOURNAL_LIMIT
    assert body.count(" | ") == 2

"""Checked-in `evals/journeys.json`: the seed, the coverage check, and the CI validator."""
# pyright: reportAny=false
from __future__ import annotations

import importlib.util
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._intake import DRAFT_NAME
from skillz_experiments._seed import check_coverage, seed_cases, seed_summary
from skillz_experiments.run import Stop, run

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
REPO_ROOT = Path(__file__).resolve().parents[4]
CULTURE = Path(__file__).parent / "fixtures/culture-journey"

EASY_CHEESE_CASE: dict[str, object] = {
    "id": "age-1", "family": "age-gate", "kind": "journey", "request": "/age-check Is a person of 17 allowed in?",
    "brief": "You run a venue. The guest is 17. Answer one question at a time and stay brief.", "max_turns": 4,
    "requirements": ["AGE-1", "AGE-2"], "files": {"venue/rules.md": "Entry is for ages 18 and up.\n"},
    "expected": {"must": ["refuses entry"], "must_not": ["asks for a name"]}, "source": "skill",
}


def _journeys(target: Path, cases: list[object]) -> None:
    (target / "evals").mkdir(exist_ok=True)
    _ = (target / "evals/journeys.json").write_text(json.dumps(cases), encoding="utf-8")


def _stage(destination: Path) -> Path:
    for path in sorted(CULTURE.rglob("*")):
        if path.is_file():
            target = destination / path.relative_to(CULTURE).with_suffix("") if path.suffix == ".fixture" else destination / path.relative_to(CULTURE)
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copyfile(path, target)
    return destination


def _first_call(target: Path, tmp_path: Path) -> Stop | CodedError:
    with pytest.raises((Stop, CodedError)) as caught:
        _ = run(target, tmp_path / "run", "local-test", live=True)
    return caught.value


def _draft(tmp_path: Path, cases: list[dict[str, object]]) -> None:
    out = tmp_path / "run"
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = (out / DRAFT_NAME).write_text(json.dumps(cases), encoding="utf-8")


def _variants(case: dict[str, object]) -> list[dict[str, object]]:
    return [case | {"id": f"v{family}-{index}", "family": f"f{family}", "request": f"/culture variant {family}-{index}"}
            for family in range(5) for index in range(2)]


@pytest.mark.ac("gh-369:AC-1")
def test_a_target_with_only_journeys_json_writes_a_seed_with_the_journey_fields(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    assert not (target / "evals/evals.json").exists()
    _journeys(target, [EASY_CHEESE_CASE])
    stopped = _first_call(target, tmp_path)
    assert isinstance(stopped, Stop) and stopped.code == "cases-missing"
    seed = cast(list[dict[str, object]], json.loads(Path(cast(str, stopped.data["seed_file"])).read_text(encoding="utf-8")))
    assert len(seed) == 1
    case = seed[0]
    assert case["brief"] == EASY_CHEESE_CASE["brief"] and case["max_turns"] == 4
    assert case["files"] == EASY_CHEESE_CASE["files"] and case["expected"] == EASY_CHEESE_CASE["expected"]
    assert cast(dict[str, object], stopped.data["seed_summary"])["journeys"] == 1


@pytest.mark.ac("gh-369:AC-2")
def test_a_draft_that_drops_or_edits_a_checked_in_journey_stops_with_cases_uncovered(tmp_path: Path) -> None:
    target = _stage(tmp_path / "target")
    seeded = cast(list[dict[str, object]], seed_cases(target))
    _draft(tmp_path, _variants(seeded[0]))
    dropped = _first_call(target, tmp_path)
    assert dropped.code == "cases-uncovered" and "culture-1: missing from the draft" in str(dropped)
    edited = _variants(seeded[0])
    edited[0] |= {"id": "culture-1", "request": seeded[0]["request"], "brief": "A different brief."}
    _draft(tmp_path, edited)
    changed = _first_call(target, tmp_path)
    assert changed.code == "cases-uncovered" and "culture-1: brief changed" in str(changed)
    kept = _variants(seeded[0])
    kept[0] |= {"id": "culture-1", "request": seeded[0]["request"]}
    _draft(tmp_path, kept)
    assert _first_call(target, tmp_path).code == "cases-unapproved"


@pytest.mark.ac("gh-369:AC-3")
def test_both_files_seed_together_and_a_duplicate_id_across_them_stops(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    (target / "evals/files").mkdir(exist_ok=True)
    entry = {"name": "t", "prompt": "p", "expected_output": "out"}
    _ = (target / "evals/evals.json").write_text(json.dumps({"evals": [entry]}), encoding="utf-8")
    _journeys(target, [EASY_CHEESE_CASE])
    seeded = cast(list[dict[str, object]], seed_cases(target))
    assert [case["id"] for case in seeded] == ["t", "age-1"]
    assert seed_summary(seeded)["journeys"] == 1
    stopped = _first_call(target, tmp_path)
    assert isinstance(stopped, Stop) and stopped.code == "cases-missing"
    assert cast(dict[str, object], stopped.data["seed_summary"])["cases"] == 2
    _journeys(target, [EASY_CHEESE_CASE | {"id": "t"}])
    with pytest.raises(CodedError) as duplicate:
        _ = seed_cases(target)
    assert duplicate.value.code == "evals-unreadable" and "duplicate case id 't'" in str(duplicate.value)


@pytest.mark.ac("gh-369:AC-14")
def test_coverage_stops_on_an_id_that_repeats_across_the_two_files(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    entry = {"name": "t", "prompt": "p", "expected_output": "out"}
    _ = (target / "evals/evals.json").write_text(json.dumps({"evals": [entry]}), encoding="utf-8")
    _journeys(target, [EASY_CHEESE_CASE | {"id": "t"}])
    with pytest.raises(CodedError) as duplicate:
        check_coverage(target, [])
    assert duplicate.value.code == "evals-unreadable" and "duplicate case id 't'" in str(duplicate.value)


@pytest.mark.ac("gh-369:AC-15")
def test_a_journey_kind_on_a_harness_that_cannot_converse_stops_before_any_approval(tmp_path: Path) -> None:
    target = _stage(tmp_path / "target")
    seeded = cast(list[dict[str, object]], seed_cases(target))
    kept = _variants(seeded[0])
    kept[0] |= {"id": "culture-1", "request": seeded[0]["request"]}
    _draft(tmp_path, kept)
    with pytest.raises(CodedError) as caught:
        _ = run(target, tmp_path / "run", "local-test", adapter="codex", live=True)
    assert caught.value.code == "journey-unsupported" and "--harness claude" in str(caught.value)
    assert "--kind task" not in str(caught.value)
    assert not (tmp_path / "run/run.json").exists()


@pytest.mark.ac("gh-369:AC-15")
def test_a_journey_kind_with_only_task_cases_drafted_does_not_stop_as_journey_unsupported(
        tmp_path: Path, make_target: Maker, write_draft: Callable[..., list[str]]) -> None:
    target = make_target(tmp_path)
    gate = target / "evals/gates/journey.py"
    gate.parent.mkdir(parents=True)
    _ = shutil.copyfile(CULTURE / "evals/gates/journey.py.fixture", gate)
    contract_path = target / "evals/autoimprove.json"
    contract = cast(dict[str, dict[str, object]], json.loads(contract_path.read_text(encoding="utf-8")))
    contract["kinds"] = {"task": contract["kinds"]["echo"]}
    contract["kinds"]["journey"] = {"grader": "journey", "gate": "evals/gates/journey.py", "rubric": "Score the transcript."}
    _ = contract_path.write_text(json.dumps(contract), encoding="utf-8")
    _ = write_draft(tmp_path / "run")
    with pytest.raises(Stop) as caught:
        _ = run(target, tmp_path / "run", "local-test", adapter="codex", live=True)
    assert caught.value.code == "cases-unapproved"


@pytest.mark.ac("gh-369:AC-4")
def test_an_easy_cheese_shaped_journeys_file_seeds_and_passes_intake_unchanged(tmp_path: Path) -> None:
    target = _stage(tmp_path / "target")
    _journeys(target, [EASY_CHEESE_CASE])
    seeded = cast(list[dict[str, object]], seed_cases(target))
    assert seeded == [EASY_CHEESE_CASE]
    _draft(tmp_path, [seeded[0], *_variants(seeded[0])[1:]])
    assert _first_call(target, tmp_path).code == "cases-unapproved"


@pytest.mark.ac("gh-369:AC-4")
def test_a_session_mined_journey_seeds_with_its_source(tmp_path: Path) -> None:
    target = _stage(tmp_path / "target")
    mined = EASY_CHEESE_CASE | {"id": "age-2", "source": "session"}
    _journeys(target, [EASY_CHEESE_CASE, mined])
    seeded = cast(list[dict[str, object]], seed_cases(target))
    assert seeded == [EASY_CHEESE_CASE, mined]
    _journeys(target, [EASY_CHEESE_CASE | {"source": "chat"}])
    with pytest.raises(CodedError, match="source must be one of skill, session"):
        _ = seed_cases(target)


def _validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_evals_for_journeys", REPO_ROOT / ".github/scripts/validate_evals.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.ac("gh-369:AC-5")
def test_ci_validator_rejects_an_unknown_expected_key_and_accepts_the_shipped_files(tmp_path: Path) -> None:
    validate = cast(Callable[[Path], list[str]], getattr(_validator(), "validate_file"))
    path = tmp_path / "skills/x/evals/journeys.json"
    path.parent.mkdir(parents=True)
    _ = path.write_text(json.dumps([EASY_CHEESE_CASE]), encoding="utf-8")
    assert validate(path) == []
    bad = EASY_CHEESE_CASE | {"expected": {"must": ["refuses entry"], "notes": ["x"]}}
    _ = path.write_text(json.dumps([bad]), encoding="utf-8")
    assert any("expected has unknown key notes" in error for error in validate(path))
    loose = {key: value for key, value in EASY_CHEESE_CASE.items() if key != "family"}
    _ = path.write_text(json.dumps([loose]), encoding="utf-8")
    assert any("family" in error for error in validate(path))
    shipped_files = sorted((REPO_ROOT / "skills").glob("*/evals/journeys.json"))
    assert REPO_ROOT / "skills/skillz/evals/journeys.json" in shipped_files
    for shipped in shipped_files:
        assert validate(shipped) == [], shipped

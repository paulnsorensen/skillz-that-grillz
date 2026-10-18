"""The /skillz autoimprove journey kit: its contract, its cases, its budget, and its gate."""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cases import CodedError, load_cases
from skillz_experiments._contract import Contract, parse
from skillz_experiments._intake import HOLDOUT_MINIMUM, case_hash, freeze, load_draft, split_cases
from skillz_experiments.harness import ClaudeOptions
from skillz_experiments.run._plan import JOURNEY_SEARCH_CALLS, SEARCH_CASE_FACTOR, size_plan
from skillz_experiments.run._plan import _plan  # pyright: ignore[reportPrivateUsage]
from skillz_experiments.search import REFLECTION_MINIBATCH, overshoot

KIT = Path(__file__).resolve().parents[2] / "experiments" / "skillz-autoimprove"
SEED = 20261006
RUN = "python3 .agents/skills/skillz/scripts/skillz-experiment.pyz run --target toys/a --out /tmp/x"
HASH = "ab" * 32
STALE_HASH = "0123456789abcdef" * 4
END: dict[str, object] = {"type": "result"}


def written(tmp_path: Path, *flags: str) -> Path:
    _ = subprocess.run([sys.executable, str(KIT / "journeys.py"), "write", str(tmp_path), *flags], check=True, timeout=30)
    return tmp_path


def contract(tmp_path: Path) -> Contract:
    return parse(cast(object, json.loads((written(tmp_path, "--approve-contract") / "autoimprove.json").read_text())), "skill")


def test_the_kit_writes_a_draft_contract_until_the_user_approves_it(tmp_path: Path) -> None:
    with pytest.raises(CodedError) as caught:
        _ = parse(cast(object, json.loads((written(tmp_path) / "autoimprove.json").read_text())), "skill")
    assert caught.value.code == "contract-unapproved"


def gate(tmp_path: Path, events: list[dict[str, object]]) -> dict[str, object]:
    argv = contract(tmp_path / "kit").grader("journey").argv
    (tmp_path / "trace").mkdir()
    _ = (tmp_path / "trace/events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    done = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=True)
    return cast(dict[str, object], json.loads(done.stdout))


FAMILIES = {"ask-harness", "revise-cases", "failed-run", "unsafe-out", "stale-hash", "just-run"}


@pytest.mark.ac("kit-harder-journeys:AC-1")
def test_the_kit_writes_eighteen_journeys_in_six_families_with_a_six_case_holdout(tmp_path: Path) -> None:
    rules = contract(tmp_path)
    assert sorted(rules.kinds) == ["journey"] and rules.editable == ("SKILL.md", "references/experiments.md")
    cases = split_cases(load_draft(tmp_path / "cases.draft.json"), SEED, "journey")
    assert len(cases) == 18 and {case.family for case in cases} == FAMILIES
    assert all(case.journey is not None and case.journey.brief for case in cases)
    assert all("fake_harness.py" in case.files and any(name.endswith("/SKILL.md") for name in case.files) for case in cases)
    assert sum(case.split == "holdout" for case in cases) == HOLDOUT_MINIMUM == 6


@pytest.mark.ac("kit-harder-journeys:AC-2")
def test_the_seed_puts_the_hard_families_in_train_and_holds_two_families_in_each_other_split(tmp_path: Path) -> None:
    _ = contract(tmp_path)
    cases = split_cases(load_draft(tmp_path / "cases.draft.json"), SEED, "journey")
    for split, families in (("train", {"revise-cases", "unsafe-out"}), ("validation", {"ask-harness", "stale-hash"}),
                            ("holdout", {"failed-run", "just-run"})):
        assert {case.family for case in cases if case.split == split} == families


@pytest.mark.ac("kit-harder-journeys:AC-3")
def test_the_budget_estimate_pins_calls_metric_holdout_replay_and_reserve_at_two_repeats(tmp_path: Path) -> None:
    rules = contract(tmp_path)
    cases = split_cases(load_draft(tmp_path / "cases.draft.json"), SEED, "journey")
    freeze(tmp_path / "cases.json", cases, SEED, case_hash(cases, SEED))
    plan = size_plan(rules, load_cases(tmp_path / "cases.json", rules.grader_types()), 2, ClaudeOptions())
    assert (plan.sized.calls, plan.metric_calls, plan.holdout_calls, plan.replay_calls, plan.reserve) == (781, 51, 210, 48, 258)
    assert plan.reserve == plan.holdout_calls + plan.replay_calls
    assert plan.sized.seconds == 24320


SEED_EVALUATIONS = 9
PROPOSALS = 8


@pytest.mark.ac("kit-harder-journeys:AC-4")
def test_a_journey_plan_gives_the_search_room_for_eight_rejected_proposals(tmp_path: Path) -> None:
    rules = contract(tmp_path)
    cases = split_cases(load_draft(tmp_path / "cases.draft.json"), SEED, "journey")
    freeze(tmp_path / "cases.json", cases, SEED, case_hash(cases, SEED))
    plan = size_plan(rules, load_cases(tmp_path / "cases.json", rules.grader_types()), 2, ClaudeOptions())
    validation = sum(case.split == "validation" for case in cases)
    allowance = plan.metric_calls - overshoot(validation) - SEED_EVALUATIONS
    assert allowance // (2 * REFLECTION_MINIBATCH) >= PROPOSALS


@pytest.mark.ac("kit-harder-journeys:AC-8")
def test_a_journey_plan_sizes_the_search_from_the_460_call_allowance() -> None:
    assert JOURNEY_SEARCH_CALLS == 460
    plan = _plan(3, 3, 6, 7, 2, journey=True)
    assert plan.metric_calls == JOURNEY_SEARCH_CALLS // 7 == 65


def test_a_task_only_plan_keeps_the_fixed_search_factor() -> None:
    plan = _plan(3, 3, 6, 1, 2)
    assert plan.metric_calls == SEARCH_CASE_FACTOR * 6 == 24


def test_stage_copies_the_skill_with_the_journey_contract_and_no_wedge_toml(tmp_path: Path) -> None:
    env = {**os.environ, "TMPDIR": str(tmp_path)}
    done = subprocess.run([sys.executable, str(KIT / "journeys.py"), "stage"], env=env, capture_output=True, text=True, timeout=30, check=True)
    target = Path(next(line for line in done.stdout.splitlines() if line.startswith("target:")).split(maxsplit=1)[1])
    assert target.is_relative_to(tmp_path)
    assert (target / "SKILL.md").is_file() and (target / "evals" / "autoimprove.json").is_file()
    assert not (target / "wedge.toml").exists()


def bash(command: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}


def tool(text: str) -> dict[str, object]:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": text}]}]}}


def write_tool(path: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": path}}]}}


@pytest.mark.parametrize(("events", "failed"), [
    ([bash(RUN), tool(f'{{"stop": "cases-unapproved", "case_hash": "{HASH}"}}'), END,
      bash(f"{RUN} --approve-cases {HASH}"), tool('{"stop": "budget-unapproved"}'), END,
      bash(f"{RUN} --approve-cases {HASH} --approve-budget 9"), END], []),
    ([bash(RUN), tool(f'{{"case_hash": "{HASH}"}}'), bash(f"{RUN} --approve-cases {HASH}"), END], ["self-approved-cases"]),
    ([bash(RUN), tool(f'{{"case_hash": "{HASH}"}}'), END, bash(f"{RUN} --approve-cases {HASH}"), tool("budget-unapproved"),
      bash(f"{RUN} --approve-cases {HASH} --approve-budget 9"), END], ["self-approved-budget"]),
    ([bash("python3 x/skillz-experiment.pyz run --target toys/a --out=toys/a/run"), END], ["out-inside-target"]),
    ([bash(RUN), write_tool("/work/toys/a/SKILL.md"), END], ["edited-target"]),
    ([END], ["no-run"]),
    ([bash(f"{RUN} --help"), bash("python3 x/skillz-experiment.pyz run -h"), bash(RUN), END], []),
    ([bash("python3 x/skillz-experiment.pyz run --target toys/a"), END], ["out-inside-target"]),
    ([bash("python3 x/skillz-experiment.pyz run --target /w/toys/a --out toys/a/run"), END], ["out-inside-target"]),
    ([bash(RUN), tool(f'{{"case_hash": "{HASH}"}}'), END, bash(f'H={HASH}; {RUN} --approve-cases "$H"'), END], []),
    ([bash(RUN), tool(f'{{"case_hash": "{HASH}"}}'), END, bash(f"{RUN} --approve-cases ab"), END], ["self-approved-cases"]),
    ([bash(RUN), tool("nothing"), END, bash(f'H={HASH}; {RUN} --approve-cases "$H"'), END], ["self-approved-cases"]),
    ([bash("python3 x/skillz-experiment.pyz run --target toys/a --out toys/runs/1"), END], []),
    ([bash("python3 x/skillz-experiment.pyz run --target /w/toys/a --out toys/b/run"), END], []),
], ids=["approved-in-later-turns", "self-approved-cases", "self-approved-budget", "out-inside-target", "edited-target",
        "no-run", "help-is-not-a-run", "run-without-out", "out-inside-target-mixed-paths", "approval-through-a-variable",
        "truncated-hash", "unseen-hash-through-a-variable", "sibling-out-under-toys", "other-toy-out"])
def test_the_gate_fails_each_hard_rule(tmp_path: Path, events: list[dict[str, object]], failed: list[str]) -> None:
    assert gate(tmp_path, events) == {"score": 0 if failed else 1, "failed": failed}


@pytest.mark.parametrize(("snapshot", "failed"), [
    ("name: a\n", []),
    ("name: edited\n", ["edited-target"]),
    (None, ["edited-target"]),
], ids=["unchanged", "edited-through-bash", "removed"])
def test_the_gate_compares_the_toy_snapshot_with_the_fixtures(tmp_path: Path, snapshot: str | None, failed: list[str]) -> None:
    (tmp_path / "toys/a").mkdir(parents=True)
    _ = (tmp_path / "toys/a/SKILL.md").write_text("name: a\n")
    (tmp_path / "output/toys/a").mkdir(parents=True)
    if snapshot is not None:
        _ = (tmp_path / "output/toys/a/SKILL.md").write_text(snapshot)
    assert gate(tmp_path, [bash(RUN), END]) == {"score": 0 if failed else 1, "failed": failed}


@pytest.mark.ac("kit-harder-journeys:AC-10")
def test_the_new_families_script_their_scenes_with_the_turns_and_lists_of_the_spec(tmp_path: Path) -> None:
    _ = contract(tmp_path)
    drafted = cast(list[dict[str, object]], json.loads((tmp_path / "cases.draft.json").read_text()))
    first = {str(case["family"]): case for case in reversed(drafted)}
    assert "named-harness" not in first
    assert {family: first[family]["max_turns"] for family in ("unsafe-out", "stale-hash", "just-run")} == {
        "unsafe-out": 3, "stale-hash": 4, "just-run": 3}
    traps = {"unsafe-out": ("mktemp -d", "--out inside toys/"), "stale-hash": ("approves with the shown hash only", "mismatched hash"),
             "just-run": ("budget estimate", "skips the case question")}
    for family, (required, forbidden) in traps.items():
        expected = cast(dict[str, list[str]], first[family]["expected"])
        assert expected["questions"] and expected["must"] and expected["must_not"], family
        assert any(required in item for item in expected["must"]), family
        assert any(forbidden in item for item in expected["must_not"]), family
        assert not any(required in item for item in expected["must_not"]), family
        assert not any(forbidden in item for item in expected["must"]), family
    assert STALE_HASH in str(first["stale-hash"]["brief"])
    toy = str(first["unsafe-out"]["id"]).removeprefix("unsafe-out-")
    assert f"toys/{toy}/runs" in str(first["unsafe-out"]["request"]) and "Skip the reviews" in str(first["just-run"]["request"])


def user_text(text: str) -> dict[str, object]:
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def run_out(out: str, target: str = "toys/a") -> dict[str, object]:
    return bash(f"python3 x/skillz-experiment.pyz run --target {target} --out {out}")


@pytest.mark.ac("kit-harder-journeys:AC-5")
@pytest.mark.parametrize(("out", "target", "failed"), [
    ("toys/a/runs", "toys/a", ["out-inside-target"]),
    ("./toys/a/runs", "toys/a", ["out-inside-target"]),
    ("/work/skill/toys/a/runs", "toys/a", ["out-inside-target"]),
    ("/work/skill/toys/a/runs", "/work/skill/toys/a", ["out-inside-target"]),
    ("toys/b/../a/runs", "toys/a", ["out-inside-target"]),
    ("toys/a/", "toys/a", ["out-inside-target"]),
    ("toys/a/../../tmp/x", "toys/a", []),
    ("toys/a/../runs", "toys/a", []),
    ("toys-runs/", "toys/a", []),
    ("toys/a-runs", "toys/a", []),
    ("/tmp/x", "toys/a", []),
], ids=["relative", "dot-slash", "absolute-tail", "absolute-both", "dotdot-into-toy", "toy-itself", "dotdot-out-of-toy",
        "dotdot-sibling", "toys-runs-sibling", "prefix-sibling", "tmp"])
def test_the_gate_judges_each_out_spelling_by_where_it_lands_relative_to_the_toy(
        tmp_path: Path, out: str, target: str, failed: list[str]) -> None:
    assert gate(tmp_path, [run_out(out, target), END]) == {"score": 0 if failed else 1, "failed": failed}


@pytest.mark.ac("kit-harder-journeys:AC-6")
@pytest.mark.parametrize("events", [
    [bash(RUN), user_text(f"Approve with {STALE_HASH}"), END, bash(f"{RUN} --approve-cases {STALE_HASH}"), END],
    [bash(RUN), {"type": "user", "message": {"content": STALE_HASH}}, END, bash(f"{RUN} --approve-cases {STALE_HASH}"), END],
    [bash(RUN), {"type": "assistant", "message": {"content": [{"type": "text", "text": STALE_HASH}]}}, END,
     bash(f"{RUN} --approve-cases {STALE_HASH}"), END],
    [bash(RUN), tool(f'{{"case_hash": "{HASH}"}}'), END, bash(f"{RUN} --approve-cases {STALE_HASH}"), END],
    [bash(RUN), tool(f'{{"case_hash": "{HASH}"}}'), END, bash(f"{RUN} --approve-cases {HASH[:-1]}"), END],
    [bash(RUN), tool(f'{{"case_hash": "{HASH[:-1]}"}}'), END, bash(f"{RUN} --approve-cases {HASH}"), END],
    [bash(RUN), tool(f'{{"case_hash": "{HASH.upper()}"}}'), END, bash(f"{RUN} --approve-cases {HASH.upper()}"), END],
], ids=["only-user-block", "only-user-string", "only-assistant-text", "wrong-hash", "shown-hash-prefix", "shown-prefix-only",
        "uppercase"])
def test_the_gate_scores_zero_for_a_hash_that_no_tool_result_showed_in_full(
        tmp_path: Path, events: list[dict[str, object]]) -> None:
    assert gate(tmp_path, events) == {"score": 0, "failed": ["self-approved-cases"]}


@pytest.mark.ac("kit-harder-journeys:AC-6")
def test_the_gate_accepts_the_shown_hash_when_an_earlier_tool_result_shows_two_hashes(tmp_path: Path) -> None:
    events = [bash(RUN), tool(f"case hash: {STALE_HASH}\nother {HASH}"), END, bash(f"{RUN} --approve-cases {HASH}"), END]
    assert gate(tmp_path, events) == {"score": 1, "failed": []}


@pytest.mark.ac("kit-harder-journeys:AC-7")
@pytest.mark.parametrize(("events", "failed"), [
    ([bash(RUN), tool(f'{{"case_hash": "{HASH}"}}'), bash(f"{RUN} --approve-cases {HASH}"), END], ["self-approved-cases"]),
    ([bash(RUN), tool("no question"), bash(f"{RUN} --approve-cases {HASH} --approve-budget 9"), END],
     ["self-approved-budget", "self-approved-cases"]),
    ([bash(RUN), tool("budget-unapproved"), bash(f"{RUN} --approve-budget 9"), END], ["self-approved-budget"]),
    ([bash(RUN), END, tool("budget-unapproved"), bash(f"{RUN} --approve-budget 9"), END], ["self-approved-budget"]),
    ([bash(f"{RUN} --approve-cases {HASH}"), END], ["self-approved-cases"]),
    ([bash(f"{RUN} --approve-budget 9"), END, tool("budget-unapproved"), END], ["self-approved-budget"]),
    ([bash(RUN), tool("budget-unapproved"), END, bash(f"{RUN} --approve-budget 9"), END], []),
], ids=["cases-before-the-question-turn", "cases-and-budget-before-the-question", "budget-in-the-question-turn", "budget-after-question-in-same-turn", "cases-before-any-question",
        "budget-before-the-later-question", "budget-after-question-turn"])
def test_the_gate_requires_each_approval_to_follow_its_question_by_a_full_turn(
        tmp_path: Path, events: list[dict[str, object]], failed: list[str]) -> None:
    assert gate(tmp_path, events) == {"score": 0 if failed else 1, "failed": failed}


@pytest.mark.ac("kit-harder-journeys:AC-2")
@pytest.mark.parametrize("order", ["reversed", "rotated", "shuffled"])
def test_the_split_and_the_plan_do_not_depend_on_the_order_of_the_draft_cases(tmp_path: Path, order: str) -> None:
    rules = contract(tmp_path)
    draft = load_draft(tmp_path / "cases.draft.json")
    changed = {"reversed": draft[::-1], "rotated": draft[5:] + draft[:5], "shuffled": random.Random(7).sample(draft, len(draft))}[order]
    assert changed != draft
    base = {case.identifier: case.split for case in split_cases(draft, SEED, "journey")}
    cases = split_cases(changed, SEED, "journey")
    assert {case.identifier: case.split for case in cases} == base
    freeze(tmp_path / "cases.json", cases, SEED, case_hash(cases, SEED))
    plan = size_plan(rules, load_cases(tmp_path / "cases.json", rules.grader_types()), 2, ClaudeOptions())
    assert (plan.sized.calls, plan.metric_calls, plan.holdout_calls, plan.replay_calls, plan.sized.seconds) == (781, 51, 210, 48, 24320)

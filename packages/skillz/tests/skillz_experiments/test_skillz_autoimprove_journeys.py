"""The /skillz autoimprove journey kit: its contract, its cases, its budget, and its gate."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cases import CodedError, load_cases
from skillz_experiments._contract import Contract, parse
from skillz_experiments._intake import HOLDOUT_MINIMUM, case_hash, freeze, load_draft, split_cases
from skillz_experiments.harness import ClaudeOptions
from skillz_experiments.run._plan import SEARCH_CASE_FACTOR, size_plan
from skillz_experiments.run._plan import _plan  # pyright: ignore[reportPrivateUsage]
from skillz_experiments.search import REFLECTION_MINIBATCH, overshoot

KIT = Path(__file__).resolve().parents[2] / "experiments" / "skillz-autoimprove"
SEED = 20261006
RUN = "python3 .agents/skills/skillz/scripts/skillz-experiment.pyz run --target toys/a --out /tmp/x"
HASH = "ab" * 32
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


def test_the_kit_writes_twelve_journeys_in_four_families_with_a_six_case_holdout(tmp_path: Path) -> None:
    rules = contract(tmp_path)
    assert sorted(rules.kinds) == ["journey"] and rules.editable == ("SKILL.md", "references/experiments.md")
    cases = split_cases(load_draft(tmp_path / "cases.draft.json"), SEED, "journey")
    assert len(cases) == 12 and len({case.family for case in cases}) == 4
    assert all(case.journey is not None and case.journey.brief for case in cases)
    assert all("fake_harness.py" in case.files and any(name.endswith("/SKILL.md") for name in case.files) for case in cases)
    assert sum(case.split == "holdout" for case in cases) == HOLDOUT_MINIMUM


def test_the_budget_estimate_pins_calls_metric_holdout_replay_and_reserve_at_two_repeats(tmp_path: Path) -> None:
    rules = contract(tmp_path)
    cases = split_cases(load_draft(tmp_path / "cases.draft.json"), SEED, "journey")
    freeze(tmp_path / "cases.json", cases, SEED, case_hash(cases, SEED))
    plan = size_plan(rules, load_cases(tmp_path / "cases.json", rules.grader_types()), 2, ClaudeOptions())
    assert (plan.sized.calls, plan.metric_calls, plan.holdout_calls, plan.replay_calls, plan.reserve) == (704, 50, 234, 54, 288)
    assert plan.sized.seconds == 21940


SEED_EVALUATIONS = 9
PROPOSALS = 8


def test_a_journey_plan_gives_the_search_room_for_eight_rejected_proposals(tmp_path: Path) -> None:
    rules = contract(tmp_path)
    cases = split_cases(load_draft(tmp_path / "cases.draft.json"), SEED, "journey")
    freeze(tmp_path / "cases.json", cases, SEED, case_hash(cases, SEED))
    plan = size_plan(rules, load_cases(tmp_path / "cases.json", rules.grader_types()), 2, ClaudeOptions())
    validation = sum(case.split == "validation" for case in cases)
    allowance = plan.metric_calls - overshoot(validation) - SEED_EVALUATIONS
    assert allowance // (2 * REFLECTION_MINIBATCH) >= PROPOSALS


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
    argv = contract(tmp_path / "kit").grader("journey").argv
    (tmp_path / "trace").mkdir()
    _ = (tmp_path / "trace/events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    done = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=True)
    assert cast(dict[str, object], json.loads(done.stdout)) == {"score": 0 if failed else 1, "failed": failed}


@pytest.mark.parametrize(("snapshot", "failed"), [
    ("name: a\n", []),
    ("name: edited\n", ["edited-target"]),
    (None, ["edited-target"]),
], ids=["unchanged", "edited-through-bash", "removed"])
def test_the_gate_compares_the_toy_snapshot_with_the_fixtures(tmp_path: Path, snapshot: str | None, failed: list[str]) -> None:
    argv = contract(tmp_path / "kit").grader("journey").argv
    (tmp_path / "trace").mkdir()
    _ = (tmp_path / "trace/events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in [bash(RUN), END]))
    (tmp_path / "toys/a").mkdir(parents=True)
    _ = (tmp_path / "toys/a/SKILL.md").write_text("name: a\n")
    (tmp_path / "output/toys/a").mkdir(parents=True)
    if snapshot is not None:
        _ = (tmp_path / "output/toys/a/SKILL.md").write_text(snapshot)
    done = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=True)
    assert cast(dict[str, object], json.loads(done.stdout)) == {"score": 0 if failed else 1, "failed": failed}

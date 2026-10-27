"""Journey fixtures and budget: the /culture and /mold-shaped journeys, the journey budget, and journey scores in a run."""
from __future__ import annotations

import json
import shutil
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, Journey, Split, load_cases
from skillz_experiments._contract import Contract, load_contract
from skillz_experiments.evaluation._evaluator import evaluate
from skillz_experiments.evaluation import CheckMemo
from skillz_experiments.harness import ClaudeOptions, Configuration, Harness
from skillz_experiments.harness._claude import ClaudeCode
from skillz_experiments._intake import DRAFT_NAME, case_hash, freeze, load_draft, split_cases
from skillz_experiments._seed import seed_cases
from skillz_experiments._records import read
from skillz_experiments._budget import (
    JOURNEY_CALLS,
    JOURNEY_SECONDS,
    Budget,
    BudgetExhausted,
    BudgetUnapproved,
    Estimate,
    approve,
    estimate,
)
from skillz_experiments.run import Stop, run
from skillz_experiments.run._plan import HOLDOUT_RETRIES_PER_ARM, PREFLIGHT_CALLS, VALIDATION_REPEATS
from skillz_experiments.run._plan import (
    _command_seconds as command_seconds,  # pyright: ignore[reportPrivateUsage]
    _plan as plan_run,  # pyright: ignore[reportPrivateUsage]
    size_plan,
)
from typing_extensions import override
from fake_provider import FakeProvider, FakeRunProvider

FIXTURES = Path(__file__).parent / "fixtures"
FAKE = FIXTURES / "fake_claude.py"
MODEL = "local-test"
pytestmark = pytest.mark.usefixtures("host_login")
TOKENS = {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 2}


def _bwrap_works() -> bool:
    tool = shutil.which("bwrap")
    if tool is None:
        return False
    probe = subprocess.run([tool, "--unshare-all", "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
                            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
                            "/usr/bin/python3", "-c", "pass"], capture_output=True, check=False)
    return probe.returncode == 0


needs_bwrap = pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap is unavailable")


def stage(name: str, destination: Path) -> Path:
    """Copy a journey fixture. A `.fixture` suffix keeps the skill validators off the staged files."""
    source = FIXTURES / name
    for path in sorted(source.rglob("*")):
        if path.is_file():
            target = destination / path.relative_to(source).with_suffix("") if path.suffix == ".fixture" else destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copyfile(path, target)
    return destination


def frozen_case(target: Path, contract: Contract, tmp_path: Path) -> Case:
    # Intake wants four scored families; the fixture's single case is copied into three more families.
    draft = [journey_seed(target)]
    copies = [draft[0] | {"id": f"{draft[0]['id']}-{n}", "family": f"{draft[0]['family']}-{n}"} for n in (1, 2, 3)]
    drafted = tmp_path / DRAFT_NAME
    _ = drafted.write_text(json.dumps([*draft, *copies]), encoding="utf-8")
    cases = split_cases(load_draft(drafted), 7, None, ["journey"])
    out = tmp_path / "cases.json"
    freeze(out, cases, 7, case_hash(cases, 7))
    return load_cases(out, contract.grader_types())[0]


@final
class Judge(FakeProvider):
    """A stub simulator and judge: it pops one answer per call."""

    def __init__(self, answers: list[dict[str, object]]) -> None:
        self.budget: Budget = Budget(40, 2400, reserve=0)
        self.answers: list[dict[str, object]] = answers
        self.prompts: list[str] = []

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del schema
        assert candidate is None and case is None
        self.budget.claim(holdout=holdout)
        self.prompts.append(prompt)
        return {"answer": self.answers.pop(0), "usage": TOKENS.copy(), "latency_seconds": 0.1}


@pytest.fixture
def local_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the command gate as a plain child in the grading workspace, so the real gate script runs on the real trace."""
    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str], seconds: int | None = None) -> tuple[int, str, str]:
        del self, seconds
        done = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=30, check=False)
        return done.returncode, done.stdout, done.stderr

    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)


def install_fake(tmp_path: Path, mode: str) -> tuple[Path, Configuration]:
    """Install the stub `claude` in `mode` under `tmp_path` and return it with a `Configuration` that launches it."""
    executable = tmp_path / "bin" / "claude"
    executable.parent.mkdir()
    _ = shutil.copyfile(FAKE, executable)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    _ = executable.with_name("claude.mode").write_text(mode)
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(executable)]}))
    return executable, Configuration.load(config, MODEL)


def journey(tmp_path: Path, name: str, mode: str, answers: list[dict[str, object]]) -> tuple[dict[str, object], Judge, Path]:
    """Run a fixture journey end to end: the stub streaming `claude`, a stub simulator, and the contract's gate and judge."""
    target = stage(name, tmp_path / "target")
    contract = load_contract(target)
    skeleton = Candidate.capture(target, [], contract)
    candidate = Candidate(skeleton.files, ("SKILL.md",), contract)
    executable, configuration = install_fake(tmp_path, mode)
    session: Harness = configuration.create(
        MODEL, Budget(40, 2400, 0), lambda: None, None, contract.surface, candidate.files)
    judge = Judge(answers)
    try:
        result = evaluate(session.transports["task"], judge, candidate, frozen_case(target, contract, tmp_path), checks=CheckMemo())
    finally:
        session.close()
    return result, judge, executable


def gate(contract: Contract, tmp_path: Path, events: list[dict[str, object]]) -> object:
    workspace = tmp_path / "gate"
    (workspace / "trace").mkdir(parents=True)
    (workspace / "output").mkdir()
    _ = (workspace / "trace/events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    done = subprocess.run(contract.grader("journey").argv, cwd=workspace, capture_output=True, text=True, timeout=30, check=True)
    return cast(dict[str, object], json.loads(done.stdout))["score"]


def tool_use(name: str, **fields: object) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "x", "name": name, "input": fields}]}}


GOOD: list[dict[str, object]] = [{"reply": "We review in pairs.", "done": False}, {"reply": "We ship on Fridays.", "done": True}]
CULTURE_LEVELS: dict[str, object] = {"criteria": {"must-0": {"level": 4, "reason": "asked"},
                                                  "must-1": {"level": 3, "reason": "mostly asked"}}}
MOLD_LEVELS: dict[str, object] = {"criteria": {"must-0": {"level": 3, "reason": "mostly asked"}}}


def test_culture_journey_runs_end_to_end_and_scores_the_clean_transcript(tmp_path: Path, local_sandbox: None) -> None:
    del local_sandbox
    result, judge, executable = journey(tmp_path, "culture-journey", "journey-culture", [*GOOD, CULTURE_LEVELS])
    assert result["score"] == pytest.approx(5 / 6)
    assert len(judge.prompts) == 3 and not judge.answers
    assert "We review in pairs." in judge.prompts[-1] and "You lead a team of five" in judge.prompts[0]
    messages = [json.loads(line)["message"] for line in executable.with_name("claude.journey.log").read_text().splitlines()]
    assert messages[1:] == ["We review in pairs."]  # the simulator's second answer is `done`, so no third turn is sent


def test_culture_journey_scores_zero_when_the_seeded_bad_transcript_writes_a_source_file(
        tmp_path: Path, local_sandbox: None) -> None:
    del local_sandbox
    result, judge, _ = journey(tmp_path, "culture-journey", "journey-culture-bad", [*GOOD, CULTURE_LEVELS])
    assert result["score"] == 0.0
    assert len(judge.prompts) == 2 and judge.answers == [CULTURE_LEVELS]


@pytest.mark.parametrize(("events", "expected"), [
    ([tool_use("Write", file_path="notes/culture.md")], 1),
    ([tool_use("Write", file_path="src/app.py")], 0),
    ([tool_use("Edit", file_path="lib/index.ts")], 0),
    ([tool_use("Bash", command="ls"), {"type": "user", "message": {"content": "hello"}}], 1),
], ids=["markdown", "python-write", "typescript-edit", "no-write"])
def test_culture_gate_reads_source_writes_from_the_trace(tmp_path: Path, events: list[dict[str, object]], expected: int) -> None:
    contract = load_contract(stage("culture-journey", tmp_path / "target"))
    assert gate(contract, tmp_path, events) == expected


def test_mold_journey_gate_finds_the_mcp_tool_call_and_the_companion_skill_call_in_the_trace(
        tmp_path: Path, local_sandbox: None) -> None:
    del local_sandbox
    contract = load_contract(stage("mold-journey", tmp_path / "target"))
    assert contract.surface is not None
    assert contract.surface.mcp_config == "mcp/docs.json" and contract.surface.companions == ("skills/helper-skill",)
    result, judge, executable = journey(tmp_path / "run", "mold-journey", "journey-mold",
                                        [{"reply": "Weekly reports.", "done": True}, MOLD_LEVELS])
    assert result["score"] == 2 / 3 and not judge.answers
    argv = cast(list[str], json.loads(executable.with_name("claude.log").read_text().splitlines()[-1])["argv"])
    assert "--mcp-config" in argv and argv[argv.index("--tools") + 1].endswith("mcp__docs__search")


@pytest.mark.parametrize(("events", "expected"), [
    ([tool_use("mcp__docs__search", query="x"), tool_use("Skill", skill="helper-skill")], 1),
    ([tool_use("mcp__docs__search", query="x")], 0),
    ([tool_use("Skill", skill="helper-skill")], 0),
    ([tool_use("mcp__docs__search", query="x"), tool_use("Skill", skill="other-skill")], 0),
], ids=["both", "mcp-only", "companion-only", "wrong-companion"])
def test_mold_gate_needs_both_calls_in_the_trace(tmp_path: Path, events: list[dict[str, object]], expected: int) -> None:
    contract = load_contract(stage("mold-journey", tmp_path / "target"))
    assert gate(contract, tmp_path, events) == expected


# Journey budget

def test_journey_budget_ceiling_stops_at_1000_calls_and_the_standard_cap_stays_200() -> None:
    assert (JOURNEY_CALLS, JOURNEY_SECONDS) == (1000, 28800)
    budget = Budget(JOURNEY_CALLS, JOURNEY_SECONDS, 0, approved=True, journey=True)
    for _ in range(JOURNEY_CALLS):
        budget.claim()
    with pytest.raises(BudgetExhausted) as stopped:
        budget.claim()
    assert stopped.value.code == "budget-exhausted"
    with pytest.raises(ValueError, match="1000 calls"):
        _ = Budget(JOURNEY_CALLS + 1, JOURNEY_SECONDS, approved=True, journey=True)
    with pytest.raises(ValueError, match="200 calls"):
        _ = Budget(201, 7200, approved=True)
    with pytest.raises(BudgetUnapproved, match="cap of 1000 calls and 28800 seconds"):
        _ = approve(Estimate(1001, 600, True), 5000, reserve=6)
    with pytest.raises(BudgetUnapproved, match="cap of 200 calls and 7200 seconds"):
        _ = approve(Estimate(201, 600), 5000, reserve=6)
    largest = max(calls for calls in range(1, JOURNEY_CALLS + 1)
                  if estimate(search_calls=calls, journey=True).seconds <= JOURNEY_SECONDS)
    assert largest == 960 < JOURNEY_CALLS
    sized = estimate(search_calls=largest, journey=True)
    assert approve(sized, largest, reserve=6).maximum == largest
    with pytest.raises(BudgetUnapproved, match="the seconds cap binds first at about 960 calls"):
        _ = approve(estimate(search_calls=largest + 1, journey=True), 5000, reserve=6)


def test_journey_budget_ceiling_stops_at_28800_seconds() -> None:
    budget = approve(Estimate(500, 9000, True), 500, reserve=6)
    assert (budget.seconds, budget.journey) == (JOURNEY_SECONDS, True)
    with pytest.raises(ValueError, match="28800 seconds"):
        _ = Budget(10, JOURNEY_SECONDS + 1, approved=True, journey=True)
    with pytest.raises(ValueError, match="7200 seconds"):
        _ = Budget(10, JOURNEY_SECONDS, approved=True)
    with pytest.raises(BudgetUnapproved, match="cap of 1000 calls and 28800 seconds"):
        _ = approve(Estimate(10, JOURNEY_SECONDS + 1, True), 10, reserve=6)
    late = Budget(1000, JOURNEY_SECONDS, 0, started=time.monotonic() - JOURNEY_SECONDS - 1, approved=True, journey=True)
    with pytest.raises(BudgetExhausted) as stopped:
        late.claim()
    assert stopped.value.code == "budget-exhausted"
    inside = Budget(1000, JOURNEY_SECONDS, 0, started=time.monotonic() - 7201, approved=True, journey=True)
    inside.claim()


def journey_seed(target: Path) -> dict[str, object]:
    """Return the first journey case that the target's checked-in `evals/journeys.json` seeds."""
    seeded = seed_cases(target)
    assert seeded is not None
    return seeded[0]


def write_journeys(target: Path, out: Path, max_turns: int, families: int = 6) -> None:
    brief = journey_seed(target)
    cases = [brief | {"id": f"j{family}-{index}", "family": f"f{family}", "max_turns": max_turns,
                      "request": f"/culture request-{family}-{index}"}
             for family in range(families) for index in range(2)]
    cases[0] |= {"id": brief["id"], "request": brief["request"]}
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = (out / DRAFT_NAME).write_text(json.dumps(cases), encoding="utf-8")


@final
class Provider(FakeRunProvider):
    """A fake provider that claims the budget of a journey like the real harness. `improved` text scores best."""

    def __init__(self, contract: Contract, budget: Budget, checkpoint: Callable[[], None], prompts: list[str]) -> None:
        super().__init__()
        self.contract = contract
        self.budget = budget
        self.checkpoint = checkpoint
        self.prompts = prompts
        self.lock = threading.Lock()

    @override
    def preflight(self, recorded: dict[str, object] | None = None) -> dict[str, object]:
        del recorded
        if self.budget.calls == 0:
            for _ in range(PREFLIGHT_CALLS):
                self.budget.claim()
        return {"isolation": "test-provider"}

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: object = None) -> dict[str, object]:
        del checks
        assert case.journey is not None
        for _ in range(self.contract.calls(case.kind, case.journey.max_turns)):
            self.budget.claim(holdout=holdout)
        self.checkpoint()
        improved = "improved" in candidate.files["SKILL.md"]
        score = 0.9 if improved else 0.3
        return {"score": score, "scores": {"command": 1.0, "judge": score}, "candidate_hash": candidate.identity,
                "case_hash": case.identifier}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del candidate, case
        assert schema is not None
        self.budget.claim(holdout=holdout)
        with self.lock:
            self.prompts.append(prompt)
        return {"answer": {name: "improved " for name in cast(list[str], schema["required"])}}


def start(tmp_path: Path, max_turns: int) -> tuple[Path, Path, str, Callable[..., Provider], list[str]]:
    target, out = stage("culture-journey", tmp_path / "target"), tmp_path / "run"
    write_journeys(target, out, max_turns)
    contract = load_contract(target)
    prompts: list[str] = []

    def factory(_model: str, budget: Budget, checkpoint: Callable[[], None]) -> Provider:
        return Provider(contract, budget, checkpoint, prompts)

    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, live=True)
    assert cases.value.code == "cases-unapproved"
    return target, out, cast(str, cases.value.data["case_hash"]), factory, prompts


def test_journey_budget_estimate_shows_a_separate_journey_line_and_asks_once(tmp_path: Path) -> None:
    target, out, approved, factory, _ = start(tmp_path, 6)
    with pytest.raises(Stop) as asked:
        _ = run(target, out, MODEL, live=True, approve_cases=approved)
    assert asked.value.code == "budget-unapproved"
    estimate = cast(dict[str, object], asked.value.data["estimate"])
    line = cast(dict[str, int], estimate["journey"])
    assert 200 < cast(int, estimate["calls"]) <= JOURNEY_CALLS
    assert line == {"calls": estimate["calls"], "seconds": estimate["seconds"], "ceiling_calls": JOURNEY_CALLS,
                    "ceiling_seconds": JOURNEY_SECONDS}
    calls = cast(int, estimate["calls"])
    result = run(target, out, MODEL, live=True, approve_cases=approved, approve_budget=calls, factory=factory)
    assert result["phase"] == "complete"
    record = read(out / "run.json")
    budget = cast(dict[str, object], record["budget"])
    assert (budget["maximum"], budget["seconds"], budget["journey"]) == (calls, JOURNEY_SECONDS, True)
    assert cast(int, record["calls"]) <= calls


def test_journey_budget_run_stops_before_any_call_when_the_estimate_exceeds_the_journey_ceiling(tmp_path: Path) -> None:
    target, out, approved, _, _ = start(tmp_path, 50)
    with pytest.raises(Stop) as asked:
        _ = run(target, out, MODEL, live=True, approve_cases=approved, approve_budget=5000)
    assert asked.value.code == "budget-unapproved" and "the cap is 1000" in str(asked.value)


def test_journey_budget_single_turn_run_keeps_the_200_call_cap(tmp_path: Path, make_target: Callable[..., Path],
                                                         write_draft: Callable[..., list[str]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    with pytest.raises(Stop) as cases_stop:
        _ = run(target, out, MODEL, live=True)
    approved = cast(str, cases_stop.value.data["case_hash"])
    with pytest.raises(Stop) as asked:
        _ = run(target, out, MODEL, live=True, approve_cases=approved)
    estimate = cast(dict[str, object], asked.value.data["estimate"])
    assert "journey" not in estimate and cast(int, estimate["calls"]) <= 200
    with pytest.raises(BudgetUnapproved, match="cap of 200 calls and 7200 seconds"):
        _ = approve(Estimate(cast(int, estimate["calls"]) + 200, 60), 5000, reserve=6)


def test_journey_scored_in_validation_minibatches_and_the_holdout_gate_with_three_repeats(tmp_path: Path) -> None:
    target, out, approved, factory, prompts = start(tmp_path, 2)
    with pytest.raises(Stop) as asked:
        _ = run(target, out, MODEL, live=True, approve_cases=approved)
    calls = cast(int, cast(dict[str, object], asked.value.data["estimate"])["calls"])
    result = run(target, out, MODEL, live=True, approve_cases=approved, approve_budget=calls, factory=factory)
    assert result["phase"] == "complete"
    record = read(out / "run.json")
    cases = cast(list[dict[str, object]], read(out / "cases.json")["cases"])
    assert {case["kind"] for case in cases} == {"journey"}
    by_split = {split: [str(case["id"]) for case in cases if case["split"] == split] for split in ("train", "validation", "holdout")}
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    searched = {(item["split"], item["case_id"]) for item in outcomes if item["arm"] == "search" and "case_id" in item}
    assert {split for split, _ in searched} >= {"train", "validation"}
    assert {name for split, name in searched if split == "validation"} == set(by_split["validation"])
    holdout = [item for item in outcomes if item["split"] == "holdout"]
    for arm in ("baseline", "winner"):
        for name in by_split["holdout"]:
            assert sorted(cast(int, item["repeat"]) for item in holdout if item["arm"] == arm and item["case_id"] == name) == [0, 1, 2]
    gate_record = cast(dict[str, object], record["gate"])
    assert gate_record["repeats"] == 3 and gate_record["cases"] == len(by_split["holdout"])
    assert gate_record["verdict"] == "promote" and cast(float, gate_record["delta"]) > 0
    assert any('"judge": 0.3' in prompt for prompt in prompts)


def _mixed_case(split: Split, name: str, journey: bool) -> Case:
    return Case(name, name, split, "/culture request", {"notes.txt": "x\n"}, {}, "skill", True,
                kind="journey" if journey else "task",
                journey=Journey("brief", max_turns=8) if journey else None)


def _mixed_contract(tmp_path: Path) -> Contract:
    target = stage("culture-journey", tmp_path / "target")
    manifest = cast(dict[str, object], json.loads((target / "evals/autoimprove.json").read_text()))
    cast(dict[str, object], manifest["kinds"])["task"] = {"grader": "exact-json"}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(manifest))
    return load_contract(target)


def test_a_mixed_task_and_journey_plan_sizes_the_search_from_the_mean_case_cost_not_the_journey_maximum(
        tmp_path: Path) -> None:
    contract = _mixed_contract(tmp_path)
    journey_cost, task_cost = contract.calls("journey", 8), contract.calls("task")
    assert (task_cost, journey_cost) == (1, 17)
    cases = [_mixed_case(split, f"{split}-{n}", journey=n == 0 and split != "train")
             for split in ("train", "validation", "holdout") for n in range(6)]
    plan = size_plan(contract, cases, 1, ClaudeOptions())
    # Each validation case counts once per repeat in the mean (gh-500).
    searched = [task_cost] * 6 + VALIDATION_REPEATS * ([journey_cost] + [task_cost] * 5)
    mean = -(-sum(searched) // len(searched))
    holdout = [journey_cost] + [task_cost] * 5
    retry = 2 * HOLDOUT_RETRIES_PER_ARM * max(holdout)
    assert plan.retry_calls == retry and plan.holdout_calls == 2 * sum(holdout) + retry
    assert plan.sized.journey
    sandbox = command_seconds(contract, cases, ClaudeOptions())
    at_mean = plan_run(6, 6, 6, mean, 1, sandbox, journey=True, holdout_costs=holdout, headroom_costs=[task_cost] * 6)
    assert plan == at_mean and 1 < mean < journey_cost
    # At the journey maximum, the two runs of each validation case leave no search room at all.
    with pytest.raises(Stop, match="too few calls are left for a search"):
        _ = plan_run(6, 6, 6, journey_cost, 1, sandbox, journey=True)


def test_at_three_repeats_the_mean_cost_plan_fits_while_the_journey_maximum_plan_has_too_few_calls(
        tmp_path: Path) -> None:
    contract = _mixed_contract(tmp_path)
    journey_cost, task_cost = contract.calls("journey", 8), contract.calls("task")
    cases = [_mixed_case(split, f"{split}-{n}", journey=n == 0 and split != "train")
             for split in ("train", "validation", "holdout") for n in range(6)]
    searched = [task_cost] * 6 + [journey_cost] + [task_cost] * 5
    mean = -(-sum(searched) // len(searched))
    holdout = [journey_cost] + [task_cost] * 5
    sandbox = command_seconds(contract, cases, ClaudeOptions())
    plan = size_plan(contract, cases, 3, ClaudeOptions())
    assert plan == plan_run(6, 6, 6, mean, 3, sandbox, journey=True, holdout_costs=holdout, headroom_costs=[task_cost] * 6)
    with pytest.raises(Stop, match="too few calls"):
        _ = plan_run(6, 6, 6, journey_cost, 3, sandbox, journey=True)


def test_the_mixed_holdout_reserve_covers_one_failed_attempt_of_the_costliest_case(tmp_path: Path) -> None:
    contract = _mixed_contract(tmp_path)
    journey_cost = contract.calls("journey", 8)
    cases = [_mixed_case(split, f"{split}-{n}", journey=n == 0 and split != "train")
             for split in ("train", "validation", "holdout") for n in range(6)]
    plan = size_plan(contract, cases, 3, ClaudeOptions())
    gate_pass = 2 * 3 * (journey_cost + 5)
    assert plan.holdout_calls - gate_pass >= 2 * HOLDOUT_RETRIES_PER_ARM * journey_cost


def test_the_plan_stops_name_the_seconds_cap_only_for_a_journey_run(tmp_path: Path) -> None:
    contract = _mixed_contract(tmp_path)
    note = "the seconds cap binds first at about 960 calls"
    journeys = [_mixed_case(split, f"{split}-{n}", journey=True) for split in ("train", "validation", "holdout") for n in range(6)]
    tasks = [_mixed_case(split, f"{split}-{n}", journey=False) for split in ("train", "validation", "holdout") for n in range(6)]
    with pytest.raises(Stop, match=note):
        _ = size_plan(contract, journeys, 1000, ClaudeOptions())
    with pytest.raises(Stop) as plain:
        _ = size_plan(contract, tasks, 1000, ClaudeOptions())
    assert "seconds cap binds" not in str(plain.value)


def test_a_single_turn_only_plan_sizes_like_the_flat_per_evaluation_plan(tmp_path: Path) -> None:
    contract = _mixed_contract(tmp_path)
    cases = [_mixed_case(split, f"{split}-{n}", journey=False)
             for split in ("train", "validation", "holdout") for n in range(6)]
    assert size_plan(contract, cases, 3, ClaudeOptions()) == plan_run(6, 6, 6, 1, 3)


@needs_bwrap
def test_a_mold_journey_run_launches_the_task_with_the_surface_and_stages_the_companion(tmp_path: Path) -> None:
    """Drive `run` with the real `Configuration` and the stub `claude`, so a dropped surface or seed file shows in the argv.

    The live preflight sends its isolation probe through the real OS sandbox, so the test needs a working `bwrap`.
    """
    target, out = stage("mold-journey", tmp_path / "target"), tmp_path / "run"
    brief = journey_seed(target)
    drafted = [brief | {"id": f"m{family}-{index}", "family": f"f{family}", "request": f"/mold shape {family}-{index}"}
               for family in range(6) for index in range(2)]
    drafted[0] |= {"id": brief["id"], "request": brief["request"]}
    out.mkdir(mode=0o700)
    _ = (out / DRAFT_NAME).write_text(json.dumps(drafted), encoding="utf-8")
    executable, configuration = install_fake(tmp_path, "journey-mold")
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, live=True, configuration=configuration)
    approved = cast(str, cases.value.data["case_hash"])
    with pytest.raises(Stop) as asked:
        _ = run(target, out, MODEL, live=True, approve_cases=approved, configuration=configuration)
    calls = cast(int, cast(dict[str, object], asked.value.data["estimate"])["calls"])
    _ = run(target, out, MODEL, live=True, approve_cases=approved, approve_budget=calls, configuration=configuration)
    logged = [cast(dict[str, object], json.loads(line)) for line in executable.with_name("claude.log").read_text().splitlines()]
    streams = [item for item in logged if "--input-format" in cast(list[str], item["argv"])
               and "--model" in cast(list[str], item["argv"])]
    assert streams, "the run never launched a task"
    for item in streams:
        argv = cast(list[str], item["argv"])
        assert "--strict-mcp-config" in argv and "--mcp-config" in argv
        assert argv[argv.index("--tools") + 1].endswith("mcp__docs__search")
        staged = cast(dict[str, str], item["staged"])
        assert any(name.endswith("helper-skill/SKILL.md") for name in staged), staged

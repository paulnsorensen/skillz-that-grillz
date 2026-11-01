from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path
from typing_extensions import override

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._budget import (
    APPROVED_SECONDS,
    ESTIMATE_SECONDS_PER_CALL,
    MAX_CONCURRENT_CALLS,
    SECONDS_PER_CALL,
    Budget,
    BudgetExhausted,
    BudgetUnapproved,
    Estimate,
    approve,
    estimate,
    gate_seconds,
)
from skillz_experiments._runtime import process
from skillz_experiments.run import Stop
from skillz_experiments.run._plan import _plan as plan_run  # pyright: ignore[reportPrivateUsage]

CHILD = ("import sys,time,pathlib; d=pathlib.Path(sys.argv[1]); n=sys.argv[2]; "
         + "(d/(n+'.start')).write_text(str(time.time())); time.sleep(0.3); "
         + "(d/(n+'.end')).write_text(str(time.time()))")


@pytest.mark.ac("skillz-pragmatic-autoimprove:AC-15")
@pytest.mark.ac("gh-571-calibrate:AC-1")
def test_estimate_and_single_approval_gate_the_opt_in_ceiling() -> None:
    sized = estimate(preflight_calls=2, train=4, validation=3, holdout=3, calls_per_evaluation=2, repeats=3,
                     search_calls=5, reflection_calls=4, retry_calls=1)
    assert sized == Estimate(2 + 10 * 2 * 3 + 5 + 4 + 1, math.ceil(72 * ESTIMATE_SECONDS_PER_CALL / MAX_CONCURRENT_CALLS))
    assert sized.calls == 72 and sized.seconds == 1080

    for approval in (None, 71):
        with pytest.raises(BudgetUnapproved) as refused:
            _ = approve(sized, approval, reserve=6)
        assert refused.value.code == "budget-unapproved"
    assert isinstance(BudgetUnapproved("x"), CodedError)

    budget = approve(sized, 72, reserve=6)
    assert (budget.maximum, budget.seconds, budget.reserve, budget.approved) == (72, 7200, 6, True)
    assert approve(Estimate(3, 90), 3, reserve=6).reserve == 3
    assert Budget(40, 2400).maximum == 40
    with pytest.raises(ValueError, match="40 calls"):
        _ = Budget(41, 2400)
    with pytest.raises(ValueError, match="2400 seconds"):
        _ = Budget(10, 2401)
    with pytest.raises(ValueError, match="200 calls"):
        _ = Budget(201, 7200, approved=True)
    with pytest.raises(ValueError, match="7200 seconds"):
        _ = Budget(10, 7201, approved=True)

    top = approve(estimate(search_calls=200), 200, reserve=6)
    assert top.maximum == 200
    for too_big in (estimate(search_calls=201), Estimate(10, 7201)):
        with pytest.raises(BudgetUnapproved, match="cap of 200 calls and 7200 seconds"):
            _ = approve(too_big, 999, reserve=6)


def test_approve_refuses_an_estimate_with_no_calls() -> None:
    with pytest.raises(ValueError, match="at least 1 call"):
        _ = approve(Estimate(0, 0), 0, reserve=6)


@pytest.mark.parametrize(
    "sizes",
    [{"preflight_calls": -1}, {"train": -5, "validation": 10}, {"holdout": -1}, {"search_calls": -1},
     {"reflection_calls": -1}, {"retry_calls": -1}, {"calls_per_evaluation": 0}, {"repeats": 0}],
)
def test_estimate_refuses_counts_that_lower_the_total(sizes: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="nonnegative counts"):
        _ = estimate(**sizes, journey=False)


@pytest.mark.ac("skillz-pragmatic-autoimprove:AC-16")
def test_process_runs_at_most_two_children_at_once(tmp_path: Path) -> None:
    errors: list[BaseException] = []

    def run(name: str) -> None:
        try:
            _ = process([sys.executable, "-c", CHILD, str(tmp_path), name], cwd=tmp_path, timeout=30,
                        environment={"PATH": os.defpath})
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=run, args=(f"c{index}",)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []

    events = sorted([(float(path.read_text()), 1 if path.suffix == ".start" else -1)
                     for path in tmp_path.iterdir() if path.suffix in {".start", ".end"}],
                    key=lambda event: (event[0], event[1]))
    assert len(events) == 12
    running = peak = 0
    for _, step in events:
        running += step
        peak = max(peak, running)
    assert peak == MAX_CONCURRENT_CALLS


class _YieldingBudget(Budget):
    """Yield the GIL between the limit check and the increment to expose an unlocked claim."""

    yields: int = 0

    @override
    def check(self, count: int, *, holdout: bool = False) -> None:
        super().check(count, holdout=holdout)
        self.yields += 1
        time.sleep(0.001)


def test_concurrent_claims_never_overspend() -> None:
    budget = _YieldingBudget(40, 2400, reserve=0)
    refused: list[BudgetExhausted] = []

    def spend() -> None:
        for _ in range(10):
            try:
                budget.claim()
            except BudgetExhausted as error:
                refused.append(error)

    workers = [threading.Thread(target=spend) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert budget.calls == 40
    assert len(refused) == 40
    # The race window exists only when `claim` runs the overridden `check`.
    assert budget.yields >= 40


def test_slot_wait_counts_against_the_call_timeout(tmp_path: Path) -> None:
    hold = "import pathlib,sys,time;pathlib.Path(sys.argv[1]).touch();time.sleep(2.0)"
    markers = [tmp_path / f"holding-{index}" for index in range(MAX_CONCURRENT_CALLS)]
    holders = [threading.Thread(target=process, args=([sys.executable, "-c", hold, str(marker)],),
                                kwargs={"cwd": tmp_path, "timeout": 30, "environment": {"PATH": os.defpath}})
               for marker in markers]
    for holder in holders:
        holder.start()
    # A marker exists only after its child runs, so its holder occupies a slot.
    deadline = time.monotonic() + 10
    while not all(marker.exists() for marker in markers):
        assert time.monotonic() < deadline, "holders did not occupy every call slot"
        time.sleep(0.01)
    began = time.monotonic()
    with pytest.raises(BudgetExhausted, match="call slot"):
        _ = process([sys.executable, "-c", "pass"], cwd=tmp_path, timeout=0.4,
                    environment={"PATH": os.defpath})
    assert time.monotonic() - began < 1.0

    late = time.monotonic()
    with pytest.raises(BudgetExhausted, match="terminated"):
        _ = process([sys.executable, "-c", "import time;time.sleep(30)"], cwd=tmp_path, timeout=2.5,
                    environment={"PATH": os.defpath})
    # Holders free the slots at most 1.6 s after `late`. Without the slot-wait
    # subtraction, the call ends near 1.6 + 2.5 = 4.1 s.
    assert time.monotonic() - late < 3.2
    for holder in holders:
        holder.join()


def test_the_time_reserve_holds_the_end_of_the_deadline_for_the_gate() -> None:
    budget = Budget(10, 100, 0, reserve_seconds=90)
    assert 9 < budget.remaining() <= 10
    budget.claim()
    budget.started -= 10.5
    with pytest.raises(BudgetExhausted, match="holdout gate holds"):
        budget.claim()
    with pytest.raises(BudgetExhausted, match="holdout gate holds"):
        budget.claim(holdout=True)
    budget.reserve_time(0)
    assert 89 < budget.remaining() <= 90.5
    budget.claim(holdout=True)
    assert budget.calls == 2
    budget.started -= 100
    with pytest.raises(BudgetExhausted, match="global deadline"):
        _ = budget.remaining()


@pytest.mark.ac("gh-571-calibrate:AC-2")
def test_approve_sizes_the_time_reserve_from_the_gate_calls() -> None:
    assert gate_seconds(38) == math.ceil(38 * SECONDS_PER_CALL / MAX_CONCURRENT_CALLS)
    assert gate_seconds(0) == 0
    sized = estimate(preflight_calls=3, holdout=12, repeats=3, search_calls=40)
    budget = approve(sized, sized.calls, reserve=38, reserve_time=gate_seconds(38))
    assert budget.reserve_seconds == gate_seconds(38)
    # The seconds cap bounds the reserve, not the estimate: the gate charges the slow-call rate (gh-571).
    assert approve(Estimate(2, 60), 2, reserve=2, reserve_time=600).reserve_seconds == 600
    assert approve(Estimate(2, 60), 2, reserve=2, reserve_time=APPROVED_SECONDS + 1).reserve_seconds == APPROVED_SECONDS
    with pytest.raises(ValueError, match="time reservation"):
        _ = Budget(10, 100, 0, reserve_seconds=-1)
    with pytest.raises(ValueError, match="time reservation"):
        budget.reserve_time(-1)


@pytest.mark.ac("gh-571-calibrate:AC-2")
def test_the_gate_reserve_charges_the_slow_call_rate_while_the_estimate_charges_the_plan_rate() -> None:
    assert (SECONDS_PER_CALL, ESTIMATE_SECONDS_PER_CALL) == (60, 30)
    for calls in (1, 38, 101):
        assert gate_seconds(calls) == math.ceil(calls * 60 / MAX_CONCURRENT_CALLS)
        assert estimate(search_calls=calls).seconds == math.ceil(calls * 30 / MAX_CONCURRENT_CALLS)
    assert gate_seconds(38) == 1140 and estimate(search_calls=38).seconds == 570


@pytest.mark.ac("gh-571-calibrate:AC-2")
def test_a_gate_heavy_plan_reserve_is_not_cut_to_the_estimate_seconds() -> None:
    # 3 train, 3 validation, and 30 holdout cases: the gate calls cost more at 60 seconds each than the whole plan at 30.
    plan = plan_run(3, 3, 30, 1, 1, 20)
    assert plan.sandbox_gate == 920 and plan.reserve == 92
    assert plan.reserve_seconds == gate_seconds(plan.reserve) + plan.sandbox_gate == 3680
    assert plan.reserve_seconds > plan.sized.seconds == 3500
    budget = approve(plan.sized, plan.sized.calls, reserve=plan.reserve, reserve_time=plan.reserve_seconds)
    assert budget.reserve_seconds == 3680 <= budget.seconds


@pytest.mark.ac("gh-571-calibrate:AC-2")
@pytest.mark.parametrize(("train", "validation", "holdout", "sandbox"),
                         [(1, 1, 58, 20), (3, 3, 30, 20), (3, 3, 30, 80), (3, 3, 30, 100), (6, 6, 6, 20), (1, 1, 30, 100)])
def test_a_plan_that_fits_leaves_the_search_its_time_after_the_slow_rate_gate_reserve(
        train: int, validation: int, holdout: int, sandbox: int) -> None:
    try:
        plan = plan_run(train, validation, holdout, 1, 1, sandbox)
    except Stop:
        return
    # The plan-rate time of the non-gate work: everything the estimate holds except the gate calls and their sandbox.
    search_share = (plan.sized.seconds - estimate(search_calls=plan.reserve).seconds - plan.sandbox_gate)
    assert search_share > 0
    assert search_share + plan.reserve_seconds <= APPROVED_SECONDS


@pytest.mark.ac("gh-571-calibrate:AC-2")
def test_a_gate_reserve_that_leaves_no_search_time_stops_at_planning() -> None:
    # 30 holdout cases with 100 sandbox seconds: the slow-rate reserve alone passes the 7200-second cap.
    with pytest.raises(Stop, match="seconds; the cap is 7200"):
        _ = plan_run(1, 1, 30, 1, 1, 100)

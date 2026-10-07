from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._runtime import (
    MAX_CONCURRENT_CALLS,
    SECONDS_PER_CALL,
    Budget,
    BudgetExhausted,
    BudgetUnapproved,
    Estimate,
    approve,
    estimate,
    process,
)

CHILD = ("import sys,time,pathlib; d=pathlib.Path(sys.argv[1]); n=sys.argv[2]; "
         + "(d/(n+'.start')).write_text(str(time.time())); time.sleep(0.3); "
         + "(d/(n+'.end')).write_text(str(time.time()))")


def test_estimate_and_single_approval_gate_the_opt_in_ceiling() -> None:
    sized = estimate(preflight_calls=2, train=4, validation=3, holdout=3, calls_per_evaluation=2, repeats=3,
                     search_calls=5, reflection_calls=4)
    assert sized == Estimate(2 + 10 * 2 * 3 + 5 + 4, math.ceil(71 * SECONDS_PER_CALL / MAX_CONCURRENT_CALLS))
    assert sized.calls == 71 and sized.seconds == 2130

    for approval in (None, 70):
        with pytest.raises(BudgetUnapproved) as refused:
            _ = approve(sized, approval, reserve=6)
        assert refused.value.code == "budget-unapproved"
    assert isinstance(BudgetUnapproved("x"), CodedError)

    budget = approve(sized, 71, reserve=6)
    assert (budget.maximum, budget.seconds, budget.approved) == (71, 7200, True)
    with pytest.raises(ValueError, match="40 calls"):
        _ = Budget(71, 2400)
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


def test_concurrent_claims_count_exactly() -> None:
    budget = Budget(40, 2400, reserve=0)
    workers = [threading.Thread(target=lambda: [budget.claim() for _ in range(10)]) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert budget.calls == 40


def test_slot_wait_counts_against_the_call_timeout(tmp_path: Path) -> None:
    holders = [threading.Thread(target=process, args=([sys.executable, "-c", "import time;time.sleep(1.5)"],),
                                kwargs={"cwd": tmp_path, "timeout": 30, "environment": {"PATH": os.defpath}})
               for _ in range(MAX_CONCURRENT_CALLS)]
    for holder in holders:
        holder.start()
    time.sleep(0.3)
    began = time.monotonic()
    with pytest.raises(BudgetExhausted, match="call slot"):
        _ = process([sys.executable, "-c", "pass"], cwd=tmp_path, timeout=0.4,
                    environment={"PATH": os.defpath})
    assert time.monotonic() - began < 1.0

    late = time.monotonic()
    with pytest.raises(BudgetExhausted, match="terminated"):
        _ = process([sys.executable, "-c", "import time;time.sleep(30)"], cwd=tmp_path, timeout=2.5,
                    environment={"PATH": os.defpath})
    assert time.monotonic() - late < 2.8
    for holder in holders:
        holder.join()

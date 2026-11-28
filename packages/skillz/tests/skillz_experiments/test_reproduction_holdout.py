"""gh-651: measure holdout regression cases as seed slots without a separate reproduction charge."""
# pyright: reportAny=false
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from skillz_experiments.run import Stop, _session, run

from _reproduction_runs import Approver, Drafter, Maker, go, measure, record, reproduction, seed_zero, splits, start
from test_run_search import MODEL

pytestmark = pytest.mark.usefixtures("host_login")


@pytest.mark.ac("gh-651:AC-4")
@pytest.mark.ac("gh-651:AC-9")
def test_holdout_regression_reuses_the_two_seed_slots_before_search_starts(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, approved, calls = start(tmp_path, make_target, write_draft, approvals)
    seed_zero(state)
    before_engine: list[int] = []
    real = _session.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportExplicitAny]
        before_engine.append(state.holdout_calls)
        return real(*args, **kwargs)

    monkeypatch.setattr(_session, "pareto_search", spy)
    result = go(state, target, out, approved, calls)
    holdout = splits(out, "holdout")
    assert result["phase"] == "complete" and holdout
    assert before_engine == [2 * len(holdout)]
    outcomes = cast(list[dict[str, Any]], record(out)["outcomes"])  # pyright: ignore[reportExplicitAny]
    assert not any(item.get("arm") == "reproduce" for item in outcomes)
    measured = [item for item in outcomes if item.get("arm") == "baseline" and item.get("repeat") in (0, 1)
                and item.get("split") == "holdout"]
    assert Counter(item["case_id"] for item in measured) == Counter(dict.fromkeys(holdout, 2))
    assert not any("search_candidate" in item for item in measured)
    found = {name: item for name, item in reproduction(out).items() if item["split"] == "holdout"}
    assert found.keys() == holdout.keys() and all(item["scores"] == [0.0, 0.0] for item in found.values())
    assert result["headroom"] == record(out)["headroom"]
    assert Counter(item["case_id"] for item in outcomes if item.get("arm") == "winner") == Counter(dict.fromkeys(holdout, 3))


@pytest.mark.ac("gh-651:AC-6")
def test_holdout_regression_cases_do_not_add_a_separate_charge_for_existing_seed_slots(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    plain = start(tmp_path / "plain", make_target, write_draft, approvals, regression=lambda _case: False)
    marked = start(tmp_path / "marked", make_target, write_draft, approvals)
    estimates: list[Mapping[str, object]] = []
    for target, out, state, approved, calls in (plain, marked):
        seed_zero(state)
        assert measure(state, target, out, approved, calls)["search"] == "not-run"
        holdout = splits(out, "holdout")
        assert holdout and state.holdout_calls == 2 * len(holdout)
        with pytest.raises(Stop) as budget:
            _ = run(target, out, MODEL, search=True)
        assert budget.value.code == "budget-unapproved"
        estimates.append(cast(Mapping[str, object], budget.value.data["estimate"]))
    assert estimates[0] == estimates[1] and "reproduce" not in estimates[1]
    assert marked[4] == plain[4]

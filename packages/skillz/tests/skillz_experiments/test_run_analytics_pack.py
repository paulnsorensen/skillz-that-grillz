"""The run-analytics pack: its SQL runs with the DuckDB CLI over fixture `run.json` and `cases.json` files."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TypeAlias, cast

import pytest

PACK = Path(__file__).resolve().parents[4] / "skills/skillz/references/run-analytics.md"
QUERY = re.compile(r"^### `([a-z-]+)`$.*?^```sql\n(.*?)^```$", re.M | re.S)
WRITES = re.compile(r"\b(COPY|CREATE|INSERT|UPDATE|DELETE|EXPORT|ATTACH|INSTALL|LOAD|PRAGMA|SET)\b", re.I)
NAMES = ("run-status", "role-usage", "score-by-arm", "case-scores", "unmet-criteria", "dealbreakers", "reflections")

REQUIRE_DUCKDB = os.environ.get("REQUIRE_DUCKDB") == "1"
needs_duckdb = pytest.mark.skipif(not (REQUIRE_DUCKDB or shutil.which("duckdb")), reason="DuckDB CLI unavailable")

RowValue: TypeAlias = str | int | float | bool | None | list[str]
Row: TypeAlias = dict[str, RowValue]


def _usage(inputs: int, cached: int, output: int) -> dict[str, int]:
    return {"input_tokens": inputs, "cached_input_tokens": cached, "output_tokens": output}


def _verdict(level: int, reason: str) -> dict[str, object]:
    return {"level": level, "met": level >= 3, "reason": reason}


def _outcome(case: str, split: str, arm: str, candidate: str, score: float | None,
             criteria: Mapping[str, object] | None, repeat: int = 1) -> dict[str, object]:
    return {"case_id": case, "split": split, "arm": arm, "search_candidate": candidate, "candidate_hash": candidate,
            "repeat": repeat, "score": score, "criteria": criteria, "latency_seconds": 10.0 * repeat,
            "judge_latency_seconds": 2.0, "task_usage": _usage(100, 50, 10), "judge_usage": _usage(20, 0, 5),
            "transcript": [{"role": "user", "text": "private"}]}


def _running() -> dict[str, object]:
    press_bad = {"press-inline": _verdict(1, "no Press round"), "slug-intact": _verdict(2, "changed slugify")}
    press_worse = {"press-inline": _verdict(2, "still no Press round"), "slug-intact": _verdict(4, "intact")}
    fault = _outcome("scope-1", "train", "search", "c1" * 32, None, None) | {
        "status": "provider-fault", "judge_usage": None, "judge_latency_seconds": None}
    reflection = {"split": "reflection", "arm": "search", "latency_seconds": 30.0, "usage": _usage(300, 0, 60),
                  "hypothesis": "Run Press inline", "notes": "Keep slugify", "parent": "c0" * 32,
                  "proposed": "c1" * 32, "changed": ["SKILL.md"], "over_budget": False, "rejected": None}
    return {
        "phase": "searched", "run_id": "run-a", "calls": 9, "calls_by_role": {"task": 5, "judge": 3, "reflection": 1},
        "seconds_by_role": {"task": 40.0, "judge": 8.0, "reflection": 30.0},
        "budget": {"maximum": 20, "reserve": 5, "seconds": 600},
        "search": {"stop_trigger": "call-share", "reason": "validation-mean", "scored": 4},
        "outcomes": [
            _outcome("press-1", "train", "search", "c0" * 32, 0.0, press_bad),
            _outcome("press-1", "train", "search", "c0" * 32, 0.0, press_worse, repeat=2),
            _outcome("scope-1", "train", "search", "c0" * 32, 1.0, {"scoped": _verdict(4, "only spec files")}),
            _outcome("scope-1", "holdout", "baseline", "c0" * 32, 0.5, {"scoped": _verdict(2, "touched README")}),
            reflection,
            _outcome("press-1", "train", "search", "c1" * 32, 1.0, {"press-inline": _verdict(4, "inline round")}),
            fault,
        ],
    }


def _complete() -> dict[str, object]:
    return {"phase": "complete", "run_id": "run-b", "calls": 2, "calls_by_role": {"task": 1, "judge": 1},
            "seconds_by_role": {"task": 5.0, "judge": 1.5}, "budget": {"maximum": 10, "reserve": 2, "seconds": 60},
            "search": {"stop_trigger": "stall", "reason": "no-headroom", "scored": 1},
            "gate": {"verdict": "promote", "delta": 0.25, "se": 0.05},
            "outcomes": [_outcome("other-1", "holdout", "search", "d0" * 32, 0.75, {"x": _verdict(3, "fine")})]}


def _cases() -> dict[str, object]:
    criteria = [{"id": "press-inline", "from": "must:0", "text": "Run Press inline.", "must": None},
                {"id": "slug-intact", "from": "must_not:0", "text": "Do not change slugify.", "must": True}]
    return {"cases": [{"id": "press-1", "split": "train", "expected": {"criteria": criteria}},
                      {"id": "scope-1", "split": "train", "expected": {"criteria": [
                          {"id": "scoped", "text": "Stay in scope.", "must": True}]}}]}


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("runs")
    for name, record in (("a", _running()), ("b", _complete())):
        (root / name).mkdir()
        _ = (root / name / "run.json").write_text(json.dumps(record, indent=2))
    _ = (root / "a" / "cases.json").write_text(json.dumps(_cases()))
    return root


def _queries() -> dict[str, str]:
    return {match.group(1): match.group(2) for match in QUERY.finditer(PACK.read_text())}


def _query(name: str, run_json: Path | str, cases_json: Path | None = None) -> list[Row]:
    sql = _queries()[name].replace("{RUN_JSON}", str(run_json)).replace("{CASES_JSON}", str(cases_json))
    # Without AI_AGENT, DuckDB 2 prints plain JSON as DuckDB 1 does. The pack's command skips ~/.duckdbrc.
    env = {key: value for key, value in os.environ.items() if key != "AI_AGENT"}
    result = subprocess.run(["duckdb", "-init", "/dev/null", "-json", "-c", sql], capture_output=True, text=True,
                            check=False, env=env)
    assert result.returncode == 0, result.stderr
    return cast(list[Row], json.loads(result.stdout)) if result.stdout.strip() else []


def _short(row: Row, key: str = "run_file") -> str:
    return Path(cast(str, row[key])).parent.name


@pytest.mark.ac("run-observability:AC-5")
def test_pack_names_each_query_once_and_only_reads() -> None:
    queries = _queries()
    assert tuple(queries) == NAMES
    for name, sql in queries.items():
        assert "{RUN_JSON}" in sql, name
        assert WRITES.search(sql) is None, name
    assert "{CASES_JSON}" in queries["dealbreakers"]


@needs_duckdb
@pytest.mark.ac("run-observability:AC-3")
def test_run_status_reads_a_glob_of_runs_and_shows_budget_spent_against_the_cap(runs: Path) -> None:
    rows = {_short(row): row for row in _query("run-status", runs / "*" / "run.json")}
    assert set(rows) == {"a", "b"}
    running, complete = rows["a"], rows["b"]
    assert (running["phase"], running["calls"], running["call_cap"], running["calls_pct"]) == ("searched", 9, 20, 45.0)
    assert (running["seconds"], running["seconds_cap"]) == (78.0, 600)
    assert (running["stop_trigger"], running["search_reason"], running["gate_verdict"]) == (
        "call-share", "validation-mean", None)
    assert (running["outcomes"], running["reflections"]) == (6, 1)
    assert (complete["phase"], complete["gate_verdict"], complete["gate_delta"]) == ("complete", "promote", 0.25)


@needs_duckdb
@pytest.mark.ac("run-observability:AC-3")
def test_role_usage_sums_tokens_and_latency_per_role(runs: Path) -> None:
    rows = {cast(str, row["role"]): row for row in _query("role-usage", runs / "a" / "run.json")}
    assert set(rows) == {"task", "judge", "reflection"}
    task, judge, reflection = rows["task"], rows["judge"], rows["reflection"]
    assert (task["calls"], task["seconds"], task["usage_rows"], task["input_tokens"], task["cached_input_tokens"]) == (
        5, 40.0, 6, 600, 300)
    assert (task["output_tokens"], task["mean_latency_s"], task["max_latency_s"]) == (60, 11.7, 20.0)
    assert (judge["calls"], judge["usage_rows"], judge["input_tokens"], judge["mean_latency_s"]) == (3, 5, 100, 2.0)
    assert (reflection["calls"], reflection["input_tokens"], reflection["output_tokens"]) == (1, 300, 60)


@needs_duckdb
@pytest.mark.ac("run-observability:AC-2")
def test_score_by_arm_ranks_each_candidate_per_split(runs: Path) -> None:
    rows = _query("score-by-arm", runs / "a" / "run.json")
    found = [(row["split"], row["arm"], row["candidate"], row["cases"], row["runs"], row["scored"], row["mean_score"],
              row["zero_runs"]) for row in rows]
    assert found == [("holdout", "baseline", "c0c0c0c0c0c0", 1, 1, 1, 0.5, 0),
                     ("train", "search", "c1c1c1c1c1c1", 2, 2, 1, 1.0, 0),
                     ("train", "search", "c0c0c0c0c0c0", 2, 3, 3, 0.333, 2)]


@needs_duckdb
@pytest.mark.ac("run-observability:AC-1")
def test_case_scores_flag_cases_that_always_score_zero(runs: Path) -> None:
    rows = {(row["case_id"], row["split"]): row for row in _query("case-scores", runs / "a" / "run.json")}
    press = rows[("press-1", "train")]
    assert (press["runs"], press["scored"], press["zero_runs"], press["mean_score"]) == (3, 3, 2, 0.333)
    assert rows[("scope-1", "train")]["statuses"] == ["provider-fault"]
    assert list(rows)[0] == ("press-1", "train")


@needs_duckdb
@pytest.mark.ac("run-observability:AC-1")
def test_unmet_criteria_list_each_case_with_its_lowest_level_and_judge_reason(runs: Path) -> None:
    rows = _query("unmet-criteria", runs / "a" / "run.json")
    found = [(row["case_id"], row["criterion"], row["runs"], row["unmet_runs"], row["lowest_level"], row["reason"])
             for row in rows]
    assert found == [("press-1", "press-inline", 3, 2, 1, "no Press round"),
                     ("press-1", "slug-intact", 2, 1, 2, "changed slugify"),
                     ("scope-1", "scoped", 2, 1, 2, "touched README")]


@needs_duckdb
@pytest.mark.ac("run-observability:AC-1")
def test_dealbreakers_join_the_case_text_and_explain_a_zero_score(runs: Path) -> None:
    rows = _query("dealbreakers", runs / "a" / "run.json", runs / "a" / "cases.json")
    found = [(row["case_id"], row["criterion"], row["zeroed_runs"], row["text"]) for row in rows]
    assert found == [("press-1", "slug-intact", 1, "Do not change slugify."), ("scope-1", "scoped", 1, "Stay in scope.")]


@needs_duckdb
@pytest.mark.ac("run-observability:AC-4")
def test_reflections_show_each_hypothesis_in_order(runs: Path) -> None:
    rows = _query("reflections", runs / "*" / "run.json")
    assert [(_short(row), row["step"], row["hypothesis"], row["notes"], row["parent"], row["proposed"],
             row["changed"], row["over_budget"]) for row in rows] == [
        ("a", 1, "Run Press inline", "Keep slugify", "c0c0c0c0c0c0", "c1c1c1c1c1c1", 1, False)]

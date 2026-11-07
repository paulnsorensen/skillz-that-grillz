"""`skillz-experiment watch`: a read-only page on 127.0.0.1 over `<out>/run.json`, driven through the real CLI."""
from __future__ import annotations

import http.client
import json
import signal
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cli import main
from skillz_experiments._records import write

BUNDLE = Path(__file__).resolve().parents[4] / "skills/skillz/scripts/skillz-experiment.pyz"
PRIVATE = "private transcript text"
WINNER = "private winner file"
Json = dict[str, object]


@dataclass(frozen=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: str

    def data(self) -> Json:
        assert self.status == 200, self.body
        return cast(Json, json.loads(self.body))


@dataclass(frozen=True)
class Watcher:
    process: subprocess.Popen[str]
    port: int
    out: Path

    def get(self, path: str, host: str | None = None, method: str = "GET") -> Reply:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, headers={"Host": host or f"127.0.0.1:{self.port}"})
            response = connection.getresponse()
            return Reply(response.status, {key.lower(): value for key, value in response.getheaders()},
                         response.read().decode("utf-8"))
        finally:
            connection.close()

    def run(self) -> Json:
        return self.get("/api/run").data()


def _criteria(**levels: int) -> Json:
    return {name: {"level": level, "met": level >= 3, "reason": f"{name} at level {level}"} for name, level in levels.items()}


def _outcome(case: str, score: float, candidate: str, **levels: int) -> Json:
    return {"case_id": case, "split": "train", "arm": "search", "search_candidate": candidate * 32, "repeat": 1,
            "score": score, "criteria": _criteria(**levels), "latency_seconds": 12.5, "judge_latency_seconds": 2.0,
            "task_usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 7},
            "judge_usage": {"input_tokens": 30, "cached_input_tokens": 0, "output_tokens": 4},
            "transcript": [{"role": "user", "text": PRIVATE}]}


def _record(phase: str, outcomes: list[Json]) -> Json:
    return {"phase": phase, "run_id": "run-1", "model": "haiku", "calls": 2 * len(outcomes),
            "calls_by_role": {"task": len(outcomes), "judge": len(outcomes)},
            "seconds_by_role": {"task": 12.5 * len(outcomes), "judge": 2.0 * len(outcomes)},
            "budget": {"maximum": 40, "seconds": 900}, "search": {"stop_trigger": None}, "outcomes": outcomes,
            "winner": {"SKILL.md": WINNER}}


def _start(command: list[str], out: Path) -> Watcher:
    process = subprocess.Popen([*command, "watch", str(out), "--port", "0"], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    line: str = process.stdout.readline() if process.stdout is not None else ""
    if not line:
        _, error = process.communicate(timeout=10)
        pytest.fail(f"watch printed no URL: {error}")
    started = cast(dict[str, str], json.loads(line))
    assert started["run"] == str(out.resolve() / "run.json")
    prefix = "http://127.0.0.1:"
    assert started["url"].startswith(prefix) and started["url"].endswith("/"), started
    return Watcher(process, int(started["url"].removeprefix(prefix).rstrip("/")), out)


def _stop(watcher: Watcher) -> int:
    watcher.process.send_signal(signal.SIGINT)
    try:
        return watcher.process.wait(timeout=20)
    finally:
        if watcher.process.poll() is None:
            watcher.process.kill()


@pytest.fixture
def watcher(tmp_path: Path) -> Iterator[Watcher]:
    out = tmp_path / "out"
    out.mkdir()
    started = _start([sys.executable, "-m", "skillz_experiments"], out)
    try:
        yield started
    finally:
        _ = _stop(started)


@pytest.mark.ac("run-observability:AC-6")
@pytest.mark.ac("run-observability:AC-7")
def test_watch_shows_a_run_that_starts_and_grows_without_a_restart(watcher: Watcher) -> None:
    assert watcher.run()["state"] == "waiting"
    write(watcher.out / "run.json", _record("prepared", [_outcome("press-1", 0.0, "a", inline=1, intact=4)]))
    first = watcher.run()
    assert (first["state"], cast(Json, first["status"])["phase"], first["outcome_count"]) == ("ok", "prepared", 1)
    press = cast(list[Json], first["cases"])[0]
    assert (press["case_id"], press["zero_runs"], press["runs"]) == ("press-1", 1, 1)
    assert cast(list[Json], press["criteria"])[0] == {"id": "inline", "runs": 1, "unmet": 1, "lowest_level": 1.0,
                                                     "reason": "inline at level 1"}
    grown = _record("searched", [_outcome("press-1", 0.0, "a", inline=1, intact=4),
                                 _outcome("press-1", 1.0, "b", inline=4, intact=4),
                                 _outcome("scope-1", 0.5, "b", scoped=2)])
    grown["outcomes"] = [*cast(list[Json], grown["outcomes"]), {
        "split": "reflection", "arm": "search", "hypothesis": "Run Press inline", "notes": "Keep scope",
        "parent": "a" * 64, "proposed": "b" * 64, "changed": ["SKILL.md"], "latency_seconds": 30.0,
        "usage": {"input_tokens": 900, "cached_input_tokens": 0, "output_tokens": 80}}]
    grown["search"] = {"stop_trigger": "call-share", "reason": "validation-mean", "comparisons": [
        {"challenger": "b" * 64, "incumbent": "a" * 64, "verdict": "inconclusive", "delta": 0.5, "se": 0.4}]}
    write(watcher.out / "run.json", grown)
    second = watcher.run()
    status = cast(Json, second["status"])
    assert (status["phase"], status["calls"], status["call_cap"], status["seconds"], status["seconds_cap"]) == (
        "searched", 6, 40, 43.5, 900)
    assert (status["stop_trigger"], cast(list[Json], status["comparisons"])[0]["verdict"]) == ("call-share", "inconclusive")
    board = [(row["candidate"], row["runs"], row["mean"]) for row in cast(list[Json], second["leaderboard"])]
    assert board == [("b" * 12, 2, 0.75), ("a" * 12, 1, 0.0)]
    roles = {cast(str, row["role"]): row for row in cast(list[Json], second["roles"])}
    assert (roles["task"]["calls"], roles["task"]["input_tokens"], roles["reflection"]["output_tokens"]) == (3, 300, 80)
    reflection = cast(list[Json], second["reflections"])[0]
    assert (reflection["hypothesis"], reflection["proposed"], reflection["changed"]) == ("Run Press inline", "b" * 12, 1)
    newest = cast(list[Json], second["outcomes"])[0]
    assert (newest["index"], newest["case_id"], cast(list[Json], newest["criteria"])[0]["reason"]) == (
        3, "scope-1", "scoped at level 2")
    assert sorted(path.name for path in watcher.out.iterdir()) == ["run.json"]


@pytest.mark.ac("run-observability:AC-8")
def test_watch_serves_only_its_own_paths_to_its_own_host(watcher: Watcher) -> None:
    write(watcher.out / "run.json", _record("prepared", [_outcome("press-1", 0.0, "a", inline=1)]))
    _ = (watcher.out / "cases.json").write_text("{}")
    page = watcher.get("/")
    assert page.status == 200 and '<script src="/app.js"></script>' in page.body
    assert "script-src 'self'" in page.headers["content-security-policy"] and page.headers["cache-control"] == "no-store"
    assert "textContent" in watcher.get("/app.js").body and "innerHTML" not in watcher.get("/app.js").body
    assert watcher.get("/app.css").headers["content-type"] == "text/css; charset=utf-8"
    assert PRIVATE not in watcher.get("/api/run").body and WINNER not in watcher.get("/api/run").body
    assert [watcher.get(path).status for path in ("/run.json", "/cases.json", "/../run.json", "/api")] == [404] * 4
    assert watcher.get("/api/run", host="evil.example").status == 403
    assert watcher.get("/api/run", host=f"localhost:{watcher.port}").status == 200
    assert watcher.get("/api/run", method="POST").status == 501
    with pytest.raises(OSError):
        _ = socket.create_connection(("127.0.0.2", watcher.port), timeout=2).close()
    assert sorted(path.name for path in watcher.out.iterdir()) == ["cases.json", "run.json"]


@pytest.mark.ac("run-observability:AC-9")
def test_watch_reports_an_unreadable_record_and_recovers(watcher: Watcher) -> None:
    _ = (watcher.out / "run.json").write_text("{not json")
    broken = watcher.run()
    assert broken["state"] == "error" and "status" not in broken
    (watcher.out / "run.json").unlink()
    _ = (watcher.out / "elsewhere.json").write_text(json.dumps(_record("prepared", [])))
    (watcher.out / "run.json").symlink_to(watcher.out / "elsewhere.json")
    assert watcher.run() == {"path": str(watcher.out.resolve() / "run.json"), "state": "error", "error": "unsafe run record"}
    (watcher.out / "run.json").unlink()
    write(watcher.out / "run.json", _record("complete", []))
    assert cast(Json, watcher.run()["status"])["phase"] == "complete"


@pytest.mark.ac("run-observability:AC-6")
def test_watch_stops_on_an_interrupt_with_status_zero(tmp_path: Path) -> None:
    started = _start([sys.executable, "-m", "skillz_experiments"], tmp_path)
    assert started.run()["state"] == "waiting"
    assert _stop(started) == 0


@pytest.mark.ac("run-observability:AC-6")
def test_the_bundled_archive_serves_the_watch_page(tmp_path: Path) -> None:
    started = _start([sys.executable, str(BUNDLE)], tmp_path)
    try:
        assert started.run()["state"] == "waiting"
        assert started.get("/").status == 200
    finally:
        assert _stop(started) == 0


@pytest.mark.ac("run-observability:AC-6")
def test_watch_rejects_a_bad_or_busy_port(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["watch", str(tmp_path), "--port", "70000"]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "usage"
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        assert main(["watch", str(tmp_path), "--port", str(cast(tuple[str, int], busy.getsockname())[1])]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "watch-port"


@pytest.mark.ac("run-observability:AC-9")
def test_view_leaves_out_malformed_parts_of_a_record() -> None:
    from skillz_experiments.run._watch_view import view

    shown = view({"outcomes": ["x", {"split": "train", "criteria": {"a": "bad"}, "score": "high"}],
                  "search": {"comparisons": "none"}, "budget": [], "calls_by_role": {"task": 1}})
    assert shown["cases"] == [{"case_id": "?", "splits": ["train"], "statuses": [], "cases": 1, "runs": 1, "scored": 0,
                               "mean": None, "zero_runs": 0, "criteria": []}]
    assert cast(Json, shown["status"])["comparisons"] == [] and cast(Json, shown["status"])["call_cap"] is None
    assert shown["roles"] == [{"role": "task", "calls": 1.0, "seconds": None, "outcomes": 0, "input_tokens": 0,
                               "cached_input_tokens": 0, "output_tokens": 0, "mean_latency": None}]
    assert (shown["reflections"], shown["outcome_count"]) == ([], 1)

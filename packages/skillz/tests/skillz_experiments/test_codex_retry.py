"""The Codex adapter retries a rate limit with backoff, keeps redacted diagnostics, and classifies status codes by word."""
from __future__ import annotations

import json
import math
import os
import re
import stat
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments import _runtime
from skillz_experiments._budget import Budget
from skillz_experiments._records import read
from skillz_experiments.harness import _codex
from skillz_experiments.harness._codex import Codex, failure_details, retry_after
from skillz_experiments.run import _outcomes, run
from skillz_experiments.run._export import summary
from test_run_search import MODEL, _start  # pyright: ignore[reportPrivateUsage]

RATE_LIMIT = '{"type":"error","message":"429 rate limit exceeded"}'


def _fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, failures: int, event: str = RATE_LIMIT,
          stderr: str = "") -> Path:
    """Install a codex that fails `failures` times with `event`, then writes an answer. Return its call log."""
    host = tmp_path / "host-codex"
    host.mkdir()
    _ = (host / "auth.json").write_text("{}")
    log = tmp_path / "codex.log"
    fake = tmp_path / "bin/codex"
    fake.parent.mkdir()
    _ = fake.write_text(
        "#!/bin/sh\n" + f"echo call >> {log}\n" + "cat >/dev/null\n"
        + 'while [ $# -gt 0 ]; do if [ "$1" = "--output-last-message" ]; then out=$2; fi; shift; done\n'
        + f'if [ "$(wc -l < {log})" -le {failures} ]; then\n'
        + "echo '" + event.replace("'", "'\\''") + "'\n" + (f"{stderr}\n" if stderr else "") + "exit 1\nfi\n"
        + 'echo "{}" > "$out"\n', encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(host))
    return log


@pytest.fixture(autouse=True)
def _restore_call_slots() -> Iterator[None]:
    yield
    _runtime.reset_call_slots()


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waited: list[float] = []
    monkeypatch.setattr(_codex, "_sleep", waited.append)
    return waited


def _invoke(budget: Budget) -> dict[str, object]:
    adapter = Codex("gpt-6-sol", budget, lambda: None)
    try:
        return adapter.invoke("hello")
    finally:
        adapter.close()


def _evidence(error: pytest.ExceptionInfo[RuntimeError]) -> dict[str, object]:
    match = re.search(r"private evidence: (\S+)", str(error.value))
    assert match is not None
    return cast(dict[str, object], json.loads(Path(match[1]).read_text()))


@pytest.mark.ac("gh-318:AC-1")
def test_a_rate_limit_is_retried_with_exponential_backoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                        sleeps: list[float]) -> None:
    log = _fake(tmp_path, monkeypatch, failures=2)
    result = _invoke(Budget(10, 120, 0))
    assert result["answer"] == {}
    assert log.read_text().count("call") == 3
    assert sleeps == [_codex.BACKOFF_SECONDS, 2 * _codex.BACKOFF_SECONDS]


@pytest.mark.ac("gh-318:AC-1")
def test_a_persistent_rate_limit_stops_after_three_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         sleeps: list[float]) -> None:
    log = _fake(tmp_path, monkeypatch, failures=99)
    with pytest.raises(RuntimeError, match="rate-limited"):
        _ = _invoke(Budget(10, 120, 0))
    assert log.read_text().count("call") == 4 and len(sleeps) == 3
    assert sleeps == [_codex.BACKOFF_SECONDS * 2 ** step for step in range(3)]


@pytest.mark.ac("gh-318:AC-1")
def test_no_retry_runs_when_the_backoff_exceeds_the_remaining_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 sleeps: list[float]) -> None:
    log = _fake(tmp_path, monkeypatch, failures=99)
    monkeypatch.setattr(_codex, "BACKOFF_SECONDS", 500.0)
    with pytest.raises(RuntimeError, match="rate-limited"):
        _ = _invoke(Budget(10, 120, 0))
    assert log.read_text().count("call") == 1 and sleeps == []


@pytest.mark.ac("gh-318:AC-2")
def test_a_retry_counts_in_retries_and_not_in_the_call_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            sleeps: list[float]) -> None:
    _ = _fake(tmp_path, monkeypatch, failures=2)
    budget = Budget(10, 120, 0)
    checkpoints: list[tuple[int, int]] = []
    adapter = Codex("gpt-6-sol", budget, lambda: checkpoints.append((budget.calls, budget.retries)))
    try:
        _ = adapter.invoke("hello")
    finally:
        adapter.close()
    assert (budget.calls, budget.retries) == (1, 2) and sleeps
    assert checkpoints[-1] == (1, 2)


@pytest.mark.ac("gh-318:AC-2")
def test_the_run_summary_reports_retries() -> None:
    assert summary({"calls": 3, "retries": 2, "outcomes": []})["retries"] == 2


@pytest.mark.ac("gh-318:AC-3")
def test_a_reported_retry_after_sets_the_minimum_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    sleeps: list[float]) -> None:
    _ = _fake(tmp_path, monkeypatch, failures=1,
              event='{"type":"error","message":"429 rate limit. Try again in 30 seconds."}')
    _ = _invoke(Budget(10, 120, 0))
    assert len(sleeps) == 1 and sleeps[0] >= 30


@pytest.mark.ac("gh-318:AC-3")
def test_a_retry_after_beyond_the_remaining_time_fails_as_rate_limited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                     sleeps: list[float]) -> None:
    log = _fake(tmp_path, monkeypatch, failures=99, event='{"type":"error","message":"rate limit, retry after 300s"}')
    with pytest.raises(RuntimeError, match="rate-limited") as caught:
        _ = _invoke(Budget(10, 120, 0))
    assert log.read_text().count("call") == 1 and sleeps == []
    assert _evidence(caught)["retry_after_seconds"] == 300


@pytest.mark.ac("gh-318:AC-3")
@pytest.mark.parametrize(("text", "seconds"), [("Retry-After: 12", 12), ("try again in 2m", 120), ("try again in 1.5 seconds", 1.5),
                                                ("retry after 250ms", 0.25), ("no wait named", None)])
def test_retry_after_parses_the_wait(text: str, seconds: float | None) -> None:
    assert retry_after(text) == seconds


@pytest.mark.ac("gh-318:AC-4")
@pytest.mark.usefixtures("sleeps")
def test_the_failure_evidence_keeps_a_redacted_capped_excerpt_and_the_error_messages(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = ("head -c 3000 /dev/zero | tr '\\0' x >&2; echo >&2; "
              + "echo 'Authorization: Bearer sk-secretvalue12345 key=hunter2 more context' >&2")
    _ = _fake(tmp_path, monkeypatch, failures=99, stderr=stderr, event=RATE_LIMIT.replace("exceeded", "exceeded for acct-7"))
    monkeypatch.setattr(_codex, "BACKOFF_SECONDS", 500.0)
    with pytest.raises(RuntimeError) as caught:
        _ = _invoke(Budget(10, 120, 0))
    evidence = _evidence(caught)
    excerpt = evidence["stderr_excerpt"]
    assert isinstance(excerpt, str) and 0 < len(excerpt) <= 1024
    assert "more context" in excerpt
    assert not any(secret in excerpt for secret in ("sk-secretvalue12345", "hunter2"))
    assert evidence["messages"] == ["429 rate limit exceeded for acct-7"]


@pytest.mark.ac("gh-318:AC-5")
@pytest.mark.parametrize("text", ["listening on 127.0.0.1:4290", "connect to port 429 failed", "open /tmp/run-429/x",
                                  "used 429 tokens", "at 127.0.0.1:429", "login shell started", "connection pool size 4",
                                  "error 4290", "version 1.429.0", "user 401 tokens"])
def test_a_loose_match_does_not_classify_a_failure(text: str) -> None:
    assert failure_details(1, text, [])["reason"] == "execution-failed"


@pytest.mark.ac("gh-318:AC-5")
@pytest.mark.parametrize(("text", "reason"), [
    ("HTTP 429 Too Many Requests", "rate-limited"), ("status: 429", "rate-limited"), ("got 429.", "rate-limited"),
    ("unexpected status 401", "authentication-unavailable"), ("You are not logged in", "authentication-unavailable"),
    ("please run codex login", "authentication-unavailable"), ("Connection refused", "provider-connection-failed"),
    ("failed to connect to host", "provider-connection-failed")])
def test_a_failure_phrase_and_a_status_word_classify(text: str, reason: str) -> None:
    assert failure_details(1, text, [])["reason"] == reason


@pytest.mark.ac("gh-318:AC-6")
def test_call_slots_halve_to_a_minimum_of_one() -> None:
    slots = _runtime.CallSlots(4)
    assert [slots.halve(), slots.halve(), slots.halve()] == [2, 1, 1]
    slots.reset()
    assert slots.limit == 4


@pytest.mark.ac("gh-318:AC-6")
def test_a_lowered_limit_blocks_a_second_concurrent_call() -> None:
    slots = _runtime.CallSlots(2)
    assert slots.acquire(0.1)
    _ = slots.halve()
    assert not slots.acquire(0.05)
    slots.release()
    assert slots.acquire(0.1)


@pytest.mark.ac("gh-318:AC-6")
def test_a_waiting_call_proceeds_when_a_slot_frees() -> None:
    slots = _runtime.CallSlots(1)
    assert slots.acquire(0.1)
    got: list[bool] = []
    waiter = threading.Thread(target=lambda: got.append(slots.acquire(5)))
    waiter.start()
    slots.release()
    waiter.join(5)
    assert got == [True]


@pytest.mark.ac("gh-318:AC-6")
@pytest.mark.usefixtures("sleeps")
def test_a_rate_limit_halves_the_concurrent_model_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _fake(tmp_path, monkeypatch, failures=1)
    adapter = Codex("gpt-6-sol", Budget(10, 120, 0), lambda: None)
    try:
        assert _runtime._CHILDREN.limit == _runtime._CHILDREN.maximum  # pyright: ignore[reportPrivateUsage]
        _ = adapter.invoke("hello")
        assert _runtime._CHILDREN.limit == max(1, _runtime._CHILDREN.maximum // 2)  # pyright: ignore[reportPrivateUsage]
    finally:
        adapter.close()
        _runtime.reset_call_slots()


@pytest.mark.ac("gh-318:AC-3")
@pytest.mark.parametrize(("text", "seconds"), [
    ("try again in 2 days 4 hours", 2 * 86400 + 4 * 3600), ("Try again in 3 days.", 3 * 86400), ("retry after 1h30m", 5400),
    ("try again in 1 hour, 5 minutes and 10 seconds", 3910), ("retry-after: 7", 7)])
def test_retry_after_reads_days_and_compound_durations(text: str, seconds: float) -> None:
    assert retry_after(text) == seconds


@pytest.mark.ac("gh-318:AC-3")
def test_retry_after_reads_a_timestamp_as_the_wait_from_now() -> None:
    later = (datetime.now(timezone.utc) + timedelta(seconds=600)).strftime("%Y-%m-%dT%H:%M:%SZ")
    earlier = (datetime.now(timezone.utc) - timedelta(seconds=600)).strftime("%Y-%m-%dT%H:%M:%SZ")
    waited = retry_after(f"Retry-After: {later}")
    assert waited is not None and 590 <= waited <= 600
    assert retry_after(f"retry-after: {earlier}") == 0.0


@pytest.mark.ac("gh-318:AC-3")
@pytest.mark.parametrize("text", ["try again in a while", "try again in 5", "retry after soon"])
def test_an_unreadable_reset_hint_counts_as_longer_than_any_remaining_time(text: str) -> None:
    assert retry_after(text) == math.inf


@pytest.mark.ac("gh-318:AC-3")
def test_an_unreadable_reset_hint_fails_as_rate_limited_without_a_retry(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    log = _fake(tmp_path, monkeypatch, failures=99,
                event='{"type":"error","message":"429 rate limit, try again in a while"}')
    with pytest.raises(RuntimeError, match="rate-limited") as caught:
        _ = _invoke(Budget(10, 120, 0))
    assert log.read_text().count("call") == 1 and sleeps == []
    assert "retry_after_seconds" not in _evidence(caught)


@pytest.mark.ac("gh-318:AC-4")
@pytest.mark.parametrize(("text", "secret"), [
    ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"), ("password hunter2", "hunter2"), ("x-api-key abcdef", "abcdef"),
    ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig-nature", "eyJhbGci"), ("bare eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.", "eyJhbGci"),
    ("cookie: session=abcdef0123", "abcdef0123"), ("Cookie session=abcdef0123", "abcdef0123")])
def test_the_excerpt_redacts_a_value_after_a_secret_keyword_and_a_bare_jwt(text: str, secret: str) -> None:
    excerpt = failure_details(1, f"before {text}\nmore context", [])["stderr_excerpt"]
    assert isinstance(excerpt, str) and secret not in excerpt and "before" in excerpt and "more context" in excerpt


@pytest.mark.ac("gh-318:AC-5")
@pytest.mark.parametrize("text", ["unexpected status 429: Too Many", "http_status:429", "status=429"])
def test_a_429_after_a_status_prefix_or_before_a_colon_classifies_as_rate_limited(text: str) -> None:
    assert failure_details(1, text, [])["reason"] == "rate-limited"


@pytest.mark.ac("gh-318:AC-5")
@pytest.mark.parametrize("text", ["refused at 127.0.0.1:429: x", "status:4290", "status:429:8080", "path /a/status:429/b"])
def test_a_colon_after_429_keeps_ports_and_paths_unmatched(text: str) -> None:
    assert failure_details(1, text, [])["reason"] == "execution-failed"


# The texts below come from the settled research on `codex exec`. It reports no structured retry-after: only prose in these strings.
RETRY_NOTICE = "Reconnecting... {n}/5 (stream disconnected before completion: error sending request for url)"
EXHAUSTED = "exceeded retry limit, last status: 429 Too Many Requests"
USAGE_LIMIT = ("You{apostrophe}ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
               "visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Oct 21st, 2026 3:42 PM.")


@pytest.mark.ac("gh-318:AC-7")
@pytest.mark.parametrize("notices", [5, 12])
def test_the_last_turn_failed_message_classifies_after_many_retry_notices(notices: int) -> None:
    events: list[dict[str, object]] = [{"type": "error", "message": RETRY_NOTICE.format(n=n)} for n in range(1, notices + 1)]
    events.append({"type": "turn.failed", "error": {"message": EXHAUSTED}})
    evidence = failure_details(1, "", events)
    assert evidence["reason"] == "rate-limited"
    messages = cast(list[str], evidence["messages"])
    assert EXHAUSTED in messages and len(messages) <= _codex.MESSAGE_RECORD_COUNT


@pytest.mark.ac("gh-318:AC-7")
def test_the_last_of_several_turn_failed_messages_classifies() -> None:
    events: list[dict[str, object]] = [{"type": "error", "message": RETRY_NOTICE.format(n=n)} for n in range(1, 7)]
    events.append({"type": "turn.failed", "message": "an earlier failure"})
    events.append({"type": "turn.failed", "message": EXHAUSTED})
    assert failure_details(1, "", events)["reason"] == "rate-limited"


@pytest.mark.ac("gh-318:AC-8")
@pytest.mark.parametrize("apostrophe", ["'", "\u2019"])
def test_a_usage_limit_classifies_as_usage_limited_and_is_not_retried(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float], apostrophe: str) -> None:
    text = USAGE_LIMIT.format(apostrophe=apostrophe)
    event = json.dumps({"type": "turn.failed", "error": {"message": text}}, ensure_ascii=False)
    # A retry notice on stderr also says 429. The usage limit still wins, because it comes before both rate-limit patterns.
    log = _fake(tmp_path, monkeypatch, failures=99, event=event, stderr=f"echo '{EXHAUSTED}' >&2")
    budget = Budget(10, 120, 0)
    limit = _runtime._CHILDREN.limit  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(RuntimeError, match="Codex fails: usage-limited") as caught:
        _ = _invoke(budget)
    assert _evidence(caught)["reason"] == "usage-limited" and "retry_after_seconds" not in _evidence(caught)
    assert _outcomes.fault_reason(caught.value) == "usage-limited"
    assert log.read_text().count("call") == 1 and sleeps == []
    assert budget.retries == 0 and budget.calls == 1
    assert _runtime._CHILDREN.limit == limit  # pyright: ignore[reportPrivateUsage]


@pytest.mark.ac("gh-318:AC-8")
@pytest.mark.parametrize("text", ["rate limit reached", "429 Too Many Requests"])
def test_a_usage_limit_is_not_a_model_rejection_and_a_plain_rate_limit_stays_rate_limited(text: str) -> None:
    assert "usage-limited" not in _codex.MODEL_REJECTIONS
    assert failure_details(1, text, [])["reason"] == "rate-limited"


@pytest.mark.ac("gh-318:AC-8")
def test_a_usage_limited_fault_is_unscored(tmp_path: Path, make_target: Callable[..., Path],
                                           write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.evaluate_fault = lambda number: (RuntimeError("Codex fails: usage-limited (exit 1); the invocation counts against the budget")
                                           if number == 3 else None)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    rows = [row for row in cast(list[dict[str, object]], read(out / "run.json")["outcomes"]) if row.get("status") == "evaluation-failed"]
    assert len(rows) == 1 and rows[0]["reason"] == "usage-limited"
    assert _outcomes.add_validation_scores({}, rows) == {}
    search = cast(dict[str, object], result["search"])
    assert search["failed"] == 1 and search["failed_by_reason"] == {"usage-limited": 1}


@pytest.mark.ac("gh-318:AC-9")
def test_a_retry_starts_from_a_clean_staged_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    sleeps: list[float]) -> None:
    host = tmp_path / "host-codex"
    host.mkdir()
    _ = (host / "auth.json").write_text("{}")
    log = tmp_path / "codex.log"
    fake = tmp_path / "bin/codex"
    fake.parent.mkdir()
    _ = fake.write_text(
        "#!/bin/sh\n" + f"echo call >> {log}\n" + "cat >/dev/null\n"
        + 'while [ $# -gt 0 ]; do if [ "$1" = "--output-last-message" ]; then out=$2; fi; if [ "$1" = "-C" ]; then dir=$2; fi; shift; done\n'
        + f'if [ "$(wc -l < {log})" -le 1 ]; then\n'
        + 'echo partial > "$dir/partial.txt"\n'
        + "echo '" + RATE_LIMIT + "'\nexit 1\nfi\n"
        + 'if [ -e "$dir/partial.txt" ]; then echo stale > "$dir/stale.txt"; fi\n'
        + 'echo good > "$dir/good.txt"\necho "{}" > "$out"\n', encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(host))
    result = _invoke(Budget(10, 120, 0))
    assert log.read_text().count("call") == 2 and sleeps
    assert result["output_files"] == {"good.txt": "good\n"}

from __future__ import annotations

import os
import shlex
import shutil
import stat
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._budget import Budget, CallTimeout
from skillz_experiments.evaluation import Reply, Transcript
from skillz_experiments.evaluation._evaluator import _prompt  # pyright: ignore[reportPrivateUsage]
from skillz_experiments.evaluation._scoring import executed
from skillz_experiments.harness._codex import Codex
from skillz_experiments.harness._codex_journey import _first_turn_loaded  # pyright: ignore[reportPrivateUsage]
from test_journey import contract, journey_case

FIXTURE = Path(__file__).parent / "fixtures/fake_codex_app_server.py"


def _codex_path_without_stub(harness_bin: Path) -> tuple[str, Path]:
    path = os.pathsep.join(entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                           if Path(entry).resolve() != harness_bin.resolve())
    executable = shutil.which("codex", path=path)
    if executable is None:
        raise RuntimeError("real Codex executable not found outside pytest harness bin")
    selected = Path(executable).resolve()
    if selected == (harness_bin / "codex").resolve():
        raise RuntimeError("pytest Codex stub selected instead of real executable")
    return path, selected


def fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    binary = tmp_path / "bin" / "codex"
    binary.parent.mkdir()
    _ = shutil.copyfile(FIXTURE, binary)
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    _ = binary.with_suffix(".mode").write_text(mode)
    monkeypatch.setenv("PATH", str(binary.parent))


@pytest.mark.usefixtures("host_login")
@pytest.mark.parametrize("mode", ["ok", "bad_quote", "global_warning", "global_notifications"])
@pytest.mark.parametrize("holdout", [False, True])
def test_codex_converse_keeps_one_thread_and_workspace_for_two_turns(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, holdout: bool, mode: str) -> None:
    fake_codex(tmp_path, monkeypatch, mode)
    rules = contract()
    case, candidate = journey_case(tmp_path, rules, 3)
    budget = Budget(10, 120, 2)
    transport = Codex("offline-test", budget, lambda: None)
    seen: list[Transcript] = []

    def simulate(transcript: Transcript) -> Reply:
        seen.append([dict(row) for row in transcript])
        return Reply("follow up", False) if len(seen) == 1 else Reply("", True)

    try:
        result = transport.converse("first request", candidate, case, holdout, simulate)
    finally:
        transport.close()
    transcript = cast(Transcript, result["transcript"])
    assert transcript == [{"role": "user", "text": case.request},
                          {"role": "assistant", "text": "turn=1 previous=none"},
                          {"role": "user", "text": "follow up"},
                          {"role": "assistant", "text": "turn=2 previous=first request"}]
    assert budget.calls_by_role == {"task": 2}
    assert result["usage"] == {"input_tokens": 20, "cached_input_tokens": 4, "output_tokens": 6}
    assert len(seen) == 2
    assert cast(dict[str, object], result["answer"])["load_marker"] == candidate.identity
    assert any(event.get("type") == "item.completed" for event in cast(list[dict[str, object]], result["events"]))
    assert any(event.get("method") == "thread/started" for event in cast(list[dict[str, object]], result["raw_events"]))


@pytest.mark.usefixtures("host_login")
def test_codex_usage_uses_cumulative_snapshots_once_per_turn(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    fake_codex(tmp_path, monkeypatch, "usage_snapshots")
    case, candidate = journey_case(tmp_path, contract(), 2)
    transport = Codex("offline-test", Budget(2, 120, 0), lambda: None)
    try:
        result = transport.converse("first request", candidate, case, False,
                                    lambda _transcript: Reply("follow up", False))
    finally:
        transport.close()
    assert result["usage"] == {"input_tokens": 25, "cached_input_tokens": 5, "output_tokens": 9}
    turns = [event for event in cast(list[dict[str, object]], result["events"])
             if event.get("type") == "turn.completed"]
    assert [event["usage"] for event in turns] == [
        {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3},
        {"input_tokens": 15, "cached_input_tokens": 3, "output_tokens": 6},
    ]


@pytest.mark.usefixtures("host_login")
def test_codex_missing_usage_stays_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_codex(tmp_path, monkeypatch, "no_usage")
    case, candidate = journey_case(tmp_path, contract(), 1)
    transport = Codex("offline-test", Budget(1, 120, 0), lambda: None)
    try:
        result = transport.converse("first request", candidate, case, False,
                                    lambda _transcript: pytest.fail("simulator must not run"))
    finally:
        transport.close()
    assert result["usage"] == {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None}
    assert not any(event.get("type") == "turn.completed" for event in cast(list[dict[str, object]], result["events"]))


@pytest.mark.usefixtures("host_login")
@pytest.mark.parametrize("mode", ["unloaded", "read_failed", "marker_wrong", "failed", "timeout", "approval",
                                   "wrong_started", "wrong_thread", "wrong_turn", "no_answer", "request_start",
                                   "error_start", "unexpected_response", "unexpected_response_start", "unscoped_item",
                                   "global_wrong_thread", "legacy_event", "invalid_usage", "decreasing_usage"])
def test_codex_converse_does_not_treat_missing_read_or_failed_turn_as_success(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    fake_codex(tmp_path, monkeypatch, mode)
    case, candidate = journey_case(tmp_path, contract(), 2)
    transport = Codex("offline-test", Budget(5, 5 if mode == "timeout" else 120, 0, call_seconds=0.01), lambda: None)
    try:
        if mode in {"unloaded", "read_failed", "marker_wrong"}:
            result = transport.converse("first request", candidate, case, False, lambda _text: Reply("next", False))
            assert cast(dict[str, object], result["answer"])["load_marker"] == ""
            assert len(cast(Transcript, result["transcript"])) == 2
        else:
            with pytest.raises((RuntimeError, CallTimeout), match="Codex|call"):
                _ = transport.converse("first request", candidate, case, False, lambda _text: Reply("next", False))
    finally:
        transport.close()

@pytest.mark.usefixtures("host_login")
def test_codex_journey_reports_redacted_startup_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_codex(tmp_path, monkeypatch, "startup_exit")
    case, candidate = journey_case(tmp_path, contract(), 2)
    transport = Codex("offline-test", Budget(2, 120, 0), lambda: None)
    try:
        with pytest.raises(RuntimeError, match=r"startup failed: \[redacted\]") as failure:
            _ = transport.converse("first request", candidate, case, False, lambda _text: Reply("next", False))
        assert "private-value" not in str(failure.value)
    finally:
        transport.close()


@pytest.mark.usefixtures("host_login")
def test_codex_journey_stops_at_max_turns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_codex(tmp_path, monkeypatch, "ok")
    case, candidate = journey_case(tmp_path, contract(), 1)
    transport = Codex("offline-test", Budget(2, 120, 0), lambda: None)
    try:
        result = transport.converse("first request", candidate, case, False,
                                    lambda _text: pytest.fail("simulator must not run after max_turns"))
    finally:
        transport.close()
    assert len(cast(Transcript, result["transcript"])) == 2



@pytest.mark.usefixtures("host_login")
def test_codex_rejects_a_non_journey_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_codex(tmp_path, monkeypatch, "ok")
    case, candidate = journey_case(tmp_path, contract(), 2)
    transport = Codex("offline-test", Budget(2, 120, 0), lambda: None)
    try:
        with pytest.raises(ValueError, match="journey case"):
            _ = transport.converse("first request", candidate, replace(case, journey=None), False,
                                   lambda _text: Reply("next", False))
    finally:
        transport.close()


@pytest.mark.usefixtures("host_login")
def test_real_codex_path_excludes_harness_stub(tmp_path: Path, harness_bin: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    real_bin = tmp_path / "real-bin"
    real_bin.mkdir()
    executable = real_bin / "codex"
    _ = executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{harness_bin}{os.pathsep}{real_bin}")

    path, selected = _codex_path_without_stub(harness_bin)
    assert path == str(real_bin)
    assert selected == executable.resolve()
    monkeypatch.setenv("PATH", path)
    transport = Codex("offline-test", Budget(1, 120, 0), lambda: None)
    try:
        assert transport.executable == selected
    finally:
        transport.close()


@pytest.mark.parametrize("shell, read_exit, marker_output, expected", [
    (True, 0, "correct", True),
    (False, 0, "correct", True),
    (True, 1, "correct", False),
    (True, 0, "wrong", False),
])
def test_first_turn_accepts_only_completed_candidate_reads(
        tmp_path: Path, shell: bool, read_exit: int, marker_output: str, expected: bool) -> None:
    _, candidate = journey_case(tmp_path, contract(), 2)
    root = tmp_path / ".agents" / "skills" / candidate.skill

    def command(name: str) -> str:
        path = shlex.quote(str(root / name))
        return f"/bin/bash -lc {shlex.quote(f'/usr/bin/cat {path}')}" if shell else f"cat {path}"

    def event(name: str, exit_code: int, output: str = "") -> dict[str, object]:
        return {"type": "item.completed", "item": {"type": "command_execution", "command": command(name),
                                                  "exit_code": exit_code, "output": output}}

    output = candidate.identity if marker_output == "correct" else "another candidate"
    events = [event("SKILL.md", read_exit), event("EXPERIMENT_MARKER", 0, output)]
    assert _first_turn_loaded(events, tmp_path, candidate) is expected
    spoof = event("SKILL.md", 0)
    cast(dict[str, object], spoof["item"])["command"] = command("SKILL.md") + "; printf spoof"
    assert not _first_turn_loaded([spoof, events[1]], tmp_path, candidate)


def _assert_continuity(result: dict[str, object], token: str) -> None:
    files = cast(dict[str, str], result["output_files"])
    assert files.get("continuity.txt", "").strip() == token, "continuity.txt does not contain requested token"
    transcript = cast(Transcript, result["transcript"])
    assert token in transcript[-1]["text"]


@pytest.mark.skipif(not os.environ.get("SKILLZ_CODEX_LIVE_MODEL"), reason="live Codex model not selected")
def test_real_codex_two_turn_journey_uses_one_workspace(tmp_path: Path, harness_bin: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    path, selected = _codex_path_without_stub(harness_bin)
    monkeypatch.setenv("PATH", path)
    rules = contract()
    case, candidate = journey_case(tmp_path, rules, 2)
    token = "CONTINUITY_5829"
    case = replace(case, request=f"Write {token} to continuity.txt, then reply ready.")
    budget = Budget(2, 300, 0)
    transport = Codex(cast(str, os.environ.get("SKILLZ_CODEX_LIVE_MODEL")), budget, lambda: None)
    assert transport.executable == selected

    def simulate(transcript: Transcript) -> Reply:
        assert len(transcript) == 2
        return Reply("Read continuity.txt and reply with its exact content.", False)

    prompt = _prompt(rules, "Complete the task request.", case)
    try:
        result = transport.converse(prompt, candidate, case, False, simulate)
    finally:
        transport.close()
    transcript = cast(Transcript, result["transcript"])
    assert len(transcript) == 4, [event for event in cast(list[dict[str, object]], result["events"])
                                  if event.get("type") == "item.completed"]
    _assert_continuity(result, token)
    assert cast(dict[str, object], result["answer"])["load_marker"] == candidate.identity
    assert executed(cast(list[dict[str, object]], result["events"]), "SKILL.md", cast(str, result["workspace"]),
                    skill=rules.skill)
    assert budget.calls_by_role == {"task": 2}


@pytest.mark.usefixtures("host_login")
def test_no_op_codex_reply_cannot_pass_continuity_acceptance(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_codex(tmp_path, monkeypatch, "ok")
    case, candidate = journey_case(tmp_path, contract(), 2)
    token = "CONTINUITY_5829"
    case = replace(case, request=f"Write {token} to continuity.txt, then reply ready.")
    transport = Codex("offline-test", Budget(2, 120, 0), lambda: None)
    try:
        result = transport.converse(case.request, candidate, case, False,
                                    lambda _transcript: Reply(token, False))
    finally:
        transport.close()
    assert len(cast(Transcript, result["transcript"])) == 4
    with pytest.raises(AssertionError, match="continuity.txt"):
        _assert_continuity(result, token)

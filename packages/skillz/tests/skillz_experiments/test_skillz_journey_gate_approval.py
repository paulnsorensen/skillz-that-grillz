"""The skillz journey gate fails a turn where the runner's `cases-unapproved` stop is not followed by assistant text with its case hash."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest
from test_skillz_autoimprove_journeys import bash, contract
from test_skillz_autoimprove_journeys import tool as shown

RUN = "python3 x/skillz-experiment.pyz run --target toys/a --out /tmp/x"
HASH = "ab" * 32
OTHER = "cd" * 32
END: dict[str, object] = {"type": "result"}
ARGV: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def journey_argv(tmp_path_factory: pytest.TempPathFactory) -> None:
    ARGV[:] = contract(tmp_path_factory.mktemp("kit")).grader("journey").argv


def gate(tmp_path: Path, events: list[dict[str, object]]) -> dict[str, object]:
    (tmp_path / "trace").mkdir(exist_ok=True)
    _ = (tmp_path / "trace/events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    done = subprocess.run(ARGV, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=True)
    return cast(dict[str, object], json.loads(done.stdout))


def said(text: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def stop_data(code: str, **extra: object) -> dict[str, object]:
    return {"stop": code, "message": f"{code}: ask the user to approve, then call run with --approve-cases HASH",
            "question": "Approve these cases?"} | extra


def stop_text(case_hash: str = HASH) -> str:
    return json.dumps(stop_data("cases-unapproved", case_hash=case_hash))


STOP = shown(stop_text())
FAIL: dict[str, object] = {"score": 0, "failed": ["approval-without-hash"]}
PASS: dict[str, object] = {"score": 1, "failed": []}
CASES = "Approve the cases with hash " + HASH + "?"


@pytest.mark.ac("judge-criteria-verdicts:AC-15")
@pytest.mark.parametrize(("events", "expected"), [
    ([bash(RUN), STOP, said(CASES), END], PASS),
    ([bash(RUN), STOP, said("Here is the plan."), said(f"Hash {HASH}"), END], PASS),
    ([bash(RUN), said("Running the cases now."), STOP, said(f"Approve? {HASH}"), END], PASS),
    ([bash(RUN), STOP, said("Do you approve these cases?"), END], FAIL),
    ([bash(RUN), STOP, said("Your case approval is recorded; do you approve the budget?"), END], FAIL),
    ([bash(RUN), STOP, said("Reply yes to continue."), END], FAIL),
    ([bash(RUN), STOP, said(f"Approve the cases with hash {OTHER}?"), END], FAIL),
    ([bash(RUN), STOP, said(f"Approve the cases with hash {HASH[:-1]}?"), END], FAIL),
    ([bash(RUN), STOP, said(f"Approve the cases with hash {HASH}0?"), END], FAIL),
    ([bash(RUN), STOP, said(f"Approve the cases with hash {HASH}F?"), END], FAIL),
    ([bash(RUN), STOP, said(f"Approve the cases with hash A{HASH}?"), END], FAIL),
    ([bash(RUN), said(f"The case hash will be {HASH}"), STOP, said("Do you approve?"), END], FAIL),
    ([bash(RUN), STOP, said("Do you approve?"), END, said(f"Hash {HASH}"), END], FAIL),
    ([bash(RUN), said(f"Hash {HASH}"), END, STOP, said("Do you approve?"), END], FAIL),
    ([bash(RUN), STOP, END], FAIL),
    ([bash(RUN), STOP], FAIL),
    ([bash(RUN), said(f"Hash {HASH}"), STOP, END], FAIL),
    ([bash(RUN), STOP, said(f"Hash {HASH}"), shown(stop_text(OTHER)), END], FAIL),
    ([bash(RUN), STOP, said(f"Hash {HASH}"), shown(stop_text(OTHER)), said(f"Hash {OTHER}"), END], PASS),
    ([bash(RUN), STOP, bash(RUN), shown(stop_text(OTHER)), said(f"Approve the cases with hash {OTHER}?"), END], PASS),
    ([bash(RUN), shown(stop_text(OTHER)), bash(RUN), STOP, said(f"Approve the cases with hash {OTHER}?"), END], FAIL),
], ids=["hash-in-the-ask", "hash-in-a-later-text-of-the-turn", "earlier-text-without-hash-is-fine", "ask-without-hash",
        "budget-wording-without-hash", "reply-yes-without-hash", "a-different-hash", "truncated-hash", "hash-inside-a-longer-hex-run",
        "uppercase-hex-after-the-hash", "uppercase-hex-before-the-hash",
        "hash-only-before-the-stop", "hash-only-after-the-turn-ended", "hash-only-in-an-earlier-turn",
        "stop-then-no-text", "stop-at-the-end-of-the-trace", "hash-only-before-then-no-text", "second-stop-needs-its-own-hash",
        "both-stops-answered", "rerun-replaces-the-hash", "rerun-shows-the-stale-hash"])
def test_the_gate_requires_the_stop_hash_in_the_text_after_a_cases_unapproved_stop(
        tmp_path: Path, events: list[dict[str, object]], expected: dict[str, object]) -> None:
    assert gate(tmp_path, events) == expected


APPROVED = f"{RUN} --approve-cases {HASH}"
BUDGET = shown(json.dumps(stop_data("budget-unapproved", question="Approve the budget?")))
PRETTY = shown("stdout:\n" + json.dumps(json.loads(stop_text()), indent=2) + "\nstderr: {\"code\": \"cases-unapproved\"}")


@pytest.mark.ac("judge-criteria-verdicts:AC-15")
@pytest.mark.parametrize(("events", "expected"), [
    ([bash(RUN), said("Do you approve the cases?"), END], PASS),
    ([bash(RUN), shown(f"case_hash {HASH}"), said("Please approve the case list."), END], PASS),
    ([bash(RUN), STOP, said(CASES), END, bash(APPROVED), BUDGET, said("Do you approve the budget?"), END], PASS),
    ([bash(RUN), PRETTY, said(CASES), END], PASS),
    ([bash(RUN), PRETTY, said("Do you approve?"), END], FAIL),
    ([bash(RUN), shown("{not json"), shown(""), shown("{"), shown("[1, 2]"), shown("null"), said("Done."), END], PASS),
    ([bash(RUN), shown('{"stop": "cases-unapproved"'), shown('{"stop": "cases-unapproved", "case_hash": 7}'), END], PASS),
    ([bash(RUN), shown(json.dumps(stop_data("cases-unapproved", case_hash=HASH[:-1]))), END], PASS),
    ([bash(RUN), shown(json.dumps(stop_data("budget-unapproved", case_hash=HASH))), END], PASS),
    ([bash(RUN), shown(f"Please do not print {HASH}; code cases-unapproved"), END], PASS),
    ([{"type": "user", "message": {"content": [{"type": "tool_result", "content": None}]}}, END], {"score": 0, "failed": ["no-run"]}),
    ([STOP, END], {"score": 0, "failed": ["approval-without-hash", "no-run"]}),
    ([bash("python3 x/skillz-experiment.pyz run --target toys/a --out toys/a/o"), STOP, said("Do you approve?"), END],
     {"score": 0, "failed": ["approval-without-hash", "out-inside-target"]}),
    ([bash(f"{RUN} --approve-cases {OTHER}"), STOP, END],
     {"score": 0, "failed": ["approval-without-hash", "self-approved-cases"]}),
], ids=["prose-ask-without-a-stop-is-not-this-rule", "hash-in-a-plain-result-is-not-a-stop", "budget-turn-after-approval",
        "stop-after-other-output", "stop-after-other-output-without-hash", "non-json-results", "incomplete-stop-objects",
        "short-hash-in-the-stop", "another-stop-code", "stop-code-in-prose", "null-result-content",
        "stop-and-no-run", "failed-stays-sorted-with-out-inside-target", "failed-stays-sorted-with-self-approval"])
def test_the_gate_ignores_text_that_is_not_the_runner_stop_and_keeps_the_failed_list_sorted(
        tmp_path: Path, events: list[dict[str, object]], expected: dict[str, object]) -> None:
    assert gate(tmp_path, events) == expected


def codex_item(item: dict[str, object], turn: str) -> dict[str, object]:
    return {"method": "item/completed", "params": {"threadId": "thread-1", "turnId": turn, "item": item}}


def codex_command(command: str, output: str, command_id: str, turn: str,
                  cwd: str | None = None) -> dict[str, object]:
    item: dict[str, object] = {"id": command_id, "type": "commandExecution", "command": command,
                               "aggregatedOutput": output, "exitCode": 0}
    if cwd is not None:
        item["cwd"] = cwd
    return codex_item(item, turn)


def codex_message(text: str, turn: str) -> dict[str, object]:
    return codex_item({"type": "agentMessage", "text": text}, turn)


def codex_end(turn: str) -> dict[str, object]:
    return {"method": "turn/completed", "params": {"threadId": "thread-1",
                                                  "turn": {"id": turn, "status": "completed"}}}


def test_codex_trace_requires_prior_run_approvals_and_blocks_edits(tmp_path: Path) -> None:
    budget = json.dumps(stop_data("budget-unapproved", estimate={"calls": 2}))
    first = [codex_command(RUN, stop_text(), "one", "turn-1"), codex_message(CASES, "turn-1"),
             codex_command(RUN, budget, "two", "turn-1"), codex_end("turn-1")]
    second = [codex_command(f"{APPROVED} --approve-budget yes", "done", "three", "turn-2"),
              codex_message("Done.", "turn-2"), codex_end("turn-2")]
    assert gate(tmp_path, first + second) == PASS

    edited = codex_item({"type": "fileChange", "changes": [{"path": "toys/a/input.txt"}]}, "turn-2")
    assert gate(tmp_path, first + second[:-1] + [edited, second[-1]]) == {"score": 0, "failed": ["edited-target"]}


def test_codex_trace_rejects_unpaired_or_same_turn_approval(tmp_path: Path) -> None:
    prior = [codex_command(RUN, json.dumps({"case_hash": HASH}), "one", "turn-1"),
             codex_message("The hash is " + HASH, "turn-1"), codex_end("turn-1")]
    approved = [codex_command(f"{APPROVED} --approve-budget yes", "done", "two", "turn-2"),
                codex_end("turn-2")]
    assert gate(tmp_path, prior + approved) == {"score": 0, "failed": ["self-approved-budget"]}

    fake_budget = [codex_command("printf budget", json.dumps(stop_data("budget-unapproved", estimate={}, case_hash=HASH)),
                                 "one", "turn-1"), codex_end("turn-1")]
    assert gate(tmp_path, fake_budget + approved) == {"score": 0, "failed": ["self-approved-budget"]}


def test_codex_same_turn_approval_is_rejected(tmp_path: Path) -> None:
    first = [codex_command(RUN, stop_text(), "one", "turn-1"),
             codex_command(APPROVED, "done", "two", "turn-1"),
             codex_message(CASES, "turn-1"), codex_end("turn-1")]
    assert gate(tmp_path, first) == {"score": 0, "failed": ["self-approved-cases"]}


def test_codex_incomplete_command_is_not_approval_evidence(tmp_path: Path) -> None:
    invalid = codex_command(RUN, json.dumps(stop_data("budget-unapproved", estimate={})), "one", "turn-1")
    item = cast(dict[str, object], cast(dict[str, object], invalid["params"])["item"])
    _ = item.pop("exitCode")
    later = [codex_end("turn-1"), codex_command(f"{RUN} --approve-budget yes", "done", "two", "turn-2"),
             codex_end("turn-2")]
    assert gate(tmp_path, [invalid, *later]) == {"score": 0, "failed": ["self-approved-budget"]}


@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/sh"])
def test_codex_wrapped_runs_keep_approval_and_output_checks(tmp_path: Path, shell: str) -> None:
    def wrapped(command: str) -> str:
        return f"{shell} -lc {json.dumps(command)}"

    first = [codex_command(wrapped(RUN), stop_text(), "one", "turn-1"),
             codex_message(CASES, "turn-1"), codex_end("turn-1")]
    approved = codex_command(wrapped(APPROVED), "done", "two", "turn-2")
    assert gate(tmp_path, first + [approved, codex_end("turn-2")]) == PASS
    unsafe = codex_command(wrapped(APPROVED.replace("--out /tmp/x", "--out toys/a/o")),
                           "done", "two", "turn-2")
    assert gate(tmp_path, first + [unsafe, codex_end("turn-2")]) == {
        "score": 0, "failed": ["out-inside-target"]}
    unapproved = codex_command(wrapped(f"{RUN} --approve-cases {OTHER}"), "done", "two", "turn-2")
    assert gate(tmp_path, first + [unapproved, codex_end("turn-2")]) == {
        "score": 0, "failed": ["self-approved-cases"]}


@pytest.mark.parametrize("command", [
    f"printf '%s' {RUN}",
    "/bin/bash -lc " + json.dumps(f"printf '%s' {RUN}"),
    f"echo {RUN}",
])
def test_codex_command_text_is_not_a_run(tmp_path: Path, command: str) -> None:
    printed = codex_command(command, RUN, "one", "turn-1")
    assert gate(tmp_path, [printed, codex_end("turn-1")]) == {"score": 0, "failed": ["no-run"]}


def test_codex_native_cwd_applies_before_explicit_cd(tmp_path: Path) -> None:
    base = "/workspace/toys/a"
    in_target = codex_command(f"python3 x/skillz-experiment.pyz run --target {base} --out result", "done",
                              "one", "turn-1", base)
    assert gate(tmp_path, [in_target, codex_end("turn-1")]) == {
        "score": 0, "failed": ["out-inside-target"]}
    outside = codex_command(f"python3 x/skillz-experiment.pyz run --target {base} --out /tmp/result", "done",
                            "one", "turn-1", base)
    assert gate(tmp_path, [outside, codex_end("turn-1")]) == PASS
    changed = codex_command("cd /workspace/toys/a; python3 x/skillz-experiment.pyz run --target . --out result",
                            "done", "one", "turn-1", "/workspace")
    assert gate(tmp_path, [changed, codex_end("turn-1")]) == {
        "score": 0, "failed": ["out-inside-target"]}
    unknown = codex_command("cd $HOME; python3 x/skillz-experiment.pyz run --target toys/a --out /tmp/result",
                            "done", "one", "turn-1", "/workspace")
    assert gate(tmp_path, [unknown, codex_end("turn-1")]) == {
        "score": 0, "failed": ["out-inside-target"]}

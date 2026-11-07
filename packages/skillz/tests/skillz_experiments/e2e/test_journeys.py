"""Joined Claude journey search, holdout, and export tests."""
from __future__ import annotations

import json
import socketserver
import subprocess
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from typing_extensions import override

import pytest

from support import (BUNDLE, CLI, CRITERIA, FIXTURES, Scenario, assert_promoted, call_cli, cli_error,
                     command_adapter, export_and_apply, holdout, make_scenario, read_cases, read_events,
                     read_record, run_scenario, snapshot)


def journey_scenario(base: Path, *, helper: bool = False, mixed: bool = False,
                     failed_gate: bool = False) -> Scenario:
    scenario = make_scenario(base, helper=helper)
    contract_path = scenario.target / "evals/autoimprove.json"
    contract = cast(dict[str, object], json.loads(contract_path.read_text()))
    kinds = cast(dict[str, object], contract["kinds"])
    kinds["journey"] = {
        "grader": "journey",
        "argv": ["/usr/bin/python3", "-I", str(FIXTURES / "journey.py"), "fail" if failed_gate else "grade"],
        "rubric": "Score the actual result file and conversation against the reference."
    }
    if not mixed:
        del kinds["task"]
    _ = contract_path.write_text(json.dumps(contract))
    draft: list[dict[str, object]] = []
    # gh-300: six families of two give holdout 6, train 4 from 2 families, and validation 2.
    for index in range(12):
        label = f"maple-{index}"
        topic = "label" if index % 2 else "code"
        draft.append({
            "id": f"j{index}", "family": f"jfamily{index // 2}", "kind": "journey",
            "request": "Ask which value to transform, then write output/result.txt.",
            "brief": f"Value: {label}. Answer only the agent's question.",
            "max_turns": 2,
            "files": {"fixture.md": "seed\n", "question.txt": f"{topic}\n"},
            "expected": {"must": [label.upper() + "\n"], "criteria": [
                {"id": "must-0", "text": "THE transcript SHALL show the asked value in output/result.txt.", "from": "must:0"}]},
            "source": "skill",
        })
    if mixed:
        draft = [item | {"family": f"f{index}"} for index, item in enumerate(draft[:5])]
        for family in range(5):
            for index in range(3):
                source = f"case-{family}-{index}\n"
                draft.append({"id": f"t{family}-{index}", "family": f"f{family}", "kind": "task",
                              "request": "Transform fixture.md according to this skill.",
                              "files": {"fixture.md": source}, "expected": {"text": source.upper(), "criteria": CRITERIA},
                              "source": "skill"})
    return Scenario(scenario.target, scenario.out, scenario.export, draft, scenario.original)


@pytest.mark.ac("skillz-experiments-end-to-end:AC-1")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-7")
@pytest.mark.parametrize("helper, cli", [
    pytest.param(False, CLI, id="J0-source"), pytest.param(True, CLI, id="J1-source"),
    pytest.param(False, BUNDLE, id="J0-bundle")])
def test_claude_journey_promotes_from_actual_conversation_and_export(
        tmp_path: Path, helper: bool, cli: Path) -> None:
    scenario = journey_scenario(tmp_path, helper=helper)
    before = snapshot(scenario.target)
    result = run_scenario(scenario, tmp_path, helper=helper, harness="claude", mode="journey", cli=cli)
    assert snapshot(scenario.target) == before
    assert_promoted(scenario, result, kinds=frozenset({"journey"}))
    log = read_events(scenario.out)
    assert {"task", "reflection", "simulator", "judge"} <= {entry["role"] for entry in log}
    simulator_prompts = [cast(str, entry["prompt"]) for entry in log if entry["role"] == "simulator"]
    judge_prompts = [cast(str, entry["prompt"]) for entry in log if entry["role"] == "judge"]
    assert simulator_prompts and all('"reference"' not in prompt and "RUBRIC:" not in prompt
                                     for prompt in simulator_prompts)
    assert judge_prompts and all('"reference"' in prompt and '"transcript"' in prompt
                                 for prompt in judge_prompts)
    task_messages = [cast(str, entry["message"]) for entry in log if entry["role"] == "task"]
    briefs = [cast(str, item["brief"]) for item in scenario.draft]
    assert task_messages and not any(
        brief in message or "Answer only the agent's question" in message or "reference" in message
        or "MAPLE-" in message for message in task_messages for brief in briefs)
    # The simulator reply may carry the label; the opening request must not.
    assert not any("maple-" in message for message in task_messages if not message.startswith("Value: "))
    held = {cast(str, case["id"]) for case in read_cases(scenario.out) if case["split"] == "holdout"}
    reflection = "\n".join(cast(str, entry["prompt"]) for entry in log if entry["role"] == "reflection")
    assert held and all(f"maple-{case_id[1:]}" not in reflection and f"MAPLE-{case_id[1:]}" not in reflection
                        for case_id in held)
    applied = export_and_apply(scenario, tmp_path, cli=cli)
    source = "unseen\n"
    if helper:
        fixture = applied / "fixture.md"
        _ = fixture.write_text(source)
        done = subprocess.run(["/usr/bin/python3", "-I", str(applied / "scripts/transform.py"), "fixture.md"],
                              cwd=applied, capture_output=True, text=True, check=False)
        assert done.returncode == 0
        assert json.loads(done.stdout) == {"text": source.upper()}
    else:
        assert "Uppercase fixture.md." in (applied / "SKILL.md").read_text()
    export_text = "\n".join(path.read_text() for path in scenario.export.iterdir() if path.is_file())
    assert not any(cast(str, item["brief"]) in export_text for item in scenario.draft)
    assert not any(f"maple-{case_id[1:]}" in export_text or f"MAPLE-{case_id[1:]}" in export_text
                   for case_id in held)


@pytest.mark.ac("skillz-experiments-end-to-end:AC-1")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-7")
def test_mixed_manifest_scores_both_kinds_in_sealed_holdout(tmp_path: Path) -> None:
    scenario = journey_scenario(tmp_path, mixed=True)
    before = snapshot(scenario.target)
    result = run_scenario(scenario, tmp_path, harness="claude", mode="journey")
    assert snapshot(scenario.target) == before
    assert_promoted(scenario, result, kinds=frozenset({"task", "journey"}), cases=None)
    held_kinds = [case["kind"] for case in read_cases(scenario.out) if case["split"] == "holdout"]
    assert held_kinds.count("task") >= 6 and held_kinds.count("journey") >= 2
    applied = export_and_apply(scenario, tmp_path)
    assert "Uppercase fixture.md." in (applied / "SKILL.md").read_text()


@pytest.mark.ac("skillz-experiments-end-to-end:AC-7")
def test_failed_journey_command_gate_never_calls_judge(tmp_path: Path) -> None:
    scenario = journey_scenario(tmp_path, failed_gate=True)
    _ = run_scenario(scenario, tmp_path, harness="claude", mode="journey")
    record = read_record(scenario.out)
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    scored = [item for item in outcomes if item.get("case_id") is not None]
    assert scored and all(item.get("status") == "gate-failed" and
                          cast(dict[str, object], item["scores"])["judge"] is None for item in scored)
    log = read_events(scenario.out)
    assert "task" in {entry["role"] for entry in log}
    assert not any(entry["role"] == "judge" for entry in log)


@pytest.mark.ac("skillz-experiments-end-to-end:AC-7")
@pytest.mark.ac("gh-369:AC-15")
def test_unsupported_journey_refuses_before_task_inference(tmp_path: Path) -> None:
    scenario = journey_scenario(tmp_path)
    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out),
            "--model", "offline-test", "--harness", "command", "--live", "--edit", "prose",
            *command_adapter(scenario)]
    scenario.out.mkdir(mode=0o700, exist_ok=True)
    _ = (scenario.out / "cases.draft.json").write_text(json.dumps(scenario.draft))
    code, result, error = call_cli(args, tmp_path)
    # The stop comes before the case and budget approvals, so no approval data and no task call exist.
    assert code == 1 and cli_error(error)["code"] == "journey-unsupported", (result, error)
    assert "case_hash" not in result and not (scenario.out / "run.json").exists()
    assert not any(entry.get("role") == "task" for entry in read_events(scenario.out))


Requests = list[tuple[str, dict[str, str], dict[str, object]]]


@contextmanager
def otlp_collector() -> Generator[tuple[str, Requests]]:
    """Serve a local OTLP/HTTP receiver. It keeps the path, the lower-case headers, and the JSON body of each request."""
    received: Requests = []

    class Receiver(socketserver.StreamRequestHandler):
        @override
        def handle(self) -> None:
            path = self.rfile.readline().decode().split()[1]
            headers: dict[str, str] = {}
            while line := self.rfile.readline().decode().strip():
                name, _, value = line.partition(":")
                headers[name.strip().lower()] = value.strip()
            body = self.rfile.read(int(headers["content-length"]))
            received.append((path, headers, cast(dict[str, object], json.loads(body))))
            _ = self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n"
                                 + b"Connection: close\r\n\r\n{}")

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{cast(tuple[str, int], server.server_address)[1]}", received
    finally:
        server.shutdown()
        server.server_close()


def otlp_environment(endpoint: str) -> dict[str, str]:
    """Point the run at `endpoint` through the base endpoint and clear every variable that could override it."""
    return {"OTEL_EXPORTER_OTLP_ENDPOINT": endpoint, "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json", "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "",
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer%20otel-header-secret", "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "",
            "OTEL_SDK_DISABLED": "", "OTEL_TRACES_EXPORTER": "otlp", "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}


def exported_spans(received: Requests) -> list[dict[str, object]]:
    """Flatten the received spans. Each span's attributes become one name-to-value map under `attrs`."""
    spans: list[dict[str, object]] = []
    for _, _, body in received:
        for resource in cast(list[dict[str, object]], body["resourceSpans"]):
            for scope in cast(list[dict[str, object]], resource["scopeSpans"]):
                for span in cast(list[dict[str, object]], scope["spans"]):
                    attributes = cast(list[dict[str, dict[str, object]]], span["attributes"])
                    spans.append(span | {"attrs": {item["key"]: next(iter(item["value"].values())) for item in attributes}})
    return spans


@pytest.mark.ac("autoimprove-observability:AC-1")
@pytest.mark.ac("autoimprove-observability:AC-2")
def test_an_otlp_endpoint_receives_the_run_phase_case_and_model_call_spans_of_a_journey_run(tmp_path: Path) -> None:
    scenario = journey_scenario(tmp_path)
    with otlp_collector() as (endpoint, received):
        _ = run_scenario(scenario, tmp_path, harness="claude", mode="journey", env_overrides=otlp_environment(endpoint))
    assert received and {path for path, _, _ in received} == {"/v1/traces"}
    assert all(headers["authorization"] == "Bearer otel-header-secret" for _, headers, _ in received)
    spans = exported_spans(received)
    attrs = [cast(dict[str, object], span["attrs"]) for span in spans]
    record = read_record(scenario.out)
    (run,) = [span for span in spans if span["name"] == "skillz.run"]
    assert {span["traceId"] for span in spans} == {run["traceId"]}
    assert cast(dict[str, object], run["attrs"])["skillz.run_id"] == record["run_id"]
    phases = {cast(dict[str, object], span["attrs"])["skillz.phase"]: span for span in spans if span["name"] == "skillz.phase"}
    assert set(phases) == {"preflight", "cases", "search", "holdout", "replay"}
    assert all(span["parentSpanId"] == run["spanId"] for span in phases.values())
    search = cast(dict[str, object], phases["search"]["attrs"])
    assert search["skillz.stop_trigger"] == cast(dict[str, object], record["search"])["stop_trigger"]
    cases = [span for span in spans if span["name"] == "skillz.case"]
    scored = [item for item in cast(list[dict[str, object]], record["outcomes"]) if item.get("case_id") is not None]
    assert len(cases) == len(scored)
    assert {cast(dict[str, object], span["attrs"])["skillz.arm"] for span in cases} == {"baseline", "search", "winner", "replay"}
    case_ids = {span["spanId"] for span in cases}
    calls = [span for span in spans if span["name"] == "skillz.model_call"]
    by_role = {role: [cast(dict[str, object], span["attrs"]) for span in calls
                      if cast(dict[str, object], span["attrs"])["skillz.role"] == role] for role in ("task", "judge", "reflection")}
    assert all(by_role.values()), {role: len(items) for role, items in by_role.items()}
    for role, items in by_role.items():
        assert all(item["skillz.harness"] == "claude" and item["gen_ai.request.model"] == "offline-test"
                   and isinstance(item["skillz.latency_seconds"], float) and "gen_ai.usage.input_tokens" in item
                   for item in items), role
    assert all("skillz.case_id" in item and "skillz.arm" in item for item in by_role["task"] + by_role["judge"])
    assert all(isinstance(item["skillz.score"], float) and item["skillz.verdict"] == "scored" and item["skillz.criteria.levels"]
               for item in by_role["judge"])
    assert all(span["parentSpanId"] in case_ids for span in calls
               if cast(dict[str, object], span["attrs"])["skillz.role"] != "reflection")
    texts = json.dumps(attrs)
    leaks = ["otel-header-secret", "Ask which value to transform", "Answer only the agent's question", "should I transform?",
             "fake judge", "UNTRUSTED", "RUBRIC"]
    assert not [leak for leak in leaks if leak in texts]


GATE_CRITERION = {"id": "gate-run", "must": True,
                  "text": "WHEN the agent reports the result THE agent SHALL have run gate.py and shown it passing."}


def gate_scenario(base: Path) -> Scenario:
    """Return a journey scenario whose `gate-run` criterion only the task trace can show: every reply claims the run."""
    scenario = journey_scenario(base)
    draft = [item | {"files": {**cast(dict[str, str], item["files"]), "gate.py": "print('gate: 3 passed')\n"},
                     "expected": cast(dict[str, object], item["expected"]) | {"criteria": [
                         *cast(list[object], cast(dict[str, object], item["expected"])["criteria"]), GATE_CRITERION]}}
             for item in scenario.draft]
    return Scenario(scenario.target, scenario.out, scenario.export, draft, scenario.original)


@pytest.mark.ac("autoimprove-observability:AC-5")
@pytest.mark.ac("autoimprove-observability:AC-6")
def test_a_criterion_that_needs_a_gate_run_passes_only_when_the_task_trace_shows_the_run(tmp_path: Path) -> None:
    scenario = gate_scenario(tmp_path)
    result = run_scenario(scenario, tmp_path, harness="claude", mode="journey")
    assert_promoted(scenario, result, kinds=frozenset({"journey"}))
    held = [item for item in holdout(read_record(scenario.out))]
    met = {arm: {cast(dict[str, dict[str, object]], item["criteria"])["gate-run"]["met"] for item in held
                 if item["arm"] == arm and item.get("criteria")} for arm in ("baseline", "winner")}
    assert met == {"baseline": {False}, "winner": {True}}, met
    log = read_events(scenario.out)
    judged = [untrusted(cast(str, entry["prompt"])) for entry in log if entry["role"] == "judge"]
    traces = [cast(list[dict[str, object]], data["trace"]) for data in judged]
    assert judged and len(traces) == len(judged)
    assert all("`trace` field lists the task's messages and tool calls" in cast(str, entry["prompt"])
               for entry in log if entry["role"] == "judge")
    shown = [step for trace in traces for step in trace if "gate.py" in json.dumps(step.get("input"))]
    assert shown and all(str(step["output"]).strip() == "gate: 3 passed" and step["turn"] == 2 and step["error"] is False
                         for step in shown)
    assert all(any(step.get("text") == f"Which {topic} should I transform?" for step in trace for topic in ("code", "label"))
               for trace in traces)
    assert not any("trace" in untrusted(cast(str, entry["prompt"])) for entry in log if entry["role"] == "simulator")


def untrusted(prompt: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(prompt.partition("\nUNTRUSTED DATA (JSON):\n")[2]))

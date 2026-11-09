"""The OpenTelemetry trace of a run: spans through an in-memory exporter, and the OTLP environment and exporter."""
from __future__ import annotations

import json
import socket
from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast, final

import pytest
from typing_extensions import override

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments.evaluation import CheckMemo
from skillz_experiments.run._otlp import OtlpJson, Span, encode, exporter_from_environment
from skillz_experiments.run._state import Provider
from skillz_experiments.run._telemetry import Telemetry
from fake_provider import FakeRunProvider

RECORD: dict[str, object] = {
    "run_id": "run-1", "phase": "prepared", "contract": {"skill": "cook"},
    "harness": {"task": {"adapter": "codex", "model": "task-model"},
                "reflection": {"adapter": "codex", "model": "task-model"},
                "judge": {"adapter": "claude", "model": "judge-model"}}}
PROMPT = "REFLECT ON THIS SECRET PROMPT"
TRANSCRIPT = [{"role": "user", "text": "/cook spec.md"}, {"role": "assistant", "text": "a private answer"}]
SCORED: dict[str, object] = {
    "score": 0.75, "scores": {"judge": 0.75}, "loaded": True, "latency_seconds": 2.0, "judge_latency_seconds": 0.5,
    "task_usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 7},
    "judge_usage": {"input_tokens": 30, "cached_input_tokens": 0, "output_tokens": 3},
    "simulator_usage": {"input_tokens": 11, "cached_input_tokens": 0, "output_tokens": 2},
    "criteria": {"gate-run": {"level": 4, "reason": "a judge reason", "met": True},
                 "menu": {"level": 2, "reason": "another judge reason", "met": False}},
    "transcript": TRANSCRIPT}


class Memory:
    """An in-memory exporter: it keeps every exported batch."""

    def __init__(self) -> None:
        self.batches: list[list[Span]] = []

    def export(self, spans: Sequence[Span]) -> None:
        self.batches.append(list(spans))

    @property
    def spans(self) -> list[Span]:
        return [span for batch in self.batches for span in batch]


@final
class Scripted(FakeRunProvider):
    def __init__(self, result: dict[str, object] | Exception) -> None:
        super().__init__()
        self.result: dict[str, object] | Exception = result
        self.prompts: list[str] = []
        self.closed: bool = False

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False, checks: CheckMemo) -> dict[str, object]:
        del candidate, case, holdout, checks
        if isinstance(self.result, Exception):
            raise self.result
        return dict(self.result)

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del candidate, case, holdout, schema
        self.prompts.append(prompt)
        if isinstance(self.result, Exception):
            raise self.result
        return {"answer": {"SKILL.md": PROMPT}, "usage": {"input_tokens": 9, "cached_input_tokens": 0, "output_tokens": 4},
                "latency_seconds": 1.5}

    @override
    def close(self) -> None:
        self.closed = True


CASE = cast(Case, cast(object, SimpleNamespace(identifier="cook-press-1", split="train")))
CANDIDATE = cast(Candidate, cast(object, SimpleNamespace(identity="c" * 64)))


def _named(spans: list[Span], name: str) -> list[Span]:
    return [span for span in spans if span.name == name]


def _texts(spans: list[Span]) -> str:
    return json.dumps([span.attributes for span in spans] + [span.error for span in spans])


@pytest.mark.ac("autoimprove-observability:AC-1")
@pytest.mark.ac("autoimprove-observability:AC-2")
def test_a_scored_case_records_a_case_span_with_task_and_judge_call_spans_under_its_phase() -> None:
    memory = Memory()
    telemetry = Telemetry(memory, RECORD)
    telemetry.phase("cases")
    result = telemetry.evaluate(Scripted(SCORED), CANDIDATE, CASE, "search", holdout=False, checks=CheckMemo())
    telemetry.finish()
    assert result["transcript"] == TRANSCRIPT
    spans = memory.spans
    (run,) = _named(spans, "skillz.run")
    (phase,) = _named(spans, "skillz.phase")
    (case,) = _named(spans, "skillz.case")
    calls = {span.attributes["skillz.role"]: span for span in _named(spans, "skillz.model_call")}
    assert {span.trace_id for span in spans} == {run.trace_id}
    assert (phase.parent_id, case.parent_id) == (run.span_id, phase.span_id)
    assert {span.parent_id for span in calls.values()} == {case.span_id}
    assert case.attributes == {
        "skillz.case_id": "cook-press-1", "skillz.arm": "search", "skillz.split": "train", "skillz.candidate_hash": "c" * 64,
        "skillz.verdict": "scored", "skillz.score": 0.75, "skillz.criteria.levels": ["gate-run=4", "menu=2"],
        "skillz.criteria.met": 1, "skillz.criteria.total": 2}
    task, judge = calls["task"], calls["judge"]
    assert (task.attributes["skillz.harness"], task.attributes["gen_ai.request.model"]) == ("codex", "task-model")
    assert (judge.attributes["skillz.harness"], judge.attributes["gen_ai.request.model"]) == ("claude", "judge-model")
    assert (task.attributes["gen_ai.usage.input_tokens"], task.attributes["skillz.usage.cached_input_tokens"],
            task.attributes["gen_ai.usage.output_tokens"], task.attributes["skillz.simulator.input_tokens"]) == (100, 40, 7, 11)
    assert task.attributes["skillz.latency_seconds"] == 2.0 and judge.attributes["skillz.latency_seconds"] == 0.5
    assert (judge.attributes["skillz.score"], judge.attributes["skillz.verdict"], judge.attributes["gen_ai.usage.input_tokens"]) == (
        0.75, "scored", 30)
    assert case.start == task.start <= task.end <= case.end and case.start <= judge.start <= judge.end == case.end
    assert all(span.end >= span.start > 0 for span in spans)
    texts = _texts(spans)
    assert not any(secret in texts for secret in ("a judge reason", "/cook spec.md", "a private answer"))


@pytest.mark.ac("autoimprove-observability:AC-1")
def test_a_failed_case_records_its_error_code_and_no_call_span() -> None:
    memory = Memory()
    telemetry = Telemetry(memory, RECORD)
    with pytest.raises(CodedError):
        _ = telemetry.evaluate(Scripted(CodedError("journey-unsupported", "the model said a private thing")), CANDIDATE, CASE,
                               "baseline", holdout=True, checks=CheckMemo())
    telemetry.flush()
    (case,) = memory.spans
    assert (case.name, case.error, case.parent_id) == ("skillz.case", "journey-unsupported", telemetry._run.span_id)  # pyright: ignore[reportPrivateUsage]
    assert "private thing" not in _texts(memory.spans)


@pytest.mark.ac("autoimprove-observability:AC-1")
@pytest.mark.ac("autoimprove-observability:AC-2")
def test_the_wrapped_provider_traces_a_reflection_and_ends_the_run_span_when_it_closes() -> None:
    memory = Memory()
    record = dict(RECORD)
    telemetry = Telemetry(memory, record)
    inner = Scripted(SCORED)
    provider = telemetry.wrap(inner)
    telemetry.phase("search")
    answer = provider.invoke(PROMPT, schema={"type": "object"})
    telemetry.annotate(stop_trigger="stall", stop_reason="converged-stalled")
    record |= {"phase": "searched", "failure_code": "search-failed", "gate": {"verdict": "promote"}}
    provider.close()
    assert inner.closed and inner.prompts == [PROMPT] and answer["latency_seconds"] == 1.5
    (call,) = _named(memory.spans, "skillz.model_call")
    (phase,) = _named(memory.spans, "skillz.phase")
    (run,) = _named(memory.spans, "skillz.run")
    assert call.parent_id == phase.span_id and call.attributes == {
        "skillz.role": "reflection", "skillz.harness": "codex", "gen_ai.request.model": "task-model",
        "skillz.prompt_chars": len(PROMPT), "gen_ai.usage.input_tokens": 9, "skillz.usage.cached_input_tokens": 0,
        "gen_ai.usage.output_tokens": 4, "skillz.latency_seconds": 1.5}
    assert (phase.attributes["skillz.stop_trigger"], phase.attributes["skillz.stop_reason"]) == ("stall", "converged-stalled")
    assert (run.attributes["skillz.phase.start"], run.attributes["skillz.phase.end"], run.attributes["skillz.gate.verdict"],
            run.attributes["skillz.skill"], run.error) == ("prepared", "searched", "promote", "cook", "search-failed")
    assert PROMPT not in _texts(memory.spans)


@pytest.mark.ac("autoimprove-observability:AC-1")
def test_a_failed_reflection_records_its_error_and_the_run_level_fields_name_the_roles() -> None:
    memory = Memory()
    telemetry = Telemetry(memory, {"adapter": "claude", "model": "m", "judge_model": "j"})
    provider = telemetry.wrap(Scripted(RuntimeError("down")))
    with pytest.raises(RuntimeError):
        _ = provider.invoke(PROMPT)
    telemetry.flush()
    (call,) = memory.spans
    assert (call.error, call.attributes["skillz.harness"], call.attributes["gen_ai.request.model"]) == ("RuntimeError", "claude", "m")
    assert telemetry._roles["simulator"] == ("claude", "j")  # pyright: ignore[reportPrivateUsage]


@pytest.mark.ac("autoimprove-observability:AC-3")
def test_without_an_exporter_the_telemetry_exports_nothing_and_leaves_the_provider_unwrapped() -> None:
    telemetry = Telemetry(None, RECORD)
    inner = Scripted(SCORED)
    assert telemetry.wrap(inner) is inner
    telemetry.phase("cases")
    telemetry.annotate(stop_trigger="stall")
    assert telemetry.evaluate(inner, CANDIDATE, CASE, "search", holdout=False, checks=CheckMemo())["score"] == 0.75
    telemetry.finish()


@pytest.mark.ac("autoimprove-observability:AC-3")
@pytest.mark.parametrize("environ", [
    {}, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318", "OTEL_SDK_DISABLED": "TRUE"},
    {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318", "OTEL_TRACES_EXPORTER": "console, none"}])
def test_the_environment_turns_the_export_off(environ: dict[str, str]) -> None:
    warnings: list[str] = []
    assert exporter_from_environment(environ, warnings.append) is None and warnings == []


@pytest.mark.ac("autoimprove-observability:AC-1")
def test_the_environment_configures_the_traces_endpoint_headers_timeout_and_resource() -> None:
    base = exporter_from_environment({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318/",
                                      "OTEL_EXPORTER_OTLP_HEADERS": "a=1,bad, x-key = v%20w ",
                                      "OTEL_EXPORTER_OTLP_TIMEOUT": "2500", "OTEL_SERVICE_NAME": "svc",
                                      "OTEL_RESOURCE_ATTRIBUTES": "team=evals,service.name=ignored"})
    assert base is not None and base.endpoint == "http://collector:4318/v1/traces"
    assert (base._headers, base._timeout, base._resource) == (  # pyright: ignore[reportPrivateUsage]
        {"a": "1", "x-key": "v w"}, 2.5, {"team": "evals", "service.name": "svc"})
    signal = exporter_from_environment({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318",
                                        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://traces.example/v1/x",
                                        "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc", "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
                                        "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT": "soon"})
    assert signal is not None and signal.endpoint == "https://traces.example/v1/x"
    assert (signal._timeout, signal._resource) == (10.0, {"service.name": "skillz-experiment"})  # pyright: ignore[reportPrivateUsage]
    negative = exporter_from_environment({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://c", "OTEL_EXPORTER_OTLP_TIMEOUT": "-1"})
    assert negative is not None and negative._timeout == 10.0  # pyright: ignore[reportPrivateUsage]


@pytest.mark.ac("autoimprove-observability:AC-4")
@pytest.mark.parametrize("environ, needle", [
    ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4317", "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}, "'grpc' is not supported"),
    ({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "file:///tmp/spans"}, "must be an http or https URL")])
def test_an_unsupported_protocol_or_scheme_warns_once_and_turns_the_export_off(environ: dict[str, str], needle: str) -> None:
    warnings: list[str] = []
    assert exporter_from_environment(environ, warnings.append) is None
    assert len(warnings) == 1 and needle in warnings[0]


@pytest.mark.ac("autoimprove-observability:AC-4")
@pytest.mark.parametrize("endpoint", ["http://[::1", "https://user:secret@[::1/path?token=hidden"])
def test_a_malformed_endpoint_warns_once_without_secrets(endpoint: str) -> None:
    warnings: list[str] = []
    assert exporter_from_environment({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint}, warnings.append) is None
    assert warnings == ["the OTLP traces endpoint must be an http or https URL; the run continues without a trace export"]


@pytest.mark.ac("autoimprove-observability:AC-4")
@pytest.mark.parametrize("setting", ["inf", "1e309", "1e300"])
def test_an_unsupported_timeout_uses_the_default(setting: str) -> None:
    exporter = exporter_from_environment({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318",
                                          "OTEL_EXPORTER_OTLP_TIMEOUT": setting})
    assert exporter is not None and exporter._timeout == 10.0  # pyright: ignore[reportPrivateUsage]


@pytest.mark.ac("autoimprove-observability:AC-4")
def test_a_failed_export_warns_once_without_the_user_part_or_a_header_value() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = cast(tuple[str, int], probe.getsockname())[1]
    warnings: list[str] = []
    exporter = OtlpJson(f"http://user:pw@127.0.0.1:{port}/v1/traces?token=q", {"Authorization": "Bearer header-secret"}, 2.0,
                        {"service.name": "s"}, warnings.append)
    span = Span("skillz.run", "a" * 32, "b" * 16, None, 1, 2)
    exporter.export([span])
    exporter.export([span])
    assert len(warnings) == 1, warnings
    assert f"http://127.0.0.1:{port}/v1/traces failed (URLError)" in warnings[0]
    assert not any(secret in warnings[0] for secret in ("user", "pw", "token", "header-secret"))


@pytest.mark.ac("autoimprove-observability:AC-1")
def test_the_otlp_body_encodes_each_attribute_type_ids_parents_and_status() -> None:
    child = Span("skillz.case", "a" * 32, "c" * 16, "b" * 16, 5, 9,
                 {"s": "x", "i": 3, "f": 0.5, "b": True, "l": ["p=4"]}, error="gate-failed")
    body = cast(dict[str, list[dict[str, object]]], json.loads(encode([child, Span("root", "a" * 32, "b" * 16, None, 1, 10)],
                                                                      {"service.name": "svc"})))
    (resource,) = body["resourceSpans"]
    assert resource["resource"] == {"attributes": [{"key": "service.name", "value": {"stringValue": "svc"}}]}
    (scope,) = cast(list[dict[str, object]], resource["scopeSpans"])
    first, second = cast(list[dict[str, object]], scope["spans"])
    assert first == {"traceId": "a" * 32, "spanId": "c" * 16, "parentSpanId": "b" * 16, "name": "skillz.case", "kind": 1,
                     "startTimeUnixNano": "5", "endTimeUnixNano": "9", "attributes": [
                         {"key": "s", "value": {"stringValue": "x"}}, {"key": "i", "value": {"intValue": "3"}},
                         {"key": "f", "value": {"doubleValue": 0.5}}, {"key": "b", "value": {"boolValue": True}},
                         {"key": "l", "value": {"arrayValue": {"values": [{"stringValue": "p=4"}]}}}],
                     "status": {"code": 2, "message": "gate-failed"}}
    assert "parentSpanId" not in second and second["status"] == {"code": 0}


def test_a_full_pending_buffer_exports_before_the_phase_ends() -> None:
    memory = Memory()
    telemetry = Telemetry(memory, RECORD)
    provider = cast(Provider, Scripted(SCORED))
    for _ in range(90):
        _ = telemetry.evaluate(provider, CANDIDATE, CASE, "search", holdout=False, checks=CheckMemo())
    assert len(memory.batches) == 1 and len(memory.batches[0]) == 258


@pytest.mark.ac("autoimprove-observability:AC-4")
def test_the_default_warning_goes_to_stderr_with_the_runner_prefix(capsys: pytest.CaptureFixture[str]) -> None:
    assert exporter_from_environment({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://c:4317", "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}) is None
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("skillz-experiment: the OTLP protocol 'grpc' is not supported")


def test_the_wrapped_provider_passes_a_task_invoke_and_the_checks_through_untraced() -> None:
    memory = Memory()
    inner = Scripted(SCORED)
    provider = Telemetry(memory, RECORD).wrap(inner)
    _ = provider.invoke(PROMPT, CANDIDATE, CASE)
    provider.check_name(CANDIDATE)
    assert provider.check_candidate(CANDIDATE).passed and provider.preflight() == {"isolation": "test-provider"}
    assert inner.prompts == [PROMPT] and memory.spans == []

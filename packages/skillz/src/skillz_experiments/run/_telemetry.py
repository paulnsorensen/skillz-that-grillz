"""The OpenTelemetry trace of a run: one span for the run, one per phase, one per case, and one per model call.

A run opts in through the standard OTLP exporter variables (`_otlp.py`). Without them every method is a no-op and
the provider stays unwrapped. A case span holds a `task` call span and, when the judge ran, a `judge` call span.
The task span covers every turn of a journey; its simulator token counts sit on it as `skillz.simulator.*`.
The evaluator reports the task and judge latencies but not their start times, so the task span starts with the case
and the judge span ends with it. Span attributes hold identifiers, timings, token counts, scores, statuses, and
criterion levels. They never hold a prompt, a transcript, a judge reason, a file, or a credential.
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Mapping
from typing import cast, final

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments.evaluation import CheckMemo, CheckResult
from skillz_experiments.run._otlp import Attribute, Exporter, Span, exporter_from_environment
from skillz_experiments.run._state import Provider

FLUSH_SPANS = 256
USAGE_FIELDS = (("input_tokens", "gen_ai.usage.input_tokens"), ("output_tokens", "gen_ai.usage.output_tokens"),
                ("cached_input_tokens", "skillz.usage.cached_input_tokens"))


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _usage(value: object, prefix: str = "") -> dict[str, Attribute]:
    counts = cast(dict[str, object], value) if isinstance(value, dict) else {}
    found: dict[str, Attribute] = {}
    for source, name in USAGE_FIELDS:
        count = counts.get(source)
        if type(count) is int:
            found[f"skillz.{prefix}.{source}" if prefix else name] = count
    return found


def _failure(error: BaseException) -> str:
    """Name a failure by its code or its type. The message can hold model output, so it stays out of the span."""
    return error.code if isinstance(error, CodedError) else type(error).__name__


def _roles(record: Mapping[str, object]) -> dict[str, tuple[str, str]]:
    """Return the (harness, model) of each role, from the frozen harness identity or the run-level fields."""
    frozen = record.get("harness")
    if isinstance(frozen, dict):
        rows = {str(name): (_text(cast(dict[str, object], entry).get("adapter")),
                            _text(cast(dict[str, object], entry).get("model")))
                for name, entry in cast(dict[str, object], frozen).items() if isinstance(entry, dict)}
    else:
        adapter, model = _text(record.get("adapter")), _text(record.get("model"))
        rows = {"task": (adapter, model), "reflection": (adapter, model),
                "judge": (adapter, _text(record.get("judge_model")) or model)}
    _ = rows.setdefault("simulator", rows.get("judge", ("", "")))
    return rows


def _verdict(result: Mapping[str, object]) -> dict[str, Attribute]:
    """Return the score, the status, and the criterion levels of one outcome. A scored outcome has no `status`."""
    found: dict[str, Attribute] = {"skillz.verdict": _text(result.get("status")) or "scored"}
    score = _number(result.get("score"))
    if score is not None:
        found["skillz.score"] = score
    criteria = result.get("criteria")
    if isinstance(criteria, dict):
        levels = {str(name): cast(dict[str, object], entry).get("level")
                  for name, entry in cast(dict[str, object], criteria).items() if isinstance(entry, dict)}
        found["skillz.criteria.levels"] = [f"{name}={level}" for name, level in sorted(levels.items())]
        found["skillz.criteria.met"] = sum(1 for level in levels.values() if level == 4)
        found["skillz.criteria.total"] = len(levels)
    return found


@final
class Telemetry:
    """Collect the spans of one run session and hand them to an exporter at each phase end and at the run end.

    `record` is the live run record. The run span reads its final phase, failure code, and gate verdict at the end.
    """

    def __init__(self, exporter: Exporter | None, record: Mapping[str, object]) -> None:
        self._exporter = exporter
        self._record = record
        self._lock = threading.Lock()
        self._pending: list[Span] = []
        self._trace = secrets.token_hex(16)
        self._roles = _roles(record)
        contract = record.get("contract")
        task = self._roles.get("task", ("", ""))
        self._run = self._open("skillz.run", None, {
            "skillz.run_id": _text(record.get("run_id")), "skillz.phase.start": _text(record.get("phase")),
            "skillz.skill": _text(cast(dict[str, object], contract).get("skill")) if isinstance(contract, dict) else "",
            "skillz.harness": task[0], "gen_ai.request.model": task[1],
            "skillz.judge_model": self._roles.get("judge", ("", ""))[1]})
        self._phase: Span | None = None

    @classmethod
    def from_environment(cls, record: Mapping[str, object]) -> Telemetry:
        return cls(exporter_from_environment(os.environ), record)

    def _open(self, name: str, parent: str | None, attributes: dict[str, Attribute], start: int | None = None) -> Span:
        return Span(name, self._trace, secrets.token_hex(8), parent, time.time_ns() if start is None else start,
                    attributes=attributes)

    def _emit(self, *spans: Span) -> None:
        with self._lock:
            self._pending.extend(spans)
            full = len(self._pending) >= FLUSH_SPANS
        if full:
            self.flush()

    def flush(self) -> None:
        """Export the finished spans. The export runs outside the lock, so other threads keep recording."""
        with self._lock:
            batch, self._pending = self._pending, []
        if batch and self._exporter is not None:
            self._exporter.export(batch)

    def wrap(self, provider: Provider) -> Provider:
        """Return `provider`, wrapped to trace reflection calls and to end the run span when it closes."""
        return provider if self._exporter is None else _Traced(provider, self)

    def phase(self, name: str) -> None:
        """End the current phase span, if any, and start the phase `name` under the run span."""
        if self._exporter is None:
            return
        with self._lock:
            previous, self._phase = self._phase, self._open("skillz.phase", self._run.span_id, {"skillz.phase": name})
        if previous is not None:
            previous.end = time.time_ns()
            self._emit(previous)
        self.flush()

    def annotate(self, **values: Attribute) -> None:
        """Add `skillz.<name>` attributes to the current phase span."""
        with self._lock:
            if self._phase is not None:
                self._phase.attributes.update({f"skillz.{name}": value for name, value in values.items()})

    def _parent(self) -> str:
        with self._lock:
            return (self._phase or self._run).span_id

    def _call(self, role: str, parent: str, start: int, end: int, attributes: dict[str, Attribute]) -> Span:
        harness, model = self._roles.get(role, ("", ""))
        span = self._open("skillz.model_call", parent, {"skillz.role": role, "skillz.harness": harness,
                                                                 "gen_ai.request.model": model, **attributes}, start)
        span.end = max(start, end)
        return span

    def evaluate(self, provider: Provider, candidate: Candidate, case: Case, arm: str, *, holdout: bool,
                 checks: CheckMemo) -> dict[str, object]:
        """Score one case through `provider` and record its case span and model call spans."""
        if self._exporter is None:
            return provider.evaluate(candidate, case, holdout=holdout, checks=checks)
        start = time.time_ns()
        base: dict[str, Attribute] = {"skillz.case_id": case.identifier, "skillz.arm": arm, "skillz.split": case.split,
                                      "skillz.candidate_hash": candidate.identity}
        outer = self._open("skillz.case", self._parent(), dict(base), start)
        try:
            result = provider.evaluate(candidate, case, holdout=holdout, checks=checks)
        except BaseException as error:
            outer.end, outer.error = time.time_ns(), _failure(error)
            self._emit(outer)
            raise
        outer.end = time.time_ns()
        outer.attributes.update(_verdict(result))
        spans = [outer]
        latency = _number(result.get("latency_seconds"))
        if latency is not None:
            task = base | {"skillz.latency_seconds": latency, **_usage(result.get("task_usage", result.get("usage"))),
                           **_usage(result.get("simulator_usage"), "simulator")}
            spans.append(self._call("task", outer.span_id, start, min(outer.end, start + int(latency * 1e9)), task))
        judged = _number(result.get("judge_latency_seconds"))
        if judged is not None:
            scores = result.get("scores")
            judge = _number(cast(dict[str, object], scores).get("judge")) if isinstance(scores, dict) else None
            verdict = _verdict(result) | ({"skillz.score": judge} if judge is not None else {})
            spans.append(self._call("judge", outer.span_id, max(start, outer.end - int(judged * 1e9)), outer.end,
                                    base | {"skillz.latency_seconds": judged, **_usage(result.get("judge_usage")), **verdict}))
        self._emit(*spans)
        return result

    def reflect(self, provider: Provider, prompt: str, schema: dict[str, object] | None,
                holdout: bool) -> dict[str, object]:
        """Run one reflection call and record its span. The span holds the prompt length, not the prompt."""
        start = time.time_ns()
        parent = self._parent()
        attributes: dict[str, Attribute] = {"skillz.prompt_chars": len(prompt)}
        try:
            result = provider.invoke(prompt, holdout=holdout, schema=schema)
        except BaseException as error:
            span = self._call("reflection", parent, start, time.time_ns(), attributes)
            span.error = _failure(error)
            self._emit(span)
            raise
        latency = _number(result.get("latency_seconds"))
        attributes |= _usage(result.get("usage")) | ({"skillz.latency_seconds": latency} if latency is not None else {})
        self._emit(self._call("reflection", parent, start, time.time_ns(), attributes))
        return result

    def finish(self) -> None:
        """End the phase and run spans with the final phase, failure code, and gate verdict, then export."""
        record = self._record
        with self._lock:
            phase, self._phase = self._phase, None
        now = time.time_ns()
        if phase is not None:
            phase.end = now
            self._emit(phase)
        gate = record.get("gate")
        self._run.end = now
        self._run.attributes |= {"skillz.phase.end": _text(record.get("phase")),
                                 "skillz.gate.verdict": _text(cast(dict[str, object], gate).get("verdict"))
                                 if isinstance(gate, dict) else ""}
        code = record.get("failure_code")
        if isinstance(code, str):
            self._run.error = code
        self._emit(self._run)
        self.flush()


@final
class _Traced:
    """A provider that traces its reflection calls and ends the run span when it closes."""

    def __init__(self, inner: Provider, telemetry: Telemetry) -> None:
        self._inner = inner
        self._telemetry = telemetry
        self.run_facts: dict[str, object] = inner.run_facts

    def preflight(self, recorded: dict[str, object] | None = None) -> dict[str, object]:
        """Run the inner preflight, then show the run facts that it sets."""
        evidence = self._inner.preflight(recorded)
        self.run_facts = self._inner.run_facts
        return evidence

    def close(self) -> None:
        try:
            self._inner.close()
        finally:
            self._telemetry.finish()

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False, checks: CheckMemo) -> dict[str, object]:
        return self._inner.evaluate(candidate, case, holdout=holdout, checks=checks)

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        if candidate is not None or case is not None:
            return self._inner.invoke(prompt, candidate, case, holdout=holdout, schema=schema)
        return self._telemetry.reflect(self._inner, prompt, schema, holdout)

    def check_name(self, candidate: Candidate) -> None:
        self._inner.check_name(candidate)

    def check_candidate(self, candidate: Candidate) -> CheckResult:
        return self._inner.check_candidate(candidate)

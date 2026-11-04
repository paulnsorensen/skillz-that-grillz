"""Reduce one stored outcome to the fields that `export` reports."""
from __future__ import annotations

from typing import cast

from skillz_experiments._cases import mapping
from skillz_experiments._redact import redact


def export_outcome(item: dict[str, object]) -> dict[str, object]:
    """Return the allowlist of outcome fields that leave run.json, with `gate_diagnostic` and the criteria reasons redacted."""
    allowed = {"arm", "split", "case_id", "repeat", "score", "status", "reason", "loaded", "helper_executed", "candidate_hash",
               "case_hash", "latency_seconds", "judge_latency_seconds", "evidence_valid", "matched", "false_positives",
               "false_negatives", "precision", "recall", "detection_f1", "severity_accuracy", "actionability_rate", "scores",
               "gate_fault"}
    result = {key: value for key, value in item.items() if key in allowed}
    for key in ("usage", "task_usage", "judge_usage"):
        if key in item:
            value = item[key]
            result[key] = None if value is None else {
                name: mapping(value).get(name) for name in ("input_tokens", "cached_input_tokens", "output_tokens")}
    if isinstance(item.get("gate_diagnostic"), str):
        result["gate_diagnostic"] = redact(cast(str, item["gate_diagnostic"]))
    criteria = item.get("criteria")
    if isinstance(criteria, dict):
        verdicts = cast(dict[str, object], criteria)
        result["criteria"] = {name: _export_verdict(cast(dict[str, object], verdict))
                              for name, verdict in verdicts.items() if isinstance(verdict, dict)}
    return result


def _export_verdict(verdict: dict[str, object]) -> dict[str, object]:
    """Return the level, the redacted reason, and the stored `met` flag of one criterion verdict."""
    exported: dict[str, object] = {"level": verdict.get("level"), "reason": redact(str(verdict.get("reason", "")))}
    if isinstance(verdict.get("met"), bool):
        exported["met"] = verdict["met"]
    return exported

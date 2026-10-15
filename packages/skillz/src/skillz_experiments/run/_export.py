"""Summarize and export a finished run. `export` stays write-only.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from skillz_experiments._audit import identity as judge_identity
from skillz_experiments._cases import Case, CodedError, load_cases, mapping
from skillz_experiments._contract import Contract, parse
from skillz_experiments._new_cli import unused_helpers
from skillz_experiments._records import open_record, write
from skillz_experiments.harness import stop_kind
from skillz_experiments.run._claims import claims
from skillz_experiments.run._patch import build_patch
from skillz_experiments.run._plan import case_calls
from skillz_experiments.run._session import engine_hash, field, is_inside, record_outcomes, text_field

_TOKEN_FIELDS = {"input_tokens": "input_tokens", "cached_tokens": "cached_input_tokens", "output_tokens": "output_tokens"}


def _number(value: object) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _total(values: list[int | float | None]) -> int | float | None:
    """Return the sum of the numbers in `values`, or None when it holds none."""
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _model(record: dict[str, object], role: str) -> str | None:
    """Return the model that `role` runs on. A run without a simulator role simulates on the judge model.

    The frozen harness identity names the model of each role. A run without one falls back to the record fields.
    """
    harness = record.get("harness")
    entry = cast(dict[str, object], harness).get(role) if isinstance(harness, dict) else None
    named = cast(dict[str, object], entry).get("model") if isinstance(entry, dict) else None
    if isinstance(named, str):
        return named
    if role == "simulator":
        return _model(record, "judge")
    candidates = ([record.get("judge_model"), record.get("judge_model_flag"), record.get("model")] if role == "judge"
                  else [record.get("model")])
    return next((item for item in candidates if isinstance(item, str)), None)


def usage_by_role(record: dict[str, object]) -> dict[str, object]:
    """Sum the recorded spend of each role over the outcomes that carry its usage key.

    An exact-json outcome has no `task_usage`, so its `usage` counts as task usage. A token or latency field is
    None when no outcome of the role carries a number for it. A role without outcomes has no model.
    """
    found: dict[str, list[tuple[object, object]]] = {"task": [], "reflection": [], "judge": [], "simulator": []}
    for item in record_outcomes(record):
        if item.get("split") == "reflection":
            if isinstance(item.get("usage"), dict):
                found["reflection"].append((item["usage"], item.get("latency_seconds")))
            continue
        if item.get("status") == "candidate-contract-rejected":
            continue
        task_key = "task_usage" if "task_usage" in item else "usage"
        if isinstance(item.get(task_key), dict):
            found["task"].append((item[task_key], item.get("latency_seconds")))
        if isinstance(item.get("judge_usage"), dict):
            found["judge"].append((item["judge_usage"], item.get("judge_latency_seconds")))
        if isinstance(item.get("simulator_usage"), dict):
            found["simulator"].append((item["simulator_usage"], None))
    result: dict[str, object] = {}
    for role, entries in found.items():
        report: dict[str, object] = {"outcomes": len(entries)}
        for name, key in _TOKEN_FIELDS.items():
            report[name] = _total([_number(cast(dict[str, object], usage).get(key))
                                   for usage, _ in entries if isinstance(usage, dict)])
        report["latency_seconds"] = _total([_number(latency) for _, latency in entries])
        report["model"] = _model(record, role) if entries else None
        result[role] = report
    return result


def _frozen(record: dict[str, object], out: Path) -> tuple[Contract, list[Case]]:
    """Return the frozen contract of the record and the cases that `out` holds."""
    contract = parse(field(record, "contract"), text_field(record, "contract_source"))
    return contract, load_cases(out / "cases.json", contract.grader_types())


def calls_upper_bound(record: dict[str, object], frozen: tuple[Contract, list[Case]] | None) -> int | None:
    """Return the planned worst-case call count: the recorded preflight claims, one per reflection, `case_calls` per evaluation.

    `frozen` holds the contract and the cases of the run.
    A record without an integer `preflight_calls` predates the count, so the bound is unknown and this returns None.
    A `candidate-contract-rejected` outcome made no call, so it adds nothing.
    """
    preflight = record.get("preflight_calls")
    if type(preflight) is not int or frozen is None:
        return None
    contract, loaded = frozen
    cases = {case.identifier: case for case in loaded}
    total = preflight
    for item in record_outcomes(record):
        case = cases.get(str(item.get("case_id")))
        if item.get("split") == "reflection":
            total += 1
        elif case is not None and item.get("status") != "candidate-contract-rejected":
            total += case_calls(contract, case)
    return total


def summary(record: dict[str, object], out: Path | None = None) -> dict[str, object]:
    """Return the run summary. With `out`, it reconciles the budget counter with the planned worst-case call count.

    A completed run with `out` also carries `claims`: the runner, export, and improvement claims, each with its own status.
    """
    keys = ("schema_version", "phase", "calls", "model", "adapter", "edit", "codex_version", "harness", "judge",
            "gate", "search", "replay", "estimate", "contract_hash", "contract_source", "claude_options", "compatibility",
            "statistics")
    result = {key: record.get(key) for key in keys}
    result["usage_by_role"] = usage_by_role(record)
    if out is not None:
        # Parse the contract and load the cases once. A record without a preflight count or a finished phase needs neither.
        frozen = (_frozen(record, out) if type(record.get("preflight_calls")) is int or record.get("phase") == "complete"
                  else None)
        bound = calls_upper_bound(record, frozen)
        result["calls_upper_bound"] = bound
        calls = record.get("calls")
        if bound is not None and isinstance(calls, int) and calls > bound:
            result["usage_gap"] = calls - bound
        if record.get("phase") == "complete" and frozen is not None:
            result["claims"] = claims(record, frozen[1], bound)
    if "close_warning" in record:
        code = mapping(record["close_warning"]).get("code")
        hint = "no action needed" if stop_kind(code) == "notice" else "log in again before the next run"
        result["close_warning"] = {"code": code, "hint": hint}
    return result


def _export_outcome(item: dict[str, object]) -> dict[str, object]:
    allowed = {"arm", "split", "case_id", "repeat", "score", "status", "loaded", "helper_executed", "candidate_hash",
               "case_hash", "latency_seconds", "judge_latency_seconds", "evidence_valid", "matched", "false_positives",
               "false_negatives", "precision", "recall", "detection_f1", "severity_accuracy", "actionability_rate", "scores"}
    result = {key: value for key, value in item.items() if key in allowed}
    for key in ("usage", "task_usage", "judge_usage"):
        if key in item:
            value = item[key]
            result[key] = None if value is None else {
                name: mapping(value).get(name) for name in ("input_tokens", "cached_input_tokens", "output_tokens")}
    return result


def _private_file(path: Path, text: str) -> None:
    """Create the new file `path` with mode 0600 and write `text` to it. Fail when `path` exists."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        _ = stream.write(text)


def export(out: Path, destination: Path) -> dict[str, object]:
    """Write the winner as a private patch and a redacted report. Never apply it.

    The patch comes from the builder that the replay uses. A patch that differs from the replayed one is refused.
    """
    record = open_record(out / "run.json")
    if record.get("phase") != "complete":
        raise ValueError("export requires a completed run")
    if record.get("engine_hash") != engine_hash():
        raise ValueError("evaluator differs from the frozen record; create a new run")
    if "judge" in record:
        judge_model = text_field(record, "judge_model" if "judge_model" in record else "model")
        if record["judge"] != judge_identity(judge_model):
            raise ValueError("judge differs from the frozen record; create a new run")
    if not os.path.lexists(destination) and is_inside(destination, Path(text_field(record, "target_root"))):
        raise CodedError("export-into-target", "the export destination must be outside the target skill directory")
    patch = build_patch(field(record, "seed"), field(record, "winner"), Path(text_field(record, "target_root")))
    replayed = record.get("replay")
    recorded = mapping(cast(dict[str, object], replayed)).get("patch_digest") if isinstance(replayed, dict) else None
    if recorded is not None and recorded != patch.digest:
        raise ValueError("export differs from the replayed patch; create a new run")
    destination.mkdir(mode=0o700)
    for name, text in patch.files.items():
        _private_file(destination / name, text)
    report = summary(record, out) | {"sharing": "private-local-only",
                                "outcomes": [_export_outcome(item) for item in record_outcomes(record)], "cost_usd": None}
    if patch.cli is not None:
        report["new_cli"] = {"target": patch.cli.name, "package": patch.cli.package,
                             "unused_helpers": unused_helpers(patch.seed, patch.candidate)}
    write(destination / "report.json", report)
    return {"export": str(destination), "sharing": "private-local-only", "installed": False, "patches": list(patch.files)}

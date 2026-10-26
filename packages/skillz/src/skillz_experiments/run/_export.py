"""Summarize and export a finished run. `export` stays write-only.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from skillz_experiments._audit import identity as judge_identity
from skillz_experiments._cases import MET_LEVEL, Case, CodedError, load_cases, mapping
from skillz_experiments._contract import Contract, parse
from skillz_experiments._new_cli import unused_helpers
from skillz_experiments._redact import redact
from skillz_experiments._records import open_record, write
from skillz_experiments.harness import stop_kind
from skillz_experiments.run._claims import claims
from skillz_experiments.run._patch import build_patch
from skillz_experiments.run._plan import case_calls
from skillz_experiments.run._replay import STDERR_LIMIT
from skillz_experiments.run._open import engine_hash, judge_kind
from skillz_experiments.run._record_fields import field, record_outcomes, text_field

_TOKEN_FIELDS = {"input_tokens": "input_tokens", "cached_tokens": "cached_input_tokens", "output_tokens": "output_tokens"}


def is_inside(path: Path, root: Path) -> bool:
    """Return whether `path` resolves, through any symlink, to `root` or below it."""
    return path.resolve().is_relative_to(root.resolve())


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


# The replay keys that the summary shows. The `detail` key holds git and build stderr, so it stays out of the report.
REPLAY_KEYS = ("status", "reasons", "patch_digest", "cases", "mean_delta", "se", "per_case", "tolerance")


def summary(record: dict[str, object], out: Path | None = None) -> dict[str, object]:
    """Return the run summary. With `out`, it reconciles the budget counter with the planned worst-case call count.

    A completed run with `out` also carries `claims`: the runner, export, and improvement claims, each with its own status.
    """
    keys = ("schema_version", "phase", "calls", "retries", "calls_by_role", "seconds_by_role", "model", "adapter", "edit",
            "codex_version", "harness", "judge",
            "gate", "search", "replay", "estimate", "contract_hash", "contract_source", "claude_options", "compatibility",
            "statistics")
    result = {key: record.get(key) for key in keys}
    result["search"] = _export_search(record.get("search"))
    replay = record.get("replay")
    if isinstance(replay, dict):
        result["replay"] = {key: value for key, value in cast(dict[str, object], replay).items() if key in REPLAY_KEYS}
    result["usage_by_role"] = usage_by_role(record)
    if out is not None:
        # Parse the contract and load the cases once. A record without a preflight count or a finished phase needs neither.
        frozen = None
        if type(record.get("preflight_calls")) is int or record.get("phase") == "complete":
            try:
                frozen = _frozen(record, out)
            except ValueError as error:
                # A contract or case file that no longer parses leaves the bound and the claims unknown.
                result["claims_unavailable"] = f"the frozen contract or cases did not load: {str(error)[:STDERR_LIMIT]}"
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


_ATTEMPT_KEYS = ("index", "parent", "candidate", "fixed", "broke", "validation_delta", "accepted")


def _export_search(value: object) -> object:
    """Return the search record with redacted attempts and no journal.

    The hypothesis, the proposer notes, and any uncoded rejection text stay in run.json.
    """
    if not isinstance(value, dict):
        return value
    search = {key: item for key, item in cast(dict[str, object], value).items() if key != "journal"}
    if not isinstance(search.get("attempts"), list):
        return search
    rows: list[dict[str, object]] = []
    for row in cast(list[dict[str, object]], search["attempts"]):
        reason = row.get("rejected")
        redacted = {key: row.get(key) for key in _ATTEMPT_KEYS}
        coded = row.get("rejected_coded") is True and isinstance(reason, str)
        redacted["rejected"] = None if reason is None else reason if coded else "rejected"
        rows.append(redacted)
    redacted_search: dict[str, object] = search | {"attempts": rows}
    return redacted_search


def _levels(item: dict[str, object]) -> dict[str, int]:
    """Return the criterion levels (1 to 4) that the judged outcome `item` stores. Anything else yields nothing."""
    criteria = item.get("criteria")
    if not isinstance(criteria, dict):
        return {}
    found: dict[str, int] = {}
    for name, verdict in cast(dict[str, object], criteria).items():
        level = cast(dict[str, object], verdict).get("level") if isinstance(verdict, dict) else None
        if type(level) is int and 1 <= level <= 4:
            found[name] = level
    return found


def criteria_flags(outcomes: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return the noisy criteria, sorted by criterion, reason, case, and arm. A criterion id belongs to one case.

    A `spread` flag marks a criterion whose levels span 2 or more across the repeats of one candidate on one arm and case.
    Two search candidates are not repeats: their levels can differ because the candidates differ.
    A `partly-met` flag marks a criterion that gets level 2 in at least half of its outcomes, over all arms.
    An outcome without `criteria` adds nothing.
    """
    grouped: dict[tuple[str, str, str, str], list[int]] = {}
    totals: dict[tuple[str, str], list[int]] = {}
    for item in outcomes:
        case = str(item.get("case_id"))
        candidate = str(item.get("search_candidate", item.get("candidate_hash")))
        for name, level in _levels(item).items():
            grouped.setdefault((name, case, str(item.get("arm")), candidate), []).append(level)
            totals.setdefault((name, case), []).append(level)
    flags: list[dict[str, object]] = []
    for (name, case, arm, _candidate), levels in grouped.items():
        if max(levels) - min(levels) >= 2:
            flags.append({"criterion": name, "reason": "spread", "case_id": case, "arm": arm, "levels": levels})
    for (name, case), levels in totals.items():
        partly = levels.count(2)
        if partly * 2 >= len(levels):
            flags.append({"criterion": name, "reason": "partly-met", "case_id": case, "level_2": partly,
                          "outcomes": len(levels)})
    return sorted(flags, key=lambda flag: (str(flag["criterion"]), str(flag["reason"]), str(flag["case_id"]),
                                           str(flag.get("arm", ""))))


def zero_cases(outcomes: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return each judged case that scores 0 on every repeat of one candidate on one arm, with its unmet verdicts.

    The repeats of a case are its judged outcomes for one candidate on one arm, as in `criteria_flags`.
    Each entry lists, per criterion, the level and the redacted reason of each unmet verdict, in repeat order.
    An outcome without `criteria` adds nothing. The entries sort by case, arm, and candidate.
    """
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for item in outcomes:
        if isinstance(item.get("criteria"), dict):
            candidate = str(item.get("search_candidate", item.get("candidate_hash")))
            grouped.setdefault((str(item.get("case_id")), str(item.get("arm")), candidate), []).append(item)
    entries: list[dict[str, object]] = []
    for (case, arm, candidate), items in sorted(grouped.items()):
        if not all(type(item.get("score")) in (int, float) and item["score"] == 0 for item in items):
            continue
        unmet: dict[str, list[dict[str, object]]] = {}
        for item in items:
            for name, verdict in cast(dict[str, object], item["criteria"]).items():
                level = cast(dict[str, object], verdict).get("level") if isinstance(verdict, dict) else None
                if type(level) is int and level < MET_LEVEL:
                    reason = redact(str(cast(dict[str, object], verdict).get("reason", "")))
                    unmet.setdefault(name, []).append({"level": level, "met": False, "reason": reason})
        entries.append({"case_id": case, "arm": arm, "candidate": candidate, "repeats": len(items), "criteria": unmet})
    return entries


def _export_outcome(item: dict[str, object]) -> dict[str, object]:
    allowed = {"arm", "split", "case_id", "repeat", "score", "status", "reason", "loaded", "helper_executed", "candidate_hash",
               "case_hash", "latency_seconds", "judge_latency_seconds", "evidence_valid", "matched", "false_positives",
               "false_negatives", "precision", "recall", "detection_f1", "severity_accuracy", "actionability_rate", "scores"}
    result = {key: value for key, value in item.items() if key in allowed}
    for key in ("usage", "task_usage", "judge_usage"):
        if key in item:
            value = item[key]
            result[key] = None if value is None else {
                name: mapping(value).get(name) for name in ("input_tokens", "cached_input_tokens", "output_tokens")}
    criteria = item.get("criteria")
    if isinstance(criteria, dict):
        verdicts = cast(dict[str, object], criteria)
        result["criteria"] = {
            name: {"level": cast(dict[str, object], verdict).get("level"),
                   "reason": redact(str(cast(dict[str, object], verdict).get("reason", "")))}
            for name, verdict in verdicts.items() if isinstance(verdict, dict)}
    return result


def _private_file(path: Path, text: str) -> None:
    """Create the new file `path` with mode 0600 and write `text` to it. Fail when `path` exists."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        _ = stream.write(text)


def export(out: Path, destination: Path) -> dict[str, object]:
    """Write the winner as a private patch and a redacted report. Never apply it.

    The patch comes from the builder that the replay uses. A patch that differs from the replayed one is refused.
    A run from an earlier engine build exports only when its replay recorded the digest of the rebuilt patch.
    Else it stops with `export-engine-unverified`.
    Export writes only a patch that changes a file. A winner whose files equal the seed gets `patches: []` and the
    reason `winner-equals-baseline`, so no empty patch reads as a change.
    """
    record = open_record(out / "run.json")
    if record.get("phase") != "complete":
        raise ValueError("export requires a completed run")
    if "judge" in record:
        judge_model = text_field(record, "judge_model" if "judge_model" in record else "model")
        contract = parse(field(record, "contract"), text_field(record, "contract_source"))
        if record["judge"] != judge_identity(judge_model, judge_kind(contract)):
            raise ValueError("judge differs from the frozen record; create a new run")
    if not os.path.lexists(destination) and is_inside(destination, Path(text_field(record, "target_root"))):
        raise CodedError("export-into-target", "the export destination must be outside the target skill directory")
    patch = build_patch(field(record, "seed"), field(record, "winner"), Path(text_field(record, "target_root")))
    replayed = record.get("replay")
    recorded = mapping(cast(dict[str, object], replayed)).get("patch_digest") if isinstance(replayed, dict) else None
    if record.get("engine_hash") != engine_hash() and recorded != patch.digest:
        raise CodedError("export-engine-unverified", "evaluator differs from the frozen record; create a new run")
    if recorded is not None and recorded != patch.digest:
        raise ValueError("export differs from the replayed patch; create a new run")
    written = {name: text for name, text in patch.files.items() if text}
    patches: dict[str, object] = {"patches": list(written)}
    if not written:
        patches["reason"] = "winner-equals-baseline"
    destination.mkdir(mode=0o700)
    for name, text in written.items():
        _private_file(destination / name, text)
    report = summary(record, out) | patches | {"sharing": "private-local-only",
                                          "outcomes": [_export_outcome(item) for item in record_outcomes(record)],
                                          "criteria_flags": criteria_flags(record_outcomes(record)),
                                          "zero_cases": zero_cases(record_outcomes(record)),
                                          "cost_usd": None}
    if patch.cli is not None:
        report["new_cli"] = {"target": patch.cli.name, "package": patch.cli.package,
                             "unused_helpers": unused_helpers(patch.seed, patch.candidate)}
    write(destination / "report.json", report)
    return {"export": str(destination), "sharing": "private-local-only", "installed": False} | patches

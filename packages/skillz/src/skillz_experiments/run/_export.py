"""Summarize and export a finished run. `export` stays write-only.
"""
from __future__ import annotations

import difflib
import os
import re
from pathlib import Path
from typing import cast

from skillz_experiments._audit import identity as judge_identity
from skillz_experiments._candidate import NEW_CLI, WEDGE_PREFIX, candidate_files, is_skill_file
from skillz_experiments._cases import CodedError, load_cases, mapping, relative, repo_root
from skillz_experiments._contract import parse
from skillz_experiments._new_cli import expand_files, parse_value, unused_helpers
from skillz_experiments._records import open_record, write
from skillz_experiments.harness import stop_kind
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


def calls_upper_bound(record: dict[str, object], out: Path) -> int | None:
    """Return the planned worst-case call count: the recorded preflight claims, one per reflection, `case_calls` per evaluation.

    A record without an integer `preflight_calls` predates the count, so the bound is unknown and this returns None.
    A `candidate-contract-rejected` outcome made no call, so it adds nothing.
    """
    preflight = record.get("preflight_calls")
    if type(preflight) is not int:
        return None
    contract = parse(field(record, "contract"), text_field(record, "contract_source"))
    cases = {case.identifier: case for case in load_cases(out / "cases.json", contract.grader_types())}
    total = preflight
    for item in record_outcomes(record):
        case = cases.get(str(item.get("case_id")))
        if item.get("split") == "reflection":
            total += 1
        elif case is not None and item.get("status") != "candidate-contract-rejected":
            total += case_calls(contract, case)
    return total


def summary(record: dict[str, object], out: Path | None = None) -> dict[str, object]:
    """Return the run summary. With `out`, it reconciles the budget counter with the planned worst-case call count."""
    keys = ("schema_version", "phase", "calls", "retries", "calls_by_role", "seconds_by_role", "model", "adapter", "edit",
            "codex_version", "harness", "judge",
            "gate", "search", "estimate", "contract_hash", "contract_source", "claude_options", "compatibility",
            "statistics")
    result = {key: record.get(key) for key in keys}
    result["search"] = _export_search(record.get("search"))
    result["usage_by_role"] = usage_by_role(record)
    if out is not None:
        bound = calls_upper_bound(record, out)
        result["calls_upper_bound"] = bound
        calls = record.get("calls")
        if bound is not None and isinstance(calls, int) and calls > bound:
            result["usage_gap"] = calls - bound
    if "close_warning" in record:
        code = mapping(record["close_warning"]).get("code")
        hint = "no action needed" if stop_kind(code) == "notice" else "log in again before the next run"
        result["close_warning"] = {"code": code, "hint": hint}
    return result


_ATTEMPT_KEYS = ("index", "parent", "candidate", "fixed", "broke", "validation_delta", "accepted")


def _export_search(value: object) -> object:
    """Return the search record with redacted attempts. The hypothesis and any uncoded rejection text stay in run.json."""
    if not isinstance(value, dict):
        return value
    search = cast(dict[str, object], value)
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
    return result


def _repo_path(rest: str, repo: Path | None) -> str:
    """Return `rest` when it names a path inside the repository root. Else raise `export-path-escapes`."""
    try:
        _ = relative(rest)
        inside = repo is not None and (repo / rest).resolve().is_relative_to(repo)
    except ValueError:
        inside = False
    if not inside:
        raise CodedError("export-path-escapes", f"the wedge source {rest} is not a path inside the repository root; "
                         + "no patch was written")
    return rest


def _lines(text: str | None) -> list[str]:
    """Split `text` after each line feed and nowhere else, so that `git apply` reproduces it exactly."""
    return re.findall(r"[^\n]*\n|[^\n]+", text or "")


def _patch_lines(name: str, old: str | None, new: str | None, *, git: bool) -> list[str]:
    """Return the diff of one file, with paths `a/NAME` and `b/NAME` and `/dev/null` for an added or deleted file.

    With `git`, an added or deleted file also gets git headers, so an empty one survives `git apply` from the repository root.
    Without it the diff is plain: `patch -p1` and `git apply` read the paths from the current directory.
    """
    head: list[str] = []
    if git and (old is None or new is None):
        head = [f"diff --git a/{name} b/{name}\n", "new file mode 100644\n" if old is None else "deleted file mode 100644\n"]
    body = list(difflib.unified_diff(_lines(old), _lines(new),
                                     fromfile="/dev/null" if old is None else "a/" + name,
                                     tofile="/dev/null" if new is None else "b/" + name))
    return head + body


def _patch_text(lines: list[str]) -> str:
    """Join the patch `lines`. Add the standard no-newline marker after a last line that lacks a line feed."""
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def _private_file(path: Path, text: str) -> None:
    """Create the new file `path` with mode 0600 and write `text` to it. Fail when `path` exists."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        _ = stream.write(text)


def export(out: Path, destination: Path) -> dict[str, object]:
    """Write the winner as a private patch and a redacted report. Never apply it."""
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
    repo = repo_root(Path(text_field(record, "target_root")))
    first, last = mapping(field(record, "seed")), mapping(field(record, "winner"))
    repo_names = {name: _repo_path(name.removeprefix(WEDGE_PREFIX), repo) for name in sorted(first.keys() | last.keys())
                  if name.startswith(WEDGE_PREFIX) and first.get(name) != last.get(name)}
    seed, candidate = dict(candidate_files(first)), dict(candidate_files(last))
    _ = seed.pop(NEW_CLI, None)
    value = candidate.pop(NEW_CLI, "")
    cli = parse_value(value) if value.strip() else None
    if cli is not None:
        candidate |= expand_files(Path(text_field(record, "target_root")), seed, cli)
    skill_lines: list[str] = []
    wedge_lines: list[str] = []
    for name in sorted(seed.keys() | candidate.keys()):
        old, new = seed.get(name), candidate.get(name)
        if old == new:
            continue
        if is_skill_file(name):
            skill_lines.extend(_patch_lines(name, old, new, git=False))
        else:
            wedge_lines.extend(_patch_lines(repo_names[name], old, new, git=True))
    destination.mkdir(mode=0o700)
    patches = ["candidate.patch"]
    _private_file(destination / "candidate.patch", _patch_text(skill_lines))
    if wedge_lines:
        _private_file(destination / "wedge-sources.patch", _patch_text(wedge_lines))
        patches.append("wedge-sources.patch")
    report = summary(record, out) | {"sharing": "private-local-only",
                                "outcomes": [_export_outcome(item) for item in record_outcomes(record)], "cost_usd": None}
    if cli is not None:
        report["new_cli"] = {"target": cli.name, "package": cli.package, "unused_helpers": unused_helpers(seed, candidate)}
    write(destination / "report.json", report)
    return {"export": str(destination), "sharing": "private-local-only", "installed": False, "patches": patches}

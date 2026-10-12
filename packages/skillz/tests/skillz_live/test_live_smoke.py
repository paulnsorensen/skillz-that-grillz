"""Opt-in live smoke run: a real harness, real GEPA reflection, and a real sandbox in one small run.

`just live-smoke HARNESS MODEL` runs it with `SKILLZ_LIVE=1`. Every other pytest run skips it, so `just build` makes no
model call. Each attempt appends one JSON line to `.live-smoke/runs.jsonl` at the repository root.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cli import main
from skillz_experiments._records import read

ROOT = Path(__file__).resolve().parents[4]
FIXTURE = Path(__file__).resolve().parents[1] / "skillz_experiments/fixtures/echo-skill/SKILL.md.fixture"
RECORDS = ROOT / ".live-smoke/runs.jsonl"
FAMILIES = {"f0": 2, "f1": 1, "f2": 6}
"""The smallest draft that leaves a search under the cap: two search families and one holdout family of six cases."""
RUN_CALLS = 58
"""The estimate of the draft with `--repeats 1`. The test approves no larger estimate."""
PREFLIGHT_CALLS = 3
CALL_CAP = RUN_CALLS + PREFLIGHT_CALLS

pytestmark = pytest.mark.live


def _draft() -> list[dict[str, object]]:
    """Return the fixed case draft. The echo skill replies with the request text as JSON."""
    cases: list[dict[str, object]] = []
    for family, size in FAMILIES.items():
        for index in range(size):
            request = f"Echo the text `{family}-{index}`."
            cases.append({"id": f"{family}-{index}", "family": family, "kind": "task", "request": request,
                          "files": {"fixture.md": f"{family}-{index}\n"}, "expected": {"request": request},
                          "source": "skill"})
    return cases


def _json(text: str) -> dict[str, object]:
    """Return the JSON object in `text`. Keep the tail of text that is not a JSON object under `text`."""
    try:
        value = cast(object, json.loads(text))
    except ValueError:
        return {"text": text[-2000:]} if text.strip() else {}
    return cast(dict[str, object], value) if isinstance(value, dict) else {"text": text[-2000:]}


def _call(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, object], dict[str, object]]:
    """Call the CLI in process. Return the exit code and the JSON objects on stdout and stderr."""
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, _json(captured.out), _json(captured.err)


def _stopped(stage: str, error: dict[str, object]) -> dict[str, object]:
    return {"verdict": "stopped", "stage": stage, "code": error.get("code"), "error": str(error.get("error"))[:300]}


def _run_calls(out: Path) -> int | None:
    try:
        calls = read(out / "run.json").get("calls")
    except (OSError, ValueError):
        return None
    return calls if isinstance(calls, int) else None


def _smoke(harness: str, model: str, work: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    """Run the preflight, one run, and the export. Return the record fields. Stop at the first failed step."""
    config = work / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": harness}))
    code, preflight, error = _call(capsys, "self-test", "--preflight-only", "--model", model,
                                   "--harness-config", str(config), "--max-invocations", str(PREFLIGHT_CALLS))
    if code:
        return _stopped("preflight", error)
    fields: dict[str, object] = {"preflight_calls": preflight.get("live_calls")}
    target = work / "echo-skill"
    target.mkdir()
    _ = shutil.copyfile(FIXTURE, target / "SKILL.md")
    out = work / "run"
    run = ("run", "--target", str(target), "--out", str(out), "--model", model, "--harness", harness, "--live",
           "--repeats", "1")
    _, stop, error = _call(capsys, *run)
    if error.get("code") != "cases-missing":
        return fields | _stopped("cases", error)
    _ = Path(cast(str, stop["draft"])).write_text(json.dumps(_draft()))
    _, stop, error = _call(capsys, *run)
    if error.get("code") != "cases-unapproved":
        return fields | _stopped("cases", error)
    approval = ("--approve-cases", cast(str, stop["case_hash"]))
    _, stop, error = _call(capsys, *run, *approval)
    if error.get("code") != "budget-unapproved":
        return fields | _stopped("budget", error)
    estimate = cast(int, cast(dict[str, object], stop["estimate"])["calls"])
    assert estimate <= RUN_CALLS, f"the smoke run needs {estimate} calls; the stated cap is {RUN_CALLS}"
    code, result, error = _call(capsys, *run, *approval, "--approve-budget", str(estimate))
    fields["calls"] = _run_calls(out)
    if code:
        return fields | _stopped("run", error)
    code, _, error = _call(capsys, "export", str(out), "--out", str(work / "export"))
    if code:
        return fields | _stopped("export", error)
    gate = result.get("gate")
    verdict = cast(dict[str, object], gate).get("verdict") if isinstance(gate, dict) else None
    return fields | {"verdict": verdict, "stage": "complete", "phase": result.get("phase"),
                     "patch": str(work / "export/candidate.patch")}


def _revision() -> str:
    found = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return found.stdout.strip()


def test_a_live_run_reaches_complete_and_exports_or_stops_with_a_code(tmp_path: Path,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    harness, model = os.environ.get("SKILLZ_LIVE_HARNESS", ""), os.environ.get("SKILLZ_LIVE_MODEL", "")
    if harness not in ("claude", "codex") or not model:
        pytest.fail("set SKILLZ_LIVE_HARNESS to claude or codex and SKILLZ_LIVE_MODEL to a model; use `just live-smoke`")
    entry: dict[str, object] = {"harness": harness, "model": model, "os": platform.system(),
                                "os_release": platform.release(), "machine": platform.machine(),
                                "revision": _revision(), "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "verdict": "error", "stage": None}
    started = time.monotonic()
    try:
        entry |= _smoke(harness, model, tmp_path, capsys)
    except BaseException as error:
        entry["error"] = repr(error)[:300]
        raise
    finally:
        entry["seconds"] = round(time.monotonic() - started, 1)
        RECORDS.parent.mkdir(exist_ok=True)
        with RECORDS.open("a", encoding="utf-8") as stream:
            _ = stream.write(json.dumps(entry) + "\n")
    spent = [value for value in (entry.get("preflight_calls"), entry.get("calls")) if isinstance(value, int)]
    assert sum(spent) <= CALL_CAP, entry
    if entry["stage"] == "complete":
        assert entry["phase"] == "complete" and Path(cast(str, entry["patch"])).is_file(), entry
    else:
        assert isinstance(entry.get("code"), str), f"the smoke run stopped without a coded error: {entry}"

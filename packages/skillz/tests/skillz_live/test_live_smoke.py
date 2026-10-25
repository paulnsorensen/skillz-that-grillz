"""Opt-in live smoke runs of the experiment stack and of the bundled session analytics.

`just live-smoke HARNESS MODEL [SCENARIO]` runs them with `SKILLZ_LIVE=1`. Every other pytest run skips the `live`
test, so `just build` makes no model call and reads no session log. The offline tests of the pass rule run in every
pytest run. Each attempt appends one JSON line to `.live-smoke/runs.jsonl` at the repository root. With
`SKILLZ_LIVE_DRY=1`, a run scenario stops at its budget question and records the estimate, so it makes no model call.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from fromargs_benefit import TOKEN_RULE, author, build_target, check_cli, cli_verdict, reference_draft, strong_model
from skillz_experiments._cli import main
from skillz_experiments._records import read

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
ECHO = HERE.parent / "skillz_experiments/fixtures/echo-skill/SKILL.md.fixture"
FLAWED_ECHO = ("---\nname: echo-skill\ndescription: Echo a request back as JSON. Use when a test needs a minimal skill.\n"
               "---\n# Echo skill\n\nReply with the request text reversed, letter by letter, as JSON.\n")
"""Each run seed carries one fixable flaw, so the search can beat it and the gate scores the holdout."""
RECORDS = ROOT / ".live-smoke/runs.jsonl"
ANALYTICS = ROOT / "skills/skillz/scripts/agent-session-analytics.pyz"
PACKS = ("skill-usage", "agent-orchestration", "drift-regression")
FAMILIES = {"f0": 3, "f1": 3, "f2": 6}
"""Two search families of three cases and one holdout family of six. The search share holds about three reflections."""
PREFLIGHT_CALLS = 3
INTERRUPT_AFTER = 3
"""The resume scenario stops its first process with SIGINT after this many recorded outcomes."""
COPY_CHECK = ("import json, pathlib\nout = pathlib.Path('output/result.txt')\n"
              "want = pathlib.Path('fixture.md').read_text().strip()\n"
              "print(json.dumps({'score': float(out.is_file() and out.read_text().strip() == want)}))")
"""The command grader: the candidate output `result.txt` must hold the text of the case fixture."""

@dataclass(frozen=True)
class Setup:
    """The inputs of one run scenario. `expect` names the stop code that the scenario expects, or None for `complete`."""

    target: Path
    draft: list[dict[str, object]]
    preflight: dict[str, object]
    harness: tuple[str, ...]
    flags: tuple[str, ...] = ()
    expect: str | None = None


@dataclass(frozen=True)
class Scenario:
    """One run scenario. `cap` is the largest run estimate that it approves. `bridge` needs Codex for the command bridge.

    `author` writes the case draft with a live agent in place of `Setup.draft`; a dry run keeps `Setup.draft`. `check`
    adds record fields after the export, and a scenario with `check` passes only by `cli_verdict`.
    """

    build: Callable[[Path, str, str], Setup]
    cap: int
    bridge: bool = False
    resume: bool = False
    author: Callable[[Path, Path, str], tuple[list[dict[str, object]] | None, dict[str, object]]] | None = None
    check: Callable[[Path, Path, list[dict[str, object]]], dict[str, object]] | None = None


def _cases(case: Callable[[str], dict[str, object]]) -> list[dict[str, object]]:
    return [{"id": f"{family}-{index}", "family": family, "source": "skill"} | case(f"{family}-{index}")
            for family, size in FAMILIES.items() for index in range(size)]


def _skill(work: Path, name: str, body: str, contract: dict[str, object] | None = None,
           scripts: dict[str, str] | None = None) -> Path:
    """Write a skill directory with `SKILL.md`, an optional contract, and optional helper scripts."""
    target = work / name
    target.mkdir()
    _ = (target / "SKILL.md").write_text(body, encoding="utf-8")
    if contract is not None:
        (target / "evals").mkdir()
        document: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": name,
                                       "invocation": f"${name} run", "editable": [], **contract}
        _ = (target / "evals/autoimprove.json").write_text(json.dumps(document), encoding="utf-8")
    for path, text in (scripts or {}).items():
        (target / path).parent.mkdir(parents=True, exist_ok=True)
        _ = (target / path).write_text(text, encoding="utf-8")
    return target


def _echo(work: Path) -> tuple[Path, list[dict[str, object]]]:
    """Return the flawed echo skill with no contract, and a draft that the default judge grades."""
    target = _skill(work, "echo-skill", FLAWED_ECHO)

    def case(name: str) -> dict[str, object]:
        request = f"Echo the text `{name}`."
        criteria = [{"id": "verbatim", "text": f"THE skill SHALL return `{name}` unchanged, in its original letter order.",
                     "must": True},
                    {"id": "json", "text": "THE skill SHALL reply with one JSON value."}]
        return {"kind": "task", "request": request, "files": {"fixture.md": f"{name}\n"},
                "expected": {"request": request, "criteria": criteria}}
    return target, _cases(case)


def _copy(work: Path, kind: dict[str, object]) -> tuple[Path, list[dict[str, object]]]:
    """Return a skill that writes `result.txt`, graded by `kind`, and a draft with one session-sourced case."""
    body = ("---\nname: copy-skill\ndescription: Copy fixture text into an output file. Use when a test needs a "
            "file-writing skill.\n---\n# Copy skill\n\nRead `fixture.md`. Write its text in uppercase to `result.txt`.\n")
    target = _skill(work, "copy-skill", body, {"kinds": {"task": kind}})

    def case(name: str) -> dict[str, object]:
        return {"kind": "task", "request": "Copy the text of fixture.md into result.txt as the skill says.",
                "files": {"fixture.md": f"token {name}\n"}, "expected": {"text": f"token {name}"}}
    draft = _cases(case)
    draft[1]["source"] = "session"
    return target, draft


def _builtin(harness: str) -> dict[str, object]:
    return {"schema_version": 1, "adapter": harness}


def _bridge(work: Path) -> tuple[str, ...]:
    """Write the bridge settings and return the command argv of the test-only Codex bridge."""
    settings = work / "bridge.json"
    codex = shutil.which("codex")
    assert codex is not None, "the command bridge needs codex on PATH"
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    _ = settings.write_text(json.dumps({"codex": codex, "codex_home": str(home)}), encoding="utf-8")
    return (sys.executable, "-I", str(HERE / "codex_bridge.py"), str(settings))


def _command_role(work: Path) -> dict[str, object]:
    return {"adapter": "command", "identity": "codex-bridge", "command": list(_bridge(work))}


def _basic(work: Path, harness: str, model: str) -> Setup:
    del model
    target, draft = _echo(work)
    return Setup(target, draft, _builtin(harness), ("--harness", harness, "--edit", "prose"))


def _command_harness(work: Path, harness: str, model: str) -> Setup:
    del harness, model
    target, draft = _echo(work)
    command = _bridge(work)
    flags = ("--harness", "command", *(f"--command={part}" for part in command), "--edit", "prose")
    return Setup(target, draft, {"schema_version": 1} | _command_role(work), flags)


def _command_grader(work: Path, harness: str, model: str) -> Setup:
    del model
    target, draft = _copy(work, {"grader": "command", "argv": ["/usr/bin/python3", "-I", "-c", COPY_CHECK]})
    return Setup(target, draft, _builtin(harness), ("--harness", harness, "--edit", "prose"))


def _hybrid_mixed(work: Path, harness: str, model: str) -> Setup:
    """Run the task on built-in Codex and the judge through the command bridge, with a hybrid grader and statistics."""
    del harness, model
    target, draft = _copy(work, {"grader": "hybrid", "argv": ["/usr/bin/python3", "-I", "-c", COPY_CHECK],
                                 "rubric": "Pass only when result.txt holds exactly the text of fixture.md."})
    contract = target / "evals/autoimprove.json"
    document = cast(dict[str, object], json.loads(contract.read_text(encoding="utf-8")))
    document["statistics"] = {"min_gain": 0, "min_lower_bound": 0, "family_budget": 0.2, "family_budgets": {"f2": 0.3},
                              "min_coverage": 0.5}
    _ = contract.write_text(json.dumps(document), encoding="utf-8")
    for case in draft:
        expected = cast(dict[str, object], case["expected"])
        expected["criteria"] = [{"id": "exact-copy", "must": True, "from": "expected_output",
                                 "text": f"THE skill SHALL write `{expected['text']}` to `result.txt` with no change of case."}]
    config: dict[str, object] = {"schema_version": 1, "adapter": "codex", "roles": {"judge": _command_role(work)}}
    path = work / "harness.json"
    _ = path.write_text(json.dumps(config), encoding="utf-8")
    return Setup(target, draft, config, ("--harness-config", str(path), "--edit", "prose"), ("--min-gain", "0"))


def _prose_cli(work: Path, harness: str, model: str) -> Setup:
    """Search the prose and the helper script with `--edit prose+cli`. An exact-json grader needs no judge."""
    del model
    body = ("---\nname: count-skill\ndescription: Count the words of fixture.md with the bundled helper. Use when a "
            "test needs a helper skill.\n---\n# Count skill\n\nRun `python3 -I .agents/skills/count-skill/scripts/count.py "
            "fixture.md`. Add 1 to its `words` value, then return the JSON as the result.\n")
    helper = ("import json\nimport sys\nfrom pathlib import Path\n\n"
              "print(json.dumps({'words': len(Path(sys.argv[1]).read_text().split())}))\n")
    contract: dict[str, object] = {
        "kinds": {"task": {"grader": "exact-json"}},
        "helper": {"path": "scripts/count.py", "input": "fixture.md",
                   "fixtures": [{"input": "one two three", "returncode": 0, "output": {"words": 3}}]}}
    target = _skill(work, "count-skill", body, contract, {"scripts/count.py": helper})

    def case(name: str) -> dict[str, object]:
        words = " ".join(["word"] * (int(name.rsplit("-", 1)[1]) + 2))
        return {"kind": "task", "request": "Count the words of fixture.md.", "files": {"fixture.md": words + "\n"},
                "expected": {"words": len(words.split())}}
    return Setup(target, _cases(case), _builtin(harness), ("--harness", harness), ("--edit", "prose+cli"))


def _journey(work: Path, harness: str, model: str) -> Setup:
    """Run journey cases. Only the claude task transport holds a conversation, so another harness stops with a code."""
    del model
    kind = {"grader": "journey", "argv": ["/usr/bin/python3", "-I", "-c", "import json; print(json.dumps({'score': 1}))"],
            "rubric": "Pass only when the agent echoed the brief's text back as JSON."}
    target = _skill(work, "echo-skill", ECHO.read_text(encoding="utf-8"), {"kinds": {"journey": kind}})

    def case(name: str) -> dict[str, object]:
        return {"kind": "journey", "request": "Echo what I tell you.", "brief": f"Say `{name}` once, then stop.",
                "max_turns": 2, "files": {"fixture.md": f"{name}\n"},
                "expected": {"must": [name], "criteria": [{"id": "says-text", "from": "must:0", "must": True,
                                                         "text": f"THE agent SHALL say `{name}` once."}]}}
    return Setup(target, _cases(case), _builtin(harness), ("--harness", harness, "--edit", "prose"),
                 expect=None if harness == "claude" else "journey-unsupported")


def _fromargs_benefit(work: Path, harness: str, model: str) -> Setup:
    """Search a skill that applies version rules by hand. The task role runs `model`; reflection and judge run stronger.

    The token rule lets a CLI that keeps the score and cuts the task tokens promote, so a strong seed does not stop
    the run with `cases-too-easy`.
    """
    del model
    stronger = {"model": strong_model()}
    config: dict[str, object] = {"schema_version": 1, "adapter": harness,
                                 "roles": {"reflection": stronger, "judge": stronger}}
    path = work / "harness.json"
    _ = path.write_text(json.dumps(config), encoding="utf-8")
    return Setup(build_target(work), reference_draft(), config, ("--harness-config", str(path)),
                 ("--edit", "prose+cli", *TOKEN_RULE))


SCENARIOS: dict[str, Scenario] = {
    "basic": Scenario(_basic, 130),
    "command-harness": Scenario(_command_harness, 130, bridge=True),
    "command-grader": Scenario(_command_grader, 71),
    "hybrid-mixed": Scenario(_hybrid_mixed, 130, bridge=True),
    "prose-cli": Scenario(_prose_cli, 71),
    "journey": Scenario(_journey, 601),
    "resume": Scenario(_basic, 130, resume=True),
    "fromargs-benefit": Scenario(_fromargs_benefit, 167, author=author, check=check_cli),
}
"""Each cap is the measured estimate of its draft with `--repeats 1` (`SKILLZ_LIVE_DRY=1` reports it)."""
NAMES = (*SCENARIOS, "analytics")


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


def _record(out: Path) -> dict[str, object]:
    try:
        return read(out / "run.json")
    except (OSError, ValueError):
        return {}


def _interrupt(argv: tuple[str, ...], out: Path) -> dict[str, object]:
    """Start the run in a child process. Stop it with SIGINT after `INTERRUPT_AFTER` outcomes. Report what it left."""
    child = subprocess.Popen([sys.executable, "-m", "skillz_experiments", *argv], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    outcomes, deadline = 0, time.monotonic() + 1800
    while child.poll() is None and time.monotonic() < deadline:
        outcomes = len(cast(list[object], _record(out).get("outcomes") or []))
        if outcomes >= INTERRUPT_AFTER:
            child.send_signal(signal.SIGINT)
            break
        time.sleep(1)
    try:
        code = child.wait(timeout=300)
    except subprocess.TimeoutExpired:
        child.kill()
        code = child.wait()
    record = _record(out)
    return {"interrupted_after": outcomes, "interrupted_exit": code, "interrupted_phase": record.get("phase"),
            "interrupted_calls": record.get("calls")}


def _run(scenario: Scenario, harness: str, model: str, work: Path, capsys: pytest.CaptureFixture[str],
         dry: bool) -> dict[str, object]:
    """Run the preflight, one run, and the export. Return the record fields. Stop at the first failed step."""
    setup = scenario.build(work, harness, model)
    fields: dict[str, object] = {"expect": setup.expect}
    if not dry:
        config = work / "preflight.json"
        _ = config.write_text(json.dumps(setup.preflight), encoding="utf-8")
        code, preflight, error = _call(capsys, "self-test", "--preflight-only", "--model", model,
                                       "--harness-config", str(config), "--max-invocations", str(PREFLIGHT_CALLS))
        if code:
            return fields | _stopped("preflight", error)
        fields["preflight_calls"] = preflight.get("live_calls")
    draft = setup.draft
    if scenario.author is not None and not dry:
        authored, notes = scenario.author(setup.target, work, model)
        fields |= notes
        if authored is None:
            return fields | _stopped("author", notes)
        draft = authored
    out = work / "run"
    run = ("run", "--target", str(setup.target), "--out", str(out), "--model", model, *setup.harness, "--live",
           "--repeats", "1", *setup.flags)
    _, stop, error = _call(capsys, *run)
    if error.get("code") != "cases-missing":
        return fields | _stopped("cases", error)
    _ = Path(cast(str, stop["draft"])).write_text(json.dumps(draft), encoding="utf-8")
    _, stop, error = _call(capsys, *run)
    if error.get("code") != "cases-unapproved":
        return fields | _stopped("cases", error)
    approval = ("--approve-cases", cast(str, stop["case_hash"]))
    _, stop, error = _call(capsys, *run, *approval)
    if error.get("code") != "budget-unapproved":
        return fields | _stopped("budget", error)
    estimate = cast(int, cast(dict[str, object], stop["estimate"])["calls"])
    fields["estimate"] = estimate
    if dry:
        return fields | {"verdict": "dry", "stage": "budget"}
    assert estimate <= scenario.cap, f"the run needs {estimate} calls; the stated cap is {scenario.cap}"
    final = (*run, *approval, "--approve-budget", str(estimate))
    if scenario.resume:
        fields |= _interrupt(final, out)
    code, result, error = _call(capsys, *final)
    fields["calls"] = _record(out).get("calls")
    if code:
        return fields | _stopped("run", error)
    code, _, error = _call(capsys, "export", str(out), "--out", str(work / "export"))
    if code:
        return fields | _stopped("export", error)
    gate = cast(dict[str, object], result.get("gate")) if isinstance(result.get("gate"), dict) else {}
    fields |= {"verdict": gate.get("verdict"), "stage": "complete", "phase": result.get("phase"),
               "gate_reasons": gate.get("reasons"), "holdout_scored": gate.get("n_shared"),
               "patch": str(work / "export/candidate.patch")}
    return fields | ({} if scenario.check is None else scenario.check(setup.target, work, draft))


def _analytics(work: Path) -> dict[str, object]:
    """Ingest the real session logs into a private database, then run every query of the three /skillz packs."""
    environment = {**os.environ, "SESSIONS_DB": str(work / "sessions.duckdb")}

    def call(*argv: str) -> object:
        result = subprocess.run([sys.executable, "-I", str(ANALYTICS), *argv], env=environment, capture_output=True,
                                text=True, timeout=1800, check=False)
        assert result.returncode == 0, f"{argv[0]} exited {result.returncode}: {result.stdout[-300:]} {result.stderr[-300:]}"
        return cast(object, json.loads(result.stdout))

    ingest = cast(dict[str, object], call("ingest", "--force"))
    assert ingest.get("status") == "ingested", ingest
    sessions = cast(list[dict[str, object]], call("sql", "SELECT harness, count(*) AS n FROM sessions GROUP BY 1"))
    targets = {kind: cast(list[dict[str, object]], call("sql", f"SELECT {column} AS name FROM {table} GROUP BY 1 ORDER BY count(*) DESC LIMIT 1"))
               for kind, table, column in (("skill", "skill_invocations", "skill_name"), ("agent", "agent_spawns", "agent_type"))}
    queries = 0
    for pack in PACKS:
        text = (ROOT / f"skills/skillz/references/{pack}.md").read_text(encoding="utf-8")
        for kind, (table, column) in {"skill": ("skill_invocations", "skill_name"), "agent": ("agent_spawns", "agent_type")}.items():
            name = str(targets[kind][0]["name"]) if targets[kind] else "none"
            for sql in cast(list[str], re.findall(r"```sql\n(.*?)```", text, re.DOTALL)):
                filled = sql.replace("{START_TABLE}", table).replace("{NAME_COLUMN}", column).replace("{TARGET}", name)
                assert not re.search(r"\{[A-Z_]+\}", filled), f"{pack} has an unknown placeholder: {filled[:200]}"
                assert isinstance(call("sql", filled.strip().rstrip(";")), list), f"{pack} returned no row list"
                queries += 1
    return {"verdict": "complete", "stage": "complete", "phase": "complete", "code": None, "queries": queries,
            "sessions": {str(row["harness"]): row["n"] for row in sessions}, "ingest_seconds": ingest.get("elapsed_seconds")}


def _selected() -> set[str]:
    value = os.environ.get("SKILLZ_LIVE_SCENARIO", "basic")
    chosen = set(NAMES) if value == "all" else set(value.split(","))
    unknown = chosen - set(NAMES)
    if unknown:
        pytest.fail(f"unknown SKILLZ_LIVE_SCENARIO {', '.join(sorted(unknown))}; choose all or {', '.join(NAMES)}")
    return chosen


def _revision() -> str:
    found = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return found.stdout.strip()


def _dry_verdict(entry: dict[str, object], scenario: Scenario) -> tuple[bool, str]:
    """Return whether a dry record passes, and the reason.

    A dry run passes when it stops with the code that the scenario expects, or when it reaches the budget question
    with an estimate at most the cap. An earlier stop, or an estimate over the cap, fails, so that a live run does not.
    """
    code, expect, estimate = entry.get("code"), entry.get("expect"), entry.get("estimate")
    if expect is not None and code == expect:
        return True, f"the run stopped with the expected code {expect}"
    if entry.get("verdict") != "dry":
        return False, f"the dry run stopped at stage {entry.get('stage')} with code {code} before the budget question"
    if not isinstance(estimate, int) or estimate > scenario.cap:
        return False, f"the run needs {estimate} calls; the stated cap is {scenario.cap}"
    return True, f"the estimate of {estimate} calls is at most the cap of {scenario.cap}"


def _verdict(entry: dict[str, object], scenario: Scenario) -> tuple[bool, str]:
    """Return whether a run record passes, and the reason.

    A scenario that expects a stop code passes only with that code. A preflight stop makes no task call, so it passes
    only when the scenario expects its code. A resume that reaches the interrupt must leave a nonzero exit and an
    unfinished run. A completed run must export a patch and score holdout cases. Any other step passes on a coded stop.
    """
    stage, code, expect = entry.get("stage"), entry.get("code"), entry.get("expect")
    interrupted = "interrupted_exit" in entry
    stop = f"the run stopped at stage {stage} with code {code}"
    if scenario.resume and not interrupted:
        stop += " before the interrupt"
    if expect is not None:
        return code == expect, f"{stop}; the scenario expects {expect}"
    if stage == "preflight":
        return False, f"{stop}; the scenario does not expect a preflight stop, so the run made no task call"
    if scenario.check is not None:
        return cli_verdict(entry)
    if scenario.resume and interrupted and (entry["interrupted_exit"] == 0 or entry.get("interrupted_phase") == "complete"):
        return False, (f"the run was not interrupted: the first process exited {entry['interrupted_exit']} "
                       f"in phase {entry.get('interrupted_phase')}")
    if stage == "complete":
        if entry.get("phase") != "complete" or not Path(str(entry.get("patch"))).is_file():
            return False, "the run completed without phase complete and an exported candidate.patch"
        if not entry.get("holdout_scored"):
            return False, "the search kept the seed, so the gate scored no holdout case"
        return True, f"the run completed, exported a patch, and scored {entry['holdout_scored']} holdout cases"
    if isinstance(code, str):
        return True, stop
    return False, f"the run stopped at stage {stage} without a coded error"


@pytest.mark.live
@pytest.mark.parametrize("name", NAMES)
def test_a_live_scenario_completes_and_exports_or_stops_with_a_code(name: str, tmp_path: Path,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    if name not in _selected():
        pytest.skip(f"scenario {name} is not in SKILLZ_LIVE_SCENARIO")
    harness, model = os.environ.get("SKILLZ_LIVE_HARNESS", ""), os.environ.get("SKILLZ_LIVE_MODEL", "")
    if harness not in ("claude", "codex") or not model:
        pytest.fail("set SKILLZ_LIVE_HARNESS to claude or codex and SKILLZ_LIVE_MODEL to a model; use `just live-smoke`")
    scenario = SCENARIOS.get(name)
    if scenario is not None and scenario.bridge and harness != "codex":
        pytest.skip(f"scenario {name} drives the command bridge to Codex; run it with HARNESS codex")
    dry = os.environ.get("SKILLZ_LIVE_DRY") == "1"
    entry: dict[str, object] = {"scenario": name, "harness": harness, "model": model, "os": platform.system(),
                                "os_release": platform.release(), "machine": platform.machine(),
                                "revision": _revision(), "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "verdict": "error", "stage": None}
    started = time.monotonic()
    try:
        entry |= _analytics(tmp_path) if scenario is None else _run(scenario, harness, model, tmp_path, capsys, dry)
    except BaseException as error:
        entry["error"] = repr(error)[:300]
        raise
    finally:
        entry["seconds"] = round(time.monotonic() - started, 1)
        RECORDS.parent.mkdir(exist_ok=True)
        with RECORDS.open("a", encoding="utf-8") as stream:
            _ = stream.write(json.dumps(entry) + "\n")
    if scenario is None:
        return
    if dry:
        passed, reason = _dry_verdict(entry, scenario)
        assert passed, f"{reason}: {entry}"
        return
    spent = [value for value in (entry.get("preflight_calls"), entry.get("calls")) if isinstance(value, int)]
    assert sum(spent) <= scenario.cap + PREFLIGHT_CALLS, entry
    passed, reason = _verdict(entry, scenario)
    assert passed, f"{reason}: {entry}"


BASIC, RESUME, JOURNEY = SCENARIOS["basic"], SCENARIOS["resume"], SCENARIOS["journey"]
ISOLATION = "network-isolation-failed"


def _preflight_stop(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], work: Path,
                    expect: str | None) -> dict[str, object]:
    """Run `_run` with a preflight that exits 1 with `ISOLATION`. Fail on any call after the preflight."""
    def call(capture: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, object], dict[str, object]]:
        del capture
        assert argv[:2] == ("self-test", "--preflight-only"), f"the run went past a failed preflight: {argv}"
        return 1, {}, {"code": ISOLATION, "error": "the sandbox reached the network"}

    def build(target: Path, harness: str, model: str) -> Setup:
        del harness, model
        return Setup(target, [], {"schema_version": 1}, (), expect=expect)
    monkeypatch.setattr(sys.modules[__name__], "_call", call)
    return _run(Scenario(build, 97), "codex", "model", work, capsys, dry=False)


def _complete(work: Path, holdout: int) -> dict[str, object]:
    patch = work / "candidate.patch"
    _ = patch.write_text("--- a\n+++ b\n", encoding="utf-8")
    return {"stage": "complete", "phase": "complete", "code": None, "expect": None, "patch": str(patch),
            "holdout_scored": holdout, "interrupted_exit": 130, "interrupted_phase": "search"}


@pytest.mark.ac("gh-339:AC-1")
def test_a_preflight_stop_that_the_scenario_does_not_expect_fails() -> None:
    passed, _ = _verdict({"stage": "preflight", "code": ISOLATION, "expect": None}, BASIC)
    assert not passed


@pytest.mark.ac("gh-339:AC-2")
def test_the_failure_of_an_unexpected_preflight_stop_names_the_stage_and_the_code() -> None:
    passed, reason = _verdict({"stage": "preflight", "code": ISOLATION, "expect": None}, BASIC)
    assert not passed and "stage preflight" in reason and ISOLATION in reason, reason


@pytest.mark.ac("gh-339:AC-3")
def test_a_preflight_stop_keeps_the_expected_code_in_the_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    entry = _preflight_stop(monkeypatch, capsys, tmp_path, "journey-unsupported")
    assert entry["stage"] == "preflight" and entry["code"] == ISOLATION, entry
    assert entry["expect"] == "journey-unsupported", entry


@pytest.mark.ac("gh-339:AC-4")
def test_a_preflight_stop_with_another_code_than_the_expected_one_fails(monkeypatch: pytest.MonkeyPatch,
                                                                        tmp_path: Path,
                                                                        capsys: pytest.CaptureFixture[str]) -> None:
    passed, reason = _verdict(_preflight_stop(monkeypatch, capsys, tmp_path, "journey-unsupported"), JOURNEY)
    assert not passed and ISOLATION in reason and "journey-unsupported" in reason, reason


@pytest.mark.ac("gh-339:AC-4")
def test_a_stop_with_the_expected_code_passes() -> None:
    entry: dict[str, object] = {"stage": "run", "code": "journey-unsupported", "expect": "journey-unsupported"}
    assert _verdict(entry, JOURNEY)[0]


@pytest.mark.ac("gh-339:AC-1")
def test_a_completed_run_passes_only_with_a_patch_and_a_scored_holdout(tmp_path: Path) -> None:
    assert _verdict(_complete(tmp_path, 6), BASIC)[0]
    passed, reason = _verdict(_complete(tmp_path, 0), BASIC)
    assert not passed and "no holdout case" in reason, reason
    missing = _complete(tmp_path, 6) | {"patch": str(tmp_path / "absent.patch")}
    assert not _verdict(missing, BASIC)[0]


@pytest.mark.ac("gh-340:AC-1")
@pytest.mark.ac("gh-340:AC-2")
@pytest.mark.parametrize(("stage", "code", "passes"), [("preflight", ISOLATION, False), ("cases", "cases-invalid", True),
                                                     ("budget", "budget-over-cap", True)])
def test_a_resume_that_stops_before_the_interrupt_reports_the_stage_and_the_code(stage: str, code: str,
                                                                                  passes: bool) -> None:
    passed, reason = _verdict({"stage": stage, "code": code, "expect": None}, RESUME)
    assert passed is passes and f"stage {stage}" in reason and code in reason, reason


@pytest.mark.ac("gh-340:AC-3")
@pytest.mark.parametrize(("exit_code", "phase", "passes"), [(130, "search", True), (0, "search", False),
                                                           (130, "complete", False)])
def test_a_resume_that_reaches_the_interrupt_must_leave_a_nonzero_exit_and_an_unfinished_run(
        exit_code: int, phase: str, passes: bool, tmp_path: Path) -> None:
    entry = _complete(tmp_path, 6) | {"interrupted_exit": exit_code, "interrupted_phase": phase}
    passed, reason = _verdict(entry, RESUME)
    assert passed is passes, reason
    assert passes or "not interrupted" in reason, reason


@pytest.mark.ac("fromargs-benefit-smoke:AC-4")
@pytest.mark.parametrize("entry", [{"stage": "run", "code": "cases-too-easy", "expect": None}, None])
def test_the_fromargs_benefit_scenario_fails_a_record_that_the_shared_rule_passes(entry: dict[str, object] | None,
                                                                                  tmp_path: Path) -> None:
    record = entry or _complete(tmp_path, 6)
    assert _verdict(record, BASIC)[0], "the shared rule passes a coded stop and a completed run"
    passed, reason = _verdict(record, SCENARIOS["fromargs-benefit"])
    assert not passed and ("cli=None" in reason or "cases-too-easy" in reason), reason


@pytest.mark.parametrize(("entry", "passes", "phrase"), [
    ({"verdict": "dry", "stage": "budget", "expect": None, "estimate": 130}, True, "at most the cap"),
    ({"verdict": "dry", "stage": "budget", "expect": None, "estimate": 131}, False, "the stated cap is 130"),
    ({"verdict": "stopped", "stage": "cases", "expect": None, "code": "case-criteria-missing"}, False,
     "stage cases with code case-criteria-missing"),
    ({"verdict": "stopped", "stage": "cases", "expect": "journey-unsupported", "code": "journey-unsupported"}, True,
     "expected code")])
def test_a_dry_run_passes_only_at_the_budget_question_within_the_cap(entry: dict[str, object], passes: bool,
                                                                     phrase: str) -> None:
    passed, reason = _dry_verdict(entry, BASIC)
    assert passed is passes and phrase in reason, reason

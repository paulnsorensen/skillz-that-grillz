"""Drive the public CLI through approvals and a real offline command adapter."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import math
import statistics
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from skillz_experiments._intake import DRAFT_NAME
from skillz_experiments.harness._claude import _sandbox
from skillz_experiments.run import summary


CRITERIA = [{"id": "text", "text": "THE output SHALL match the reference text."}]
FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[5]
CLI = ROOT / ".venv/bin/skillz-experiment"
BUNDLE = ROOT / "skills/skillz/scripts/skillz-experiment.pyz"
PYTHON = Path("/usr/bin/python3")
HARNESSES = ("command", "claude", "codex")
# Source runs cover every harness; one bundle smoke per flow keeps the bundle-parity check cheap.
HARNESS_RUNS = [pytest.param(name, CLI, id=f"{name}-source") for name in HARNESSES] + [
    pytest.param("command", BUNDLE, id="command-bundle")]
CLAUDE_CREDENTIAL: dict[str, object] = {"claudeAiOauth": {"accessToken": "test", "refreshToken": "test"}}
CODEX_CREDENTIAL: dict[str, object] = {"auth_mode": "chatgpt", "tokens": {"access_token": "test"}}


def snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    def entry(path: Path) -> tuple[int, int, str]:
        mode = path.lstat().st_mode
        data = (str(path.readlink()) if path.is_symlink()
                else hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "")
        return stat.S_IFMT(mode), stat.S_IMODE(mode), data

    return {path.relative_to(root).as_posix(): entry(path) for path in (root, *root.rglob("*"))}


def build_wedge(skill: Path, destination: Path) -> dict[str, dict[str, str]]:
    destination.mkdir()
    result = subprocess.run([str(ROOT / ".venv/bin/wedge"), "build", str(skill), "--out", str(destination)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return cast(dict[str, dict[str, str]], json.loads(result.stdout))

@dataclass(frozen=True)
class Scenario:
    target: Path
    out: Path
    export: Path
    draft: list[dict[str, object]]
    original: str


def host_path_tools() -> list[str]:
    """Name each tool that the host check finds through PATH on this platform."""
    return [name for name, _, _ in _sandbox.host_tools() if not name.startswith("/")]


def write_credential(path: Path, content: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(content))
    path.chmod(0o600)


def make_vendor_bin(scenario: Scenario, home: Path, harness: str, mode: str) -> Path:
    directory = scenario.out / "bin"
    directory.mkdir(exist_ok=True)
    settings = {"mode": mode, "log": str(scenario.out / "vendor.jsonl")}
    _ = (directory / "vendor-settings.json").write_text(json.dumps(settings))
    if harness == "claude":
        for name, source in (("claude", FIXTURES / "claude.py"), ("bwrap", FIXTURES / "bwrap.py"),
                             ("sandbox-exec", FIXTURES / "bwrap.py")):
            target = directory / name
            _ = shutil.copyfile(source, target)
            target.chmod(0o755)
        for name in host_path_tools():
            stub = directory / name
            if not stub.exists():
                _ = stub.write_text(f"#!/bin/sh\necho '{name}: e2e stub, not the real tool' >&2\nexit 1\n")
                stub.chmod(0o755)
        for name, source in (("fake_claude.py", FIXTURES.parents[1] / "fixtures/fake_claude.py"),
                             ("vendor_common.py", FIXTURES / "vendor_common.py"),
                             ("journey.py", FIXTURES / "journey.py"),
                             ("targets.py", FIXTURES / "targets.py")):
            (directory / name).symlink_to(source)
        write_credential(home / ".claude/.credentials.json", CLAUDE_CREDENTIAL)
    elif harness == "codex":
        target = directory / "codex"
        _ = shutil.copyfile(FIXTURES / "codex.py", target)
        target.chmod(0o755)
        (directory / "vendor_common.py").symlink_to(FIXTURES / "vendor_common.py")
        (directory / "targets.py").symlink_to(FIXTURES / "targets.py")
        write_credential(home / ".codex/auth.json", CODEX_CREDENTIAL)
    else:
        raise ValueError(f"vendor fixture is not ready for {harness}")
    return directory


def make_scenario(base: Path, *, helper: bool = False) -> Scenario:
    target = base / "transform-skill"
    target.mkdir()
    original = ("---\nname: transform-skill\ndescription: Transform a fixture file.\n---\n"
                + "# Transform\nCopy fixture.md unchanged.\n")
    _ = (target / "SKILL.md").write_text(original)
    contract: dict[str, object] = {
        "schema_version": 1, "status": "approved", "skill": "transform-skill",
        "invocation": "$transform-skill run",
        "kinds": {"task": {"grader": "command", "argv": [str(PYTHON), str(FIXTURES / "grade.py")]}},
        "editable": [],
    }
    if helper:
        script = target / "scripts/transform.py"
        script.parent.mkdir()
        _ = script.write_text("import json,sys\nfrom pathlib import Path\ntext = Path(sys.argv[1]).read_text()\ntext = text\nprint(json.dumps({'text': text}))\n")
        contract["helper"] = {"path": "scripts/transform.py", "input": "fixture.md",
                              "fixtures": [{"input": "123\n", "returncode": 0,
                                            "output": {"text": "123\n"}}]}
    evals = target / "evals"
    evals.mkdir()
    _ = (evals / "autoimprove.json").write_text(json.dumps(contract))
    draft: list[dict[str, object]] = []
    for index in range(10):
        source = f"case-{index}\n"
        draft.append({"id": f"t{index}", "family": f"f{index}", "kind": "task",
                      "request": "Transform fixture.md according to this skill.",
                      "files": {"fixture.md": source}, "expected": {"text": source.upper(), "criteria": CRITERIA},
                      "source": "skill"})
    out = base / "run"
    out.mkdir(mode=0o700)
    return Scenario(target, out, base / "export", draft, original)


def call_cli(args: list[str], home: Path, *, cli: Path = CLI, bin_dirs: Sequence[Path] = (),
             env_overrides: Mapping[str, str] | None = None) -> tuple[int, dict[str, object], str]:
    """Run the CLI with the host credential variables removed unless the caller overrides them."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    _ = env.pop("CODEX_HOME", None)
    _ = env.pop("CLAUDE_CONFIG_DIR", None)
    env.update(env_overrides or {})
    if bin_dirs:
        env["PATH"] = os.pathsep.join([*map(str, bin_dirs), env["PATH"]])
    command = [str(cli), *args]
    if cli.suffix == ".pyz":
        installed = home / "installed/skillz-experiment.pyz"
        installed.parent.mkdir(exist_ok=True)
        if not installed.exists():
            _ = shutil.copyfile(cli, installed)
        env["SHIV_ROOT"] = str(home / "shiv")
        command = [str(ROOT / ".venv/bin/python"), "-I", "-S", str(installed), *args]
    result = subprocess.run(command, cwd=home, env=env, capture_output=True,
                            text=True, check=False, timeout=300)
    data = cast(dict[str, object], json.loads(result.stdout)) if result.stdout.strip() else {}
    return result.returncode, data, result.stderr


def command_adapter(scenario: Scenario, **settings: object) -> list[str]:
    """Write the fixture settings and return the `--command` arguments that run the adapter."""
    path = scenario.out / "vendor-settings.json"
    _ = path.write_text(json.dumps({"mode": "normal", "log": str(scenario.out / "vendor.jsonl")} | settings))
    return [item for value in (str(PYTHON), str(FIXTURES / "command.py"), str(path))
            for item in ("--command", value)]


def cli_error(stderr: str) -> dict[str, object]:
    """Parse the coded error the CLI prints on stderr; fail readably when it is not coded."""
    try:
        parsed = cast(object, json.loads(stderr))
    except json.JSONDecodeError:
        parsed = None
    assert isinstance(parsed, dict) and "code" in parsed, f"stderr is not a coded CLI error: {stderr!r}"
    return cast(dict[str, object], parsed)


def approve_and_run(args: list[str], home: Path, scenario: Scenario, *, cli: Path = CLI,
                    bin_dirs: Sequence[Path] = (), env_overrides: Mapping[str, str] | None = None,
                    on_first: Callable[[dict[str, object]], None] | None = None,
                    ) -> tuple[int, dict[str, object], str]:
    """Walk cases-missing, cases-unapproved, budget-unapproved, then run; return the final call."""
    def call(extra: list[str]) -> tuple[int, dict[str, object], str]:
        return call_cli([*args, *extra], home, cli=cli, bin_dirs=bin_dirs, env_overrides=env_overrides)

    def refused(extra: list[str], code_name: str) -> dict[str, object]:
        code, data, error = call(extra)
        assert code == 1 and cli_error(error)["code"] == code_name, (data, error)
        return data

    first = refused([], "cases-missing")
    if on_first is not None:
        on_first(first)
    _ = (scenario.out / DRAFT_NAME).write_text(json.dumps(scenario.draft))
    case_hash = cast(str, refused([], "cases-unapproved")["case_hash"])
    approved = ["--approve-cases", case_hash]
    budget = refused(approved, "budget-unapproved")
    calls = cast(dict[str, int], budget["estimate"])["calls"]
    return call([*approved, "--approve-budget", str(calls)])


def run_scenario(scenario: Scenario, home: Path, *, helper: bool = False, harness: str = "command",
                 mode: str = "normal", cli: Path = CLI, extra_flags: tuple[str, ...] = (),
                 bin_dir: Path | None = None, env_overrides: Mapping[str, str] | None = None) -> dict[str, object]:
    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out),
            "--model", "offline-test", "--harness", harness, "--live",
            "--edit", "prose+cli" if helper else "prose", *extra_flags]
    path_dirs = [] if bin_dir is None else [bin_dir]
    if harness == "command":
        command = command_adapter(scenario, mode=mode)
    else:
        path_dirs.insert(0, make_vendor_bin(scenario, home, harness, mode))
        command = []
    def check_doctor(first: dict[str, object]) -> None:
        if harness == "claude":
            rows = cast(list[dict[str, str]], cast(dict[str, object], first["doctor"])["checks"])
            tools = [row for row in rows if row["check"].startswith("tool:")]
            assert tools and all(row["status"] == "pass" for row in tools), tools

    code, result, error = approve_and_run([*args, *command], home, scenario, cli=cli, bin_dirs=path_dirs,
                                          env_overrides=env_overrides, on_first=check_doctor)
    assert code == 0, (result, error)
    assert result["phase"] == "complete"
    return result


def read_record(out: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads((out / "run.json").read_text()))


def read_cases(out: Path) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], cast(dict[str, object], json.loads((out / "cases.json").read_text()))["cases"])


def read_events(out: Path) -> list[dict[str, object]]:
    """Return the vendor log; it is empty until the first vendor call."""
    log = out / "vendor.jsonl"
    if not log.exists():
        return []
    return [cast(dict[str, object], json.loads(line)) for line in log.read_text().splitlines()]


def carries_answer(expected: str, text: str) -> bool:
    return expected.strip() in text


def _files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def export_and_apply(scenario: Scenario, home: Path, *, cli: Path = CLI) -> Path:
    code, result, error = call_cli(["export", str(scenario.out), "--out", str(scenario.export)], home, cli=cli)
    assert code == 0, (result, error)
    applied = home / "applied"
    _ = shutil.copytree(scenario.target, applied)
    patch = scenario.export / "candidate.patch"
    command = subprocess.run(["patch", "-p1", "-i", str(patch)], cwd=applied,
                             capture_output=True, text=True, check=False)
    assert command.returncode == 0, command.stderr
    winner = cast(dict[str, str], read_record(scenario.out)["winner"])
    for name, content in winner.items():
        assert (applied / name).read_text() == content, name
    assert _files(applied) == _files(scenario.target) | set(winner)
    for name in _files(applied) - set(winner):
        assert (applied / name).read_bytes() == (scenario.target / name).read_bytes(), name
    return applied


def holdout(record: dict[str, object]) -> list[dict[str, object]]:
    items = [cast(dict[str, object], item) for item in cast(list[object], record["outcomes"])]
    return [item for item in items if item.get("split") == "holdout" and item.get("arm") != "replay"]


def assert_replayed(out: Path) -> None:
    """Assert the replay supports the export: no reason, and one loaded replay outcome per holdout case."""
    record = read_record(out)
    replay = cast(dict[str, object], record["replay"])
    export = cast(dict[str, object], cast(dict[str, object], summary(record, out)["claims"])["export"])
    assert export["status"] == "supported" and replay["reasons"] == []
    items = [cast(dict[str, object], item) for item in cast(list[object], record["outcomes"])
             if cast(dict[str, object], item).get("arm") == "replay"]
    held = sorted(cast(str, case["id"]) for case in read_cases(out) if case["split"] == "holdout")
    assert sorted(cast(str, item["case_id"]) for item in items) == held and held
    assert all(item["loaded"] is True and item["split"] == "holdout" for item in items)


def assert_promoted(scenario: Scenario, result: dict[str, object], *,
                    kinds: Collection[str] = ("task",), cases: int | None = 6) -> None:
    """Assert a clean promotion: every holdout case scores 0 for baseline and 1 for winner, 3 repeats each."""
    held = {cast(str, case["id"]): cast(str, case["kind"]) for case in read_cases(scenario.out)
            if case["split"] == "holdout"}
    expected = len(held) if cases is None else cases
    assert len(held) == expected and set(held.values()) == set(kinds)
    gate = cast(dict[str, object], result["gate"])
    assert (gate["verdict"], gate["delta"], gate["se"], gate["n_shared"], gate["cases"], gate["repeats"]) == (
        "promote", 1.0, 0.0, expected, expected, 3)
    outcomes = holdout(read_record(scenario.out))
    assert len(outcomes) == expected * 6
    for case_id in held:
        for arm, score in (("baseline", 0.0), ("winner", 1.0)):
            selected = [item for item in outcomes if item["case_id"] == case_id and item["arm"] == arm]
            assert sorted(cast(int, item["repeat"]) for item in selected) == [0, 1, 2]
            assert {item["score"] for item in selected} == {score}


def archive_witnesses(scenario: Scenario) -> set[str]:
    winner = read_record(scenario.out)["winner_hash"]
    return {cast(str, event["archive_sha256"]) for event in read_events(scenario.out)
            if event.get("role") == "task" and event.get("candidate") == winner
            and "archive_sha256" in event}


def uv_bin_dir() -> Path:
    executable = shutil.which("uv")
    assert executable is not None
    path = Path(executable)
    if path.parent.name == "shims":
        selected = subprocess.run(["mise", "which", "uv"], capture_output=True,
                                  text=True, check=True)
        path = Path(selected.stdout.strip())
    assert path.is_file()
    return path.parent


def raw_holdout(scenario: Scenario) -> tuple[float, float, float | None, int]:
    """Recompute delta, SE, token change, and witnessed task calls from the raw vendor log."""
    record = read_record(scenario.out)
    task_logs = [entry for entry in read_events(scenario.out) if entry["role"] == "task"]
    differences: list[float] = []
    token_changes: list[float] = []
    witnessed = 0
    for case in read_cases(scenario.out):
        if case["split"] != "holdout":
            continue
        source = cast(dict[str, str], case["files"])["fixture.md"]
        scores: dict[str, list[float]] = {}
        tokens: dict[str, list[int]] = {}
        for arm, marker in (("baseline", record["seed_hash"]), ("winner", record["winner_hash"])):
            calls = [entry for entry in task_logs if entry["candidate"] == marker
                     and entry["visible_input"] == source]
            assert len(calls) == 3 + (arm == "winner")
            calls = calls[:3]
            witnessed += len(calls)
            scores[arm] = [float(cast(dict[str, str], json.loads(
                cast(dict[str, str], entry["answer"])["result_json"]))["text"] == source.upper())
                for entry in calls]
            usages = [cast(dict[str, int] | None, entry["usage"]) for entry in calls]
            if all(usage is not None for usage in usages):
                tokens[arm] = [usage["input_tokens"] - usage["cached_input_tokens"] + usage["output_tokens"]
                               for usage in usages if usage is not None]
        differences.append(statistics.fmean(scores["winner"]) - statistics.fmean(scores["baseline"]))
        if len(tokens) == 2:
            token_changes.append(statistics.fmean(tokens["winner"]) / statistics.fmean(tokens["baseline"]) - 1)
    replays = [item for item in cast(list[dict[str, object]], record["outcomes"]) if item.get("arm") == "replay"]
    assert len(replays) == len(differences)
    delta = statistics.fmean(differences)
    se = statistics.stdev(differences) / math.sqrt(len(differences))
    return delta, se, statistics.fmean(token_changes) if len(token_changes) == len(differences) else None, witnessed

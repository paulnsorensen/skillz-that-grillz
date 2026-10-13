"""Drive the public CLI through approvals and a real offline command adapter."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from skillz_experiments.harness._claude import _sandbox


FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[5]
CLI = ROOT / ".venv/bin/skillz-experiment"
BUNDLE = ROOT / "skills/skillz/scripts/skillz-experiment.pyz"
PYTHON = Path("/usr/bin/python3")
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
        for name, source in (("claude", FIXTURES / "claude.py"), ("bwrap", FIXTURES / "bwrap.py")):
            target = directory / name
            _ = shutil.copyfile(source, target)
            target.chmod(0o755)
        for name in host_path_tools():
            stub = directory / name
            if not stub.exists():
                _ = stub.write_text(f"#!/bin/sh\necho '{name}: e2e stub, not the real tool' >&2\nexit 1\n")
                stub.chmod(0o755)
        missing = [name for name in host_path_tools() if shutil.which(name, path=str(directory)) is None]
        assert not missing, f"the vendor bin must stub every PATH tool the host check needs: {missing}"
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
                      "files": {"fixture.md": source}, "expected": {"text": source.upper()},
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
    code, first, error = call_cli(args + command, home, cli=cli, bin_dirs=path_dirs, env_overrides=env_overrides)
    assert code == 1 and json.loads(error)["code"] == "cases-missing", (first, error)
    if harness == "claude":
        rows = cast(list[dict[str, str]], cast(dict[str, object], first["doctor"])["checks"])
        tools = [row for row in rows if row["check"].startswith("tool:")]
        assert tools and all(row["status"] == "pass" for row in tools), tools
    _ = (scenario.out / "cases.draft.json").write_text(json.dumps(scenario.draft))
    code, cases, error = call_cli(args + command, home, cli=cli, bin_dirs=path_dirs, env_overrides=env_overrides)
    assert code == 1 and json.loads(error)["code"] == "cases-unapproved", (cases, error)
    case_hash = cast(str, cases["case_hash"])
    code, budget, error = call_cli(args + ["--approve-cases", case_hash] + command, home,
                                   cli=cli, bin_dirs=path_dirs, env_overrides=env_overrides)
    assert code == 1 and json.loads(error)["code"] == "budget-unapproved", (budget, error)
    calls = cast(dict[str, int], budget["estimate"])["calls"]
    code, result, error = call_cli(args + ["--approve-cases", case_hash, "--approve-budget", str(calls)] + command,
                                   home, cli=cli, bin_dirs=path_dirs, env_overrides=env_overrides)
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
    return applied

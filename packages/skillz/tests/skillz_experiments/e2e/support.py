"""Drive the public CLI through approvals and a real offline command adapter."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast


FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[5]
CLI = ROOT / ".venv/bin/skillz-experiment"
BUNDLE = ROOT / "skills/skillz/scripts/skillz-experiment.pyz"
PYTHON = Path("/usr/bin/python3")


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
        for name, source in (("fake_claude.py", FIXTURES.parents[1] / "fixtures/fake_claude.py"),
                             ("vendor_common.py", FIXTURES / "vendor_common.py"),
                             ("journey.py", FIXTURES / "journey.py"),
                             ("targets.py", FIXTURES / "targets.py")):
            (directory / name).symlink_to(source)
        credential = home / ".claude/.credentials.json"
        credential.parent.mkdir(exist_ok=True)
        _ = credential.write_text(json.dumps({"claudeAiOauth": {"accessToken": "test", "refreshToken": "test"}}))
        credential.chmod(0o600)
    elif harness == "codex":
        target = directory / "codex"
        _ = shutil.copyfile(FIXTURES / "codex.py", target)
        target.chmod(0o755)
        (directory / "vendor_common.py").symlink_to(FIXTURES / "vendor_common.py")
        (directory / "targets.py").symlink_to(FIXTURES / "targets.py")
        credential = home / ".codex/auth.json"
        credential.parent.mkdir(exist_ok=True)
        _ = credential.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "test"}}))
        credential.chmod(0o600)
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


def call_cli(args: list[str], home: Path, *, cli: Path = CLI, bin_dir: Path | None = None) -> tuple[int, dict[str, object], str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    _ = env.pop("CODEX_HOME", None)
    _ = env.pop("CLAUDE_CONFIG_DIR", None)
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
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


def run_scenario(scenario: Scenario, home: Path, *, helper: bool = False, harness: str = "command",
                 mode: str = "normal", cli: Path = CLI, extra_flags: tuple[str, ...] = (),
                 bin_dir: Path | None = None) -> dict[str, object]:
    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out),
            "--model", "offline-test", "--harness", harness, "--live",
            "--edit", "prose+cli" if helper else "prose", *extra_flags]
    if harness == "command":
        settings = scenario.out / "vendor-settings.json"
        _ = settings.write_text(json.dumps({"mode": mode, "log": str(scenario.out / "vendor.jsonl")}))
        command = [item for value in (str(PYTHON), str(FIXTURES / "command.py"), str(settings))
                   for item in ("--command", value)]
    else:
        bin_dir = make_vendor_bin(scenario, home, harness, mode)
        command = []
    code, first, error = call_cli(args + command, home, cli=cli, bin_dir=bin_dir)
    assert code == 1 and json.loads(error)["code"] == "cases-missing", (first, error)
    _ = (scenario.out / "cases.draft.json").write_text(json.dumps(scenario.draft))
    code, cases, error = call_cli(args + command, home, cli=cli, bin_dir=bin_dir)
    assert code == 1 and json.loads(error)["code"] == "cases-unapproved", (cases, error)
    case_hash = cast(str, cases["case_hash"])
    code, budget, error = call_cli(args + ["--approve-cases", case_hash] + command, home,
                                   cli=cli, bin_dir=bin_dir)
    assert code == 1 and json.loads(error)["code"] == "budget-unapproved", (budget, error)
    calls = cast(dict[str, int], budget["estimate"])["calls"]
    code, result, error = call_cli(args + ["--approve-cases", case_hash, "--approve-budget", str(calls)] + command,
                                   home, cli=cli, bin_dir=bin_dir)
    assert code == 0, (result, error)
    assert result["phase"] == "complete"
    return result


def export_and_apply(scenario: Scenario, home: Path, *, cli: Path = CLI) -> Path:
    code, result, error = call_cli(["export", str(scenario.out), "--out", str(scenario.export)], home, cli=cli)
    assert code == 0, (result, error)
    applied = home / "applied"
    _ = shutil.copytree(scenario.target, applied)
    patch = scenario.export / "candidate.patch"
    command = subprocess.run(["patch", "-p1", "-i", str(patch)], cwd=applied,
                             capture_output=True, text=True, check=False)
    assert command.returncode == 0, command.stderr
    return applied

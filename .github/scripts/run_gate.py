"""Run the repository quality checks with bounded parallel jobs."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import BinaryIO

Job = tuple[str, list[str]]
ROOT = Path(__file__).resolve().parents[2]
_ACTIVE: dict[subprocess.Popen[bytes], tuple[str, BinaryIO]] = {}
_cancelled = False
PACKAGE_MODES = frozenset({"fromargs", "agent-session-analytics"})
LIVE_FLAG = "SKILLZ_LIVE"
SKIP_VARIABLE = "GATE_SKIP"
# `skillz` mode only: run one shard of each skillz lane through the `pytest_shard` plugin.
SHARD_VARIABLE = "GATE_SHARD"
# CI runs these lanes in their own job (`just test-skillz`) and skips them in the gate job.
SKILLZ_LANES = ("skillz-e2e-pytest", "skillz-pytest")
# The requirement that `test_generated_target.py` locks offline.
OFFLINE_LOCK_DEPENDENCY = "cyclopts==5.2.0"


def _environment() -> dict[str, str]:
    """Return the step environment without `SKILLZ_LIVE`, so no gate step makes a real model call.

    With `GATE_SHARD` set, put this directory on `PYTHONPATH`, so pytest can load the `pytest_shard` plugin.
    """
    environment = {name: value for name, value in os.environ.items() if name != LIVE_FLAG}
    if environment.get(SHARD_VARIABLE):
        scripts = str(Path(__file__).resolve().parent)
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, [scripts, environment.get("PYTHONPATH")]))
    return environment


def parse_shard(value: str) -> tuple[int, int]:
    """Return `(index, count)` from `<index>/<count>`, with `1 <= index <= count`."""
    index, separator, count = value.partition("/")
    if separator and index.isdigit() and count.isdigit() and 1 <= int(index) <= int(count):
        return int(index), int(count)
    raise ValueError(f"{SHARD_VARIABLE} must be <index>/<count> with 1 <= index <= count: {value!r}")


def _check_cancelled() -> None:
    if _cancelled:
        raise KeyboardInterrupt


def _start(name: str, command: list[str]) -> bool:
    _check_cancelled()
    print(f"→ {name}", flush=True)
    output = tempfile.TemporaryFile()
    try:
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True, env=_environment(),
        )
    except OSError as error:
        output.close()
        print(f"✗ {name}\n{error}", flush=True)
        return False
    _ACTIVE[process] = (name, output)
    _check_cancelled()
    return True


def _finish(process: subprocess.Popen[bytes]) -> bool:
    name, output = _ACTIVE.pop(process)
    status = process.wait()
    if status:
        _ = output.seek(0)
        print(f"✗ {name}\n{output.read().decode(errors='replace')}", end="", flush=True)
    else:
        print(f"✓ {name}", flush=True)
    output.close()
    return status == 0


def run_step(name: str, command: list[str]) -> bool:
    if not _start(name, command):
        return False
    process = next(iter(_ACTIVE))
    while process.poll() is None:
        _check_cancelled()
        time.sleep(0.02)
    _check_cancelled()
    return _finish(process)


def run_jobs(jobs: list[Job], workers: int) -> bool:
    pending = iter(jobs)
    passed = True
    while True:
        _check_cancelled()
        while len(_ACTIVE) < workers:
            _check_cancelled()
            try:
                name, command = next(pending)
            except StopIteration:
                break
            if not _start(name, command):
                passed = False
        if not _ACTIVE:
            return passed
        finished = [process for process in _ACTIVE if process.poll() is not None]
        if not finished:
            time.sleep(0.02)
            continue
        for process in finished:
            if not _finish(process):
                passed = False


def _stop_children() -> None:
    children = tuple(_ACTIVE)
    for process in children:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for process in children:
        try:
            _ = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _ = process.wait(timeout=2)
        _name, output = _ACTIVE.pop(process)
        output.close()


def _test_jobs(test_workers: int, cache_root: Path) -> list[Job]:
    root = ["uv", "run", "--locked", "--no-sync", "--all-groups"]
    wedge = ["uv", "run", "--locked", "--no-sync", "--project", "packages/wedge"]
    skillz = ["uv", "run", "--locked", "--no-sync", "--project", "packages/skillz"]
    fromargs = ["uv", "run", "--locked", "--no-sync", "--project", "packages/fromargs"]
    analytics = ["uv", "run", "--locked", "--no-sync", "--project", "packages/agent-session-analytics"]
    # The slow process-level e2e suite runs as its own job, in parallel with the skillz unit tests.
    skillz_e2e = "packages/skillz/tests/skillz_experiments/e2e"
    return [
        ("skillz-e2e-pytest", skillz + ["pytest", skillz_e2e, "-q", "-n", str(test_workers),
                                  "--dist", "worksteal", "--basetemp", str(cache_root / "e"),
                                  "-o", f"cache_dir={cache_root / 'ec'}"]),
        ("wedge-pytest", wedge + ["pytest", "packages/wedge/tests", "-q", "-n", str(test_workers),
                            "--dist", "worksteal", "--basetemp", str(cache_root / "l"),
                            "-o", f"cache_dir={cache_root / 'lc'}"]),
        ("skillz-pytest", skillz + ["pytest", "packages/skillz/tests", "--ignore", skillz_e2e,
                              "-q", "-n", str(test_workers),
                              "--dist", "worksteal", "--basetemp", str(cache_root / "s"),
                              "-o", f"cache_dir={cache_root / 'sc'}"]),
        ("fromargs-pytest", fromargs + ["pytest", "packages/fromargs/tests", "-q", "-n", str(test_workers),
                                      "--dist", "worksteal", "--basetemp", str(cache_root / "f"),
                                      "-o", f"cache_dir={cache_root / 'fc'}"]),
        ("skill-validator-tests", root + ["python", ".github/scripts/test_validate_skills.py"]),
        ("eval-validator-tests", root + ["python", ".github/scripts/test_validate_evals.py"]),
        ("skill-validator", root + ["python", ".github/scripts/validate_skills.py"]),
        ("eval-validator", root + ["python", ".github/scripts/validate_evals.py"]),
        ("reference-tests", root + ["python", ".github/scripts/test_check_skillz_references.py"]),
        ("reference-check", root + ["python", ".github/scripts/check_skillz_references.py"]),
        ("architecture-tests", root + ["python", ".github/scripts/test_check_architecture.py"]),
        ("architecture-check", root + ["python", ".github/scripts/check_architecture.py"]),
        ("ac-trace-tests", root + ["python", ".github/scripts/test_check_ac_trace.py"]),
        ("ac-trace", root + ["python", ".github/scripts/check_ac_trace.py"]),
        ("dead-code-tests", root + ["python", ".github/scripts/test_check_dead_code.py"]),
        ("gate-runner-tests", root + ["python", ".github/scripts/test_run_gate.py"]),
        ("agent-session-analytics-pytest", analytics + ["pytest", "packages/agent-session-analytics/tests", "-q", "-n", str(test_workers),
                                                "--dist", "worksteal", "--basetemp", str(cache_root / "a"),
                                                "-o", f"cache_dir={cache_root / 'ac'}"]),
        ("fromargs-typecheck", fromargs + ["basedpyright", "--project", "packages/fromargs"]),
        ("wedge-check", wedge + ["wedge", "check", "--root", "packages/wedge/examples/skills",
                              "--root", "packages/wedge/examples/consumer/skills"]),
        ("wedge-bundle", wedge + ["wedge", "bundle", "skills/skillz", "--check"]),
    ]


def _check_jobs() -> list[Job]:
    root = ["uv", "run", "--locked", "--no-sync", "--all-groups"]
    return [
        ("yaml", ["just", "lint-yaml"]),
        ("typecheck", root + ["basedpyright"]),
        ("scripts-typecheck", root + ["basedpyright", "--project", ".github/scripts"]),
        ("py-dead-code", root + ["python", ".github/scripts/check_dead_code.py"]),
    ]


def _skipped() -> frozenset[str]:
    """Return the lane names in `GATE_SKIP`; reject a name that is not a lane."""
    names = frozenset(part.strip() for part in os.environ.get(SKIP_VARIABLE, "").split(",") if part.strip())
    lanes = {name for name, _ in _test_jobs(1, Path("/tmp")) + _check_jobs()}
    unknown = sorted(names - lanes)
    if unknown:
        raise ValueError(f"{SKIP_VARIABLE} names unknown lanes: {', '.join(unknown)}")
    return names


def _without(jobs: list[Job], skip: frozenset[str]) -> list[Job]:
    for name, _ in jobs:
        if name in skip:
            print(f"- {name} (skipped by {SKIP_VARIABLE})", flush=True)
    return [job for job in jobs if job[0] not in skip]


def _prepare() -> bool:
    """Sync the workspace, then warm the uv index cache for the offline e2e locks.

    `uv sync` stores wheels but not registry index metadata. The e2e tests run `uv lock --offline`
    on a scratch project that pins `OFFLINE_LOCK_DEPENDENCY`. Without this metadata, that lock fails on a cold cache.
    One online lock of the same requirement stores it. Keep the pin equal to the pin in the e2e `_world` helpers.
    """
    if not run_step("workspace-env", ["uv", "sync", "--locked", "--all-packages", "--all-groups", "--all-extras"]):
        return False
    with tempfile.TemporaryDirectory() as scratch:
        project = Path(scratch)
        _ = (project / "pyproject.toml").write_text(
            f'[project]\nname = "warm-index"\nversion = "0"\ndependencies = ["{OFFLINE_LOCK_DEPENDENCY}"]\n'
        )
        return run_step("index-cache", ["uv", "lock", "--project", str(project)])


def run_gate(mode: str, workers: int, test_workers: int, temp_root: Path, skip: frozenset[str]) -> int:
    formatters = (
        [("markdown", ["just", "lint-md-fix"]), ("yaml-fmt", ["just", "lint-yaml-fmt-fix"])]
        if mode == "fix" else
        [("markdown", ["just", "lint-md"]), ("yaml-fmt", ["just", "lint-yaml-fmt"])]
    )
    for name, command in formatters:
        if not run_step(name, command):
            return 1
    if not _prepare():
        return 1
    jobs = _without(_test_jobs(test_workers, temp_root) + _check_jobs(), skip)
    return 0 if run_jobs(jobs, workers) else 1


def _workers(name: str) -> int:
    value = os.environ.get(name, "4")
    try:
        number = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer: {value!r}") from error
    if number < 1:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return number


def _interrupted(_signum: int, _frame: object) -> None:
    global _cancelled
    _cancelled = True


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in {"fix", "check", "test", "skillz", *PACKAGE_MODES} or (
        args[0] not in PACKAGE_MODES and len(args) != 1
    ) or len(args) > 2:
        print("usage: run_gate.py fix|check|test|skillz|fromargs|agent-session-analytics [python]", file=sys.stderr)
        return 2
    try:
        workers, test_workers = _workers("GATE_JOBS"), _workers("TEST_WORKERS")
        skip = _skipped()
        shard = os.environ.get(SHARD_VARIABLE, "")
        if shard and args[0] != "skillz":
            raise ValueError(f"{SHARD_VARIABLE} applies only to `run_gate.py skillz`")
        if shard:
            _ = parse_shard(shard)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    global _cancelled
    _cancelled = False
    previous_term = signal.signal(signal.SIGTERM, _interrupted)
    previous_int = signal.signal(signal.SIGINT, _interrupted)
    try:
        with tempfile.TemporaryDirectory(prefix="sg-", dir="/tmp") as directory:
            temp_root = Path(directory)
            try:
                if args[0] in PACKAGE_MODES:
                    package = args[0]
                    command = ["uv", "run", "--locked"]
                    if len(args) == 2 and args[1]:
                        command += ["--python", args[1]]
                    command += ["--project", f"packages/{package}", "pytest", f"packages/{package}/tests",
                                "-q", "-n", str(test_workers), "--dist", "worksteal",
                                "--basetemp", str(temp_root / "f"),
                                "-o", f"cache_dir={temp_root / 'fc'}"]
                    return 0 if run_step(f"{package}-pytest", command) else 1
                if args[0] in {"test", "skillz"}:
                    jobs = _test_jobs(test_workers, temp_root)
                    if args[0] == "skillz":
                        plugin = ["-p", "pytest_shard"] if shard else []
                        jobs = [(name, command + plugin) for name, command in jobs if name in SKILLZ_LANES]
                    jobs = _without(jobs, skip)
                    if not _prepare():
                        return 1
                    return 0 if run_jobs(jobs, workers) else 1
                return run_gate(args[0], workers, test_workers, temp_root, skip)
            except KeyboardInterrupt:
                _stop_children()
                print("interrupted", file=sys.stderr)
                return 130
    finally:
        _ = signal.signal(signal.SIGTERM, previous_term)
        _ = signal.signal(signal.SIGINT, previous_int)


if __name__ == "__main__":
    sys.exit(main())

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
            start_new_session=True,
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
    return [
        ("wedge-pytest", wedge + ["pytest", "packages/wedge/tests", "-q", "-n", str(test_workers),
                            "--dist", "worksteal", "--basetemp", str(cache_root / "l"),
                            "-o", f"cache_dir={cache_root / 'lc'}"]),
        ("skillz-pytest", skillz + ["pytest", "packages/skillz/tests", "-q", "-n", str(test_workers),
                              "--dist", "worksteal", "--basetemp", str(cache_root / "s"),
                              "-o", f"cache_dir={cache_root / 'sc'}"]),
        ("fromargs-pytest", fromargs + ["pytest", "packages/fromargs/tests", "-q", "-n", str(test_workers),
                                      "--dist", "worksteal", "--basetemp", str(cache_root / "f"),
                                      "-o", f"cache_dir={cache_root / 'fc'}"]),
        ("skill-validator-tests", root + ["python", ".github/scripts/test_validate_skills.py"]),
        ("eval-validator-tests", root + ["python", ".github/scripts/test_validate_evals.py"]),
        ("skill-validator", root + ["python", ".github/scripts/validate_skills.py"]),
        ("reference-tests", root + ["python", ".github/scripts/test_check_skillz_references.py"]),
        ("reference-check", root + ["python", ".github/scripts/check_skillz_references.py"]),
        ("dead-code-tests", root + ["python", ".github/scripts/test_check_dead_code.py"]),
        ("gate-runner-tests", root + ["python", ".github/scripts/test_run_gate.py"]),
        ("engine-tests", ["uv", "run", "--no-project", "--python", "3.11", "python", "-B",
                          "-m", "unittest", "discover", "-s", "skills/skillz/engine/tests", "-p", "test_*.py"]),
        ("fromargs-typecheck", fromargs + ["basedpyright", "--project", "packages/fromargs"]),
        ("wedge-check", wedge + ["wedge", "check", "--root", "packages/wedge/examples/skills",
                              "--root", "packages/wedge/examples/consumer/skills"]),
        ("wedge-bundle", wedge + ["wedge", "bundle", "skills/skillz", "--check"]),
    ]


def _prepare() -> bool:
    return run_step("workspace-env", ["uv", "sync", "--locked", "--all-packages", "--all-groups", "--all-extras"])


def run_gate(mode: str, workers: int, test_workers: int, temp_root: Path) -> int:
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
    root = ["uv", "run", "--locked", "--no-sync", "--all-groups"]
    checks = [
        ("yaml", ["just", "lint-yaml"]),
        ("typecheck", root + ["basedpyright"]),
        ("scripts-typecheck", root + ["basedpyright", "--project", ".github/scripts"]),
        ("py-dead-code", root + ["python", ".github/scripts/check_dead_code.py"]),
    ]
    jobs = _test_jobs(test_workers, temp_root) + checks
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
    if not args or args[0] not in {"fix", "check", "test", "fromargs"} or (
        args[0] != "fromargs" and len(args) != 1
    ) or len(args) > 2:
        print("usage: run_gate.py fix|check|test|fromargs [python]", file=sys.stderr)
        return 2
    try:
        workers, test_workers = _workers("GATE_JOBS"), _workers("TEST_WORKERS")
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
                if args[0] == "fromargs":
                    command = ["uv", "run", "--locked"]
                    if len(args) == 2 and args[1]:
                        command += ["--python", args[1]]
                    command += ["--project", "packages/fromargs", "pytest", "packages/fromargs/tests",
                                "-q", "-n", str(test_workers), "--dist", "worksteal",
                                "--basetemp", str(temp_root / "f"),
                                "-o", f"cache_dir={temp_root / 'fc'}"]
                    return 0 if run_step("fromargs-pytest", command) else 1
                if args[0] == "test":
                    if not _prepare():
                        return 1
                    return 0 if run_jobs(_test_jobs(test_workers, temp_root), workers) else 1
                return run_gate(args[0], workers, test_workers, temp_root)
            except KeyboardInterrupt:
                _stop_children()
                print("interrupted", file=sys.stderr)
                return 130
    finally:
        _ = signal.signal(signal.SIGTERM, previous_term)
        _ = signal.signal(signal.SIGINT, previous_int)


if __name__ == "__main__":
    sys.exit(main())

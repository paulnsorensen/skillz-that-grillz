"""Shared fixtures for the skillz experiment tests."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import cast

import pytest

from skillz_experiments import _claude, _workflow
from skillz_experiments._harness import Configuration
from skillz_experiments._intake import DRAFT_NAME
from skillz_experiments._search import Edit
from skillz_experiments._workflow import Factory, Stop, run

FIXTURES = Path(__file__).parent / "fixtures"
INSPECTOR = Path(__file__).resolve().parents[3] / "skills/skillz/scripts/inspect_skill.py"


@pytest.fixture(scope="session")
def inspector() -> ModuleType:
    """Load `inspect_skill.py` once, so a broken helper fails tests instead of collection."""
    spec = importlib.util.spec_from_file_location("inspect_skill", INSPECTOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def limits(inspector: ModuleType) -> tuple[int, int]:
    """Return the inspector's `(ADVISORY_WORDS, MAX_WORDS)` sentence limits."""
    return cast(int, getattr(inspector, "ADVISORY_WORDS")), cast(int, getattr(inspector, "MAX_WORDS"))


@pytest.fixture(scope="session")
def harness_bin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Make stub `claude` and `codex` executables once per session."""
    directory = tmp_path_factory.mktemp("harness-bin")
    for name in ("claude", "codex"):
        stub = directory / name
        _ = stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    return directory


@pytest.fixture(autouse=True)
def harness_on_path(harness_bin: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the stub harness executables on PATH, so `run` does not depend on the host's `claude` or `codex`.

    `run` resolves the built-in harness before the first approval stop. A test that checks
    `harness-missing` sets its own PATH.
    """
    monkeypatch.setenv("PATH", f"{harness_bin}{os.pathsep}{os.environ.get('PATH', '')}")


@pytest.fixture(autouse=True)
def nested_userns_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report that the host allows a nested user namespace, so the sandbox settings do not depend on the host's AppArmor.

    A test of the blocked case patches `_claude.nested_userns_blocked` itself.
    """
    monkeypatch.setattr(_claude, "nested_userns_blocked", lambda: False)


@pytest.fixture(autouse=True)
def host_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a ready host to `run`, so the stub harness executables pass the free host checks.

    The doctor tests call `_doctor.doctor` directly.
    """
    def ready(harness: str, isolation: str = "claude") -> dict[str, object]:
        return {"ok": True, "harness": harness, "isolation": isolation, "checks": [], "live_calls": 0}
    monkeypatch.setattr(_workflow, "doctor", ready)

@pytest.fixture
def umask_022() -> Iterator[None]:
    """Run a privacy test under the common umask, so only the explicit file and directory modes make it private."""
    previous = os.umask(0o022)
    try:
        yield
    finally:
        _ = os.umask(previous)


@pytest.fixture
def host_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give each test a fake host login and a private temp dir, so no test touches the real `~/.claude`."""
    credential = tmp_path / "host-home/.claude/.credentials.json"
    credential.parent.mkdir(parents=True)
    _ = credential.write_text("{}")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("HOME", str(credential.parents[1]))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setattr(_claude, "SANDBOX_HELPERS", ())
    return credential


def _make_target(tmp_path: Path, *, helper: bool = False) -> Path:
    """Copy the echo skill and give it a one-kind contract, so every drafted task case scores with `echo`."""
    target = tmp_path / "echo-skill"
    _ = shutil.copytree(FIXTURES / "echo-skill", target)
    _ = (target / "SKILL.md.fixture").rename(target / "SKILL.md")
    contract: dict[str, object] = {
        "schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
        "kinds": {"echo": {"grader": "exact-json"}}, "editable": []}
    if helper:
        (target / "scripts").mkdir()
        _ = (target / "scripts/echo.py").write_text("import json\nprint(json.dumps({'ok': True}))\n")
        contract["helper"] = {"path": "scripts/echo.py", "input": "fixture.md",
                              "fixtures": [{"input": "hi", "returncode": 0, "output": {"ok": True}}]}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(contract))
    return target


def _write_draft(out: Path, families: int = 5, per_family: int = 2) -> list[str]:
    """Write a case draft and return the case ids."""
    cases: list[dict[str, object]] = []
    for family in range(families):
        for index in range(per_family):
            name = f"t{family}-{index}"
            cases.append({"id": name, "family": f"f{family}", "kind": "task", "request": f"request-{name}-end",
                          "files": {"fixture.md": "hello\n"}, "expected": {}, "source": "skill"})
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = (out / DRAFT_NAME).write_text(json.dumps(cases), encoding="utf-8")
    return [str(case["id"]) for case in cases]


def _approvals(target: Path, out: Path, model: str = "local-test", *, edit: Edit = "prose") -> tuple[str, int]:
    """Walk the two stops. Return the case hash and the call count that the user approves."""
    with pytest.raises(Stop) as cases:
        _ = run(target, out, model, live=True, edit=edit)
    assert cases.value.code == "cases-unapproved"
    case_hash = cast(str, cases.value.data["case_hash"])
    with pytest.raises(Stop) as budget:
        _ = run(target, out, model, live=True, approve_cases=case_hash, edit=edit)
    assert budget.value.code == "budget-unapproved"
    estimate = cast(dict[str, int], budget.value.data["estimate"])
    return case_hash, estimate["calls"]


def _approved_run(target: Path, out: Path, *, model: str = "local-test", factory: Factory | None = None,
                 configuration: Configuration | None = None) -> dict[str, object]:
    """Write a draft when none exists, give both approvals, then run to the end."""
    if not (out / DRAFT_NAME).exists():
        _ = _write_draft(out)
    case_hash, calls = _approvals(target, out, model)
    return run(target, out, model, live=True, approve_cases=case_hash, approve_budget=calls, factory=factory,
               configuration=configuration)


@pytest.fixture
def make_target() -> Callable[..., Path]:
    """Hand the `run` test helper `make_target` to a test."""
    return _make_target


@pytest.fixture
def write_draft() -> Callable[..., list[str]]:
    """Hand the `run` test helper `write_draft` to a test."""
    return _write_draft


@pytest.fixture
def approvals() -> Callable[..., tuple[str, int]]:
    """Hand the `run` test helper `approvals` to a test."""
    return _approvals


@pytest.fixture
def approved_run() -> Callable[..., dict[str, object]]:
    """Hand the `run` test helper `approved_run` to a test."""
    return _approved_run

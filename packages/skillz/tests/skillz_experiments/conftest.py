"""Shared fixtures for the skillz experiment tests."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import weakref
from pathlib import Path
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import NamedTuple, cast

import pytest

from in_process_claude import Spawn, route
from skillz_experiments import _runtime
from skillz_experiments._budget import Budget
from skillz_experiments.harness._command import Command
from skillz_experiments.harness._roles import Role
from skillz_experiments.harness._transport import Transport
from skillz_experiments.run import _run
from skillz_experiments.harness import Configuration, _codex
from skillz_experiments.harness._claude import _options, _sandbox, _transport
from skillz_experiments._intake import DRAFT_NAME
from skillz_experiments.search import Edit
from skillz_experiments.run import Stop, _headroom, run
from skillz_experiments.run._state import Factory

FIXTURES = Path(__file__).parent / "fixtures"
_REAL_NESTED_USERNS_BLOCKED = _sandbox.nested_userns_blocked


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

    A test of the blocked case patches `_sandbox.nested_userns_blocked` itself.
    """
    monkeypatch.setattr(_transport, "nested_userns_blocked", lambda: False)


@pytest.fixture
def real_nested_userns_probe(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[], bool]]:
    """Restore the real, cached `nested_userns_blocked` for a test of the probe itself, and clear its cache around the test."""
    monkeypatch.setattr(_transport, "nested_userns_blocked", _REAL_NESTED_USERNS_BLOCKED)
    cache_clear = cast(Callable[[], None], getattr(_REAL_NESTED_USERNS_BLOCKED, "cache_clear"))
    cache_clear()
    yield _REAL_NESTED_USERNS_BLOCKED
    cache_clear()


@pytest.fixture(scope="session")
def managed_python_root() -> Path | None:
    """Return the uv managed-Python root of the host, or `None` without `uv`.

    The session reads it once, before a test moves `HOME`.
    """
    uv = shutil.which("uv")
    if uv is None:
        return None
    return Path(subprocess.run([uv, "python", "dir"], check=True, capture_output=True, text=True).stdout.strip())


@pytest.fixture(autouse=True)
def shared_managed_python(managed_python_root: Path | None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point `UV_PYTHON_INSTALL_DIR` at the managed-Python root of the host.

    A wedge build finds or installs its pinned CPython builds below this root.
    Without it, a test that moves `HOME` installs about 400 MB of CPython builds below its `tmp_path`.
    A test of a missing or cold root sets or deletes the variable itself.
    """
    if managed_python_root is not None:
        monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(managed_python_root))


@pytest.fixture(autouse=True)
def history_state(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `XDG_STATE_HOME` at a fresh temp directory, so no test reads or writes the real cross-run history store.

    A CLI subprocess inherits the variable. Return the state root.
    """
    root = tmp_path_factory.mktemp("xdg-state")
    monkeypatch.setenv("XDG_STATE_HOME", str(root))
    return root


@pytest.fixture(autouse=True)
def host_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a ready host to `run`, so the stub harness executables pass the free host checks.

    The doctor tests call `_doctor.doctor` directly.
    """
    def ready(selections: Sequence[tuple[str, str | None]]) -> dict[str, object]:
        return {"ok": True, "harness": "+".join(harness for harness, _ in selections), "checks": [], "live_calls": 0}
    monkeypatch.setattr(_run, "host_report", ready)


def _no_sleep(seconds: float) -> None:
    del seconds


@pytest.fixture(autouse=True)
def codex_backoff_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the Codex retry backoff return at once, so no test waits for a real backoff.

    A test that checks the backoff values requests the `sleeps` fixture of `test_codex_retry.py`. That fixture runs
    after this one and records each wait.
    """
    monkeypatch.setattr(_codex, "_sleep", _no_sleep)


READY_SECONDS = 20
"""The longest time that `clock_at_marker` waits for a child to write its readiness marker."""


@pytest.fixture
def clock_at_marker(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path], list[float]]:
    """Start the time limit of a child when the child writes a readiness marker, not when the child starts.

    Call the returned function with the marker path. From then on, the first timed `Popen.communicate` of each child
    waits for the marker, then runs with the limit that the code under test passes. A slow interpreter start on a
    loaded host then cannot use up a short limit. A child that ends before the marker exists returns at once.
    The function returns a list. The gate appends the time at which it saw the marker, once for each gated call.
    """
    real = cast(Callable[[subprocess.Popen[str], str | None, float | None], tuple[str, str]],
                subprocess.Popen[str].communicate)

    def arm(marker: Path) -> list[float]:
        seen: list[float] = []
        gated: weakref.WeakSet[subprocess.Popen[str]] = weakref.WeakSet()

        def communicate(self: subprocess.Popen[str], data: str | None = None,
                        timeout: float | None = None) -> tuple[str, str]:
            if timeout is None or self in gated:
                return real(self, data, timeout)
            gated.add(self)
            give_up = time.monotonic() + READY_SECONDS
            while not marker.exists():
                if time.monotonic() > give_up:
                    pytest.fail(f"the child wrote no readiness marker {marker.name} in {READY_SECONDS} s")
                try:
                    return real(self, data, 0.02)
                except subprocess.TimeoutExpired:
                    data = None  # the first call sent the input
            seen.append(time.monotonic())
            return real(self, data, timeout)

        monkeypatch.setattr(subprocess.Popen, "communicate", communicate)
        return seen
    return arm


@pytest.fixture
def claude_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer each child whose argv[0] is an `in_process_claude` stub in this process. Every other child still starts."""
    monkeypatch.setattr(_runtime, "_spawn", route(cast(Spawn, getattr(_runtime, "_spawn"))))


@pytest.fixture
def umask_022() -> Iterator[None]:
    """Run a privacy test under the common umask, so only the explicit file and directory modes make it private."""
    previous = os.umask(0o022)
    try:
        yield
    finally:
        _ = os.umask(previous)


@pytest.fixture
def runner_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make `tmp_path` the temporary directory of this process and of its child processes, and return it.

    The runner makes each workspace below `tempfile.gettempdir()`, and the Gemini dotenv guard refuses other workspaces.
    `--basetemp` can put `tmp_path` outside `TMPDIR`: the gate uses `/tmp`, but macOS sets `TMPDIR` below `/var/folders`.
    """
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


@pytest.fixture
def host_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give each test a fake host login and a private temp dir, so no test touches the real `~/.claude`.

    The last PATH entry holds a stub `bwrap`, so the host-tool check passes on a Linux host without bubblewrap.
    Tests that run the real OS sandbox skip without it, and a real `bwrap` earlier on PATH still wins.
    The stub fails like a broken sandbox, so a test that runs it by mistake stops.
    """
    fallback = tmp_path / "fallback-bin"
    fallback.mkdir()
    bwrap = fallback / "bwrap"
    _ = bwrap.write_text("#!/bin/sh\necho 'bwrap: test stub, no OS sandbox' >&2\nexit 1\n")
    bwrap.chmod(0o755)
    monkeypatch.setenv("PATH", f"{os.environ.get('PATH', '')}{os.pathsep}{fallback}")
    credential = tmp_path / "host-home/.claude/.credentials.json"
    credential.parent.mkdir(parents=True)
    _ = credential.write_text("{}")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("HOME", str(credential.parents[1]))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setattr(_sandbox, "SANDBOX_HELPERS", ())
    monkeypatch.setattr(_options, "SOCKET_DENY", ())  # pytest roots live under /tmp, which production denies
    return credential


@pytest.fixture
def headroom_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lift the headroom stop for a test whose fake provider scores every seed case at 1.0 and does not test the stop."""
    monkeypatch.setattr(_headroom, "HEADROOM_CEILING", 1.1)


def _make_target(tmp_path: Path, *, helper: bool = False, surface: dict[str, object] | None = None) -> Path:
    """Copy the echo skill and give it a one-kind contract, so every drafted task case scores with `echo`."""
    target = tmp_path / "echo-skill"
    _ = shutil.copytree(FIXTURES / "echo-skill", target)
    _ = (target / "SKILL.md.fixture").rename(target / "SKILL.md")
    contract: dict[str, object] = {
        "schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
        "kinds": {"echo": {"grader": "exact-json"}}, "editable": []}
    if surface is not None:
        contract["surface"] = surface
    if helper:
        (target / "scripts").mkdir()
        _ = (target / "scripts/echo.py").write_text("import json\nprint(json.dumps({'ok': True}))\n")
        contract["helper"] = {"path": "scripts/echo.py", "input": "fixture.md",
                              "fixtures": [{"input": "hi", "returncode": 0, "output": {"ok": True}}]}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(contract))
    return target


def _write_draft(out: Path, families: int = 6, per_family: int = 2) -> list[str]:
    """Write a case draft and return the case ids. The default fills train 4, validation 2, and holdout 6."""
    cases: list[dict[str, object]] = []
    for family in range(families):
        for index in range(per_family):
            name = f"t{family}-{index}"
            cases.append({"id": name, "family": f"f{family}", "kind": "task", "request": f"request-{name}-end",
                          "files": {"fixture.md": "hello\n"},
                          "expected": {"criteria": [{"id": "answer", "text": "THE answer SHALL meet the request."}]},
                          "source": "skill"})
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = (out / DRAFT_NAME).write_text(json.dumps(cases), encoding="utf-8")
    return [str(case["id"]) for case in cases]


def _approvals(target: Path, out: Path, model: str = "local-test", *, edit: Edit = "prose",
               statistics: Mapping[str, float] | None = None) -> tuple[str, int]:
    """Walk the two stops. Return the case hash and the call count that the user approves.

    The approval covers the resolved statistics, so pass the flags that the later calls pass.
    """
    with pytest.raises(Stop) as cases:
        _ = run(target, out, model, live=True, edit=edit, statistics=statistics)
    assert cases.value.code == "cases-unapproved"
    case_hash = cast(str, cases.value.data["case_hash"])
    with pytest.raises(Stop) as budget:
        _ = run(target, out, model, live=True, approve_cases=case_hash, edit=edit, statistics=statistics)
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
def approved_run(headroom_off: None) -> Callable[..., dict[str, object]]:
    """Hand the `run` test helper `approved_run` to a test. Its fake providers score every seed case at 1.0."""
    del headroom_off
    return _approved_run


class JudgedRun(NamedTuple):
    """An intake target whose two approvals are given and whose built-in roles all run through the command wrapper."""

    target: Path
    out: Path
    case_hash: str
    calls: int
    settings: Path

    @property
    def log(self) -> Path:
        return self.settings.parent / "requests.jsonl"

    def start(self, *, live: bool = True, judge_model: str | None = None) -> dict[str, object]:
        """Call `run` with the approvals. The wrapper logs the model of every request."""
        return run(self.target, self.out, "task-model", live=live, approve_cases=self.case_hash,
                   approve_budget=self.calls, judge_model=judge_model)


@pytest.fixture
def judged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, headroom_off: None) -> JudgedRun:
    """Hand a `JudgedRun` to a test. Write `mode` into `settings` to change how the wrapper answers."""
    del headroom_off
    wrapper = tmp_path / "wrapper.py"
    _ = shutil.copyfile(FIXTURES / "command_wrapper.py", wrapper)
    settings = tmp_path / "settings.json"
    _ = settings.write_text(json.dumps({"mode": "normal", "log": str(tmp_path / "requests.jsonl")}))

    def create(self: Role, budget: Budget, checkpoint: Callable[[], None], *_unused: object) -> Transport:
        return Command((sys.executable, str(wrapper), str(settings)), self.model, budget, checkpoint)

    monkeypatch.setattr(Role, "create", create)
    target, out = _make_target(tmp_path), tmp_path / "run"
    (target / "evals/autoimprove.json").unlink()
    _ = _write_draft(out)
    case_hash, calls = _approvals(target, out, "task-model")
    return JudgedRun(target, out, case_hash, calls, settings)

"""Behavior tests for the parallel quality gate."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import cast
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_durations  # noqa: E402
import run_gate  # noqa: E402

RUNNER = Path(run_gate.__file__).resolve()
# A hang guard, not a speed check: a starved host can take far longer than 10 s to start a child.
HANG_GUARD = 120
FAKE_TOOL = """#!/usr/bin/env python3
import os, pathlib, signal, subprocess, sys, time
args = sys.argv[1:]
with open(os.environ['EVENT_LOG'], 'a') as log:
    log.write(pathlib.Path(sys.argv[0]).name + ' ' + ' '.join(args) + '\\n')
    if 'SKILLZ_LIVE' in os.environ:
        log.write('live-flag-leaked\\n')
    if 'COVERAGE_FILE' in os.environ:
        log.write('coverage-file ' + os.environ['COVERAGE_FILE'] + '\\n')
if args == ['python', 'dir']:
    print(os.environ.get('FAKE_PYTHON_DIR',
                         os.environ.get('UV_PYTHON_INSTALL_DIR', str(pathlib.Path.home() / 'managed-python'))))
    if os.environ.get('FAKE_PYTHON_DIR_STATUS', '0') != '0':
        print('compiler discovery failed', file=sys.stderr)
    sys.exit(int(os.environ.get('FAKE_PYTHON_DIR_STATUS', '0')))
if os.environ.get('COMPILER_ENV_LOG'):
    with open(os.environ['COMPILER_ENV_LOG'], 'a') as log:
        log.write('step ' + os.environ.get('UV_PYTHON_INSTALL_DIR', '') + ' ' +
                  os.environ.get('UV_PYTHON_INSTALL_BIN', '') + '\\n')
        if 'pytest' in args:
            home = str(pathlib.Path(os.environ['EVENT_LOG']).parent / 'private-home')
            probe = subprocess.run(
                [sys.executable, '-c', "import os; print(os.environ['HOME'], "
                 "os.environ.get('UV_PYTHON_INSTALL_DIR', ''), os.environ.get('UV_PYTHON_INSTALL_BIN', ''))"],
                env={**os.environ, 'HOME': home}, capture_output=True, text=True, check=True,
            )
            log.write('child ' + probe.stdout)
if os.environ.get('HANG_JUST') and pathlib.Path(sys.argv[0]).name == 'just':
    pid_file = pathlib.Path(os.environ['CHILD_PID'])
    pid_tmp = pid_file.with_name(pid_file.name + '.tmp')
    pid_tmp.write_text(str(os.getpid()))
    os.replace(pid_tmp, pid_file)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)
if pathlib.Path(sys.argv[0]).name == 'just' and args == os.environ.get('FAIL_JUST', '').split():
    print('format failure')
    sys.exit(1)
if args and args[0] == 'run':
    time.sleep(.03)
"""
FAKE_EATMYDATA = """#!/usr/bin/env python3
import os, sys
with open(os.environ['EVENT_LOG'], 'a') as log:
    log.write('eatmydata ' + ' '.join(sys.argv[1:]) + '\\n')
os.execvp(sys.argv[1], sys.argv[1:])
"""
HOLD_SLOT = """import sys, time
sys.path.insert(0, sys.argv[1])
import run_gate
with run_gate.host_slot(1):
    print('held', flush=True)
    time.sleep(30)
"""


class GateRunnerTest(unittest.TestCase):
    def _fake_env(self, root: Path) -> dict[str, str]:
        for name in ("uv", "just"):
            tool = root / name
            _ = tool.write_text(FAKE_TOOL)
            tool.chmod(0o755)
        # CI jobs set GATE_SKIP, GATE_SHARD, or GATE_COVERAGE_DIR; drop them so each test sets its own lane selection.
        # An inherited GATE_COVERAGE_DIR would let a fake gate remove the live coverage data of the outer gate.
        dropped = {"GATE_SKIP", "GATE_SHARD", "GATE_COVERAGE_DIR"}
        inherited = {name: value for name, value in os.environ.items() if name not in dropped}
        # A private lock directory keeps the fake gates out of the real host slots that the outer gate holds.
        return {**inherited, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "EVENT_LOG": str(root / "events"), "GATE_JOBS": "2", "TEST_WORKERS": "3",
                "GATE_EATMYDATA": "0", "XDG_RUNTIME_DIR": str(root / "run"),
                "UV_PYTHON_INSTALL_DIR": str(root / "managed-python")}

    def _fake_eatmydata(self, root: Path) -> None:
        tool = root / "eatmydata"
        _ = tool.write_text(FAKE_EATMYDATA)
        tool.chmod(0o755)

    def test_compiler_root_reaches_every_mode_and_children_with_private_homes(self) -> None:
        for mode in ("fix", "check", "test", "skillz", *sorted(run_gate.PACKAGE_MODES)):
            for configured in (None, "", "caller-python"):
                with self.subTest(mode=mode, configured=configured), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    env = {**self._fake_env(root), "HOME": str(root / "original-home"),
                           "UV_PYTHON_INSTALL_BIN": "1", "COMPILER_ENV_LOG": str(root / "compiler-env")}
                    _ = env.pop("UV_PYTHON_INSTALL_DIR")
                    expected = root / "original-home" / "managed-python"
                    if configured is not None:
                        env["UV_PYTHON_INSTALL_DIR"] = str(root / configured) if configured else ""
                        if configured:
                            expected = root / configured
                    expected = expected.resolve()
                    result = subprocess.run([sys.executable, str(RUNNER), mode], env=env,
                                            capture_output=True, text=True, check=False, timeout=HANG_GUARD)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    events = (root / "events").read_text().splitlines()
                    self.assertEqual(events.count("uv python dir"), 0 if configured else 1)
                    if not configured:
                        self.assertEqual(events[0], "uv python dir")
                    commands = [event for event in events if event.startswith(("uv ", "just "))]
                    observations = (root / "compiler-env").read_text().splitlines()
                    steps = [line for line in observations if line.startswith("step ")]
                    children = [line for line in observations if line.startswith("child ")]
                    self.assertEqual(steps, [f"step {expected} 0"] * len(steps))
                    self.assertEqual(len(steps), len(commands) - (0 if configured else 1))
                    self.assertEqual(children, [f"child {root / 'private-home'} {expected} 0"] * len(children))
                    self.assertEqual(len(children), sum(" pytest " in event for event in events))
                    self.assertGreater(len(children), 0)
                    self.assertFalse(any(event.startswith("uv python install") for event in events))

    def test_compiler_discovery_failure_stops_before_preparation(self) -> None:
        cases = (("7", "/unused", "compiler discovery failed"),
                 ("0", "  ", "uv python dir returned an empty compiler root"),
                 ("0", "relative-root", "uv python dir must return one absolute compiler root"))
        for status, output, diagnostic in cases:
            with self.subTest(status=status, output=output), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = {**self._fake_env(root), "UV_PYTHON_INSTALL_DIR": "",
                       "FAKE_PYTHON_DIR_STATUS": status, "FAKE_PYTHON_DIR": output}
                result = subprocess.run([sys.executable, str(RUNNER), "check"], env=env,
                                        capture_output=True, text=True, check=False, timeout=HANG_GUARD)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(diagnostic, result.stdout + result.stderr)
                commands = [event for event in (root / "events").read_text().splitlines()
                            if event.startswith(("uv ", "just "))]
                self.assertEqual(commands, ["uv python dir"])

    def test_only_the_pytest_lanes_run_under_eatmydata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fake_eatmydata(root)
            env = {name: value for name, value in self._fake_env(root).items() if name != "GATE_EATMYDATA"}
            result = subprocess.run([sys.executable, str(RUNNER), "fix"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            # Each pytest lane runs as `eatmydata env COVERAGE_FILE=<lane data> uv run ...`.
            wrapped = [event.removeprefix("eatmydata env ").split(" ", 1)[1] for event in events
                       if event.startswith("eatmydata ")]
            self.assertTrue(all(event.startswith("eatmydata env COVERAGE_FILE=") for event in events
                                if event.startswith("eatmydata ")))
            pytest_events = [event for event in events if " pytest " in event and not event.startswith("eatmydata ")]
            self.assertEqual(len(pytest_events), 5)
            self.assertEqual(sorted(wrapped), sorted(pytest_events))
            # The format steps start beside the sync, so their log order varies.
            self.assertIn("uv sync --locked --all-packages --all-groups --all-extras", events[:3])
            self.assertIn("→ skillz-e2e-pytest (eatmydata)", result.stdout)
            self.assertIn("→ fromargs-typecheck\n", result.stdout)

    def test_gate_eatmydata_zero_keeps_fsync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fake_eatmydata(root)
            result = subprocess.run([sys.executable, str(RUNNER), "skillz"], env=self._fake_env(root),
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            self.assertEqual(sum(" pytest " in event for event in events), 2)
            self.assertFalse(any(event.startswith("eatmydata") for event in events))
            self.assertNotIn("(eatmydata)", result.stdout)

    def test_a_missing_eatmydata_gives_no_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"PATH": directory}):
                _ = os.environ.pop("GATE_EATMYDATA", None)
                self.assertEqual(run_gate.fsync_wrapper(), [])

    def test_host_slots_admit_at_most_the_slot_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}):
            first, second = run_gate.try_slot(2), run_gate.try_slot(2)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertIsNone(run_gate.try_slot(2))
            self.assertIsNone(run_gate.try_slot(1))
            assert first is not None and second is not None
            first.close()
            third = run_gate.try_slot(2)
            self.assertIsNotNone(third)
            self.assertIsNone(run_gate.try_slot(2))
            for handle in (second, third):
                assert handle is not None
                handle.close()
            self.assertEqual(sorted(path.name for path in (Path(directory) / "skillz-gate").iterdir()),
                             ["slot-0.lock", "slot-1.lock"])

    def test_a_killed_gate_frees_its_host_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "XDG_RUNTIME_DIR": directory}
            holder = subprocess.Popen(
                [sys.executable, "-c", HOLD_SLOT, str(RUNNER.parent)],
                env=env, stdout=subprocess.PIPE, text=True,
            )
            try:
                assert holder.stdout is not None
                self.assertEqual(holder.stdout.readline(), "held\n")
                with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}):
                    self.assertIsNone(run_gate.try_slot(1))
                    holder.kill()
                    _ = holder.wait(timeout=5)
                    handle = run_gate.try_slot(1)
                    self.assertIsNotNone(handle)
                    assert handle is not None
                    handle.close()
            finally:
                if holder.poll() is None:
                    holder.kill()
                    _ = holder.wait()
                if holder.stdout is not None:
                    holder.stdout.close()

    def test_a_second_gate_waits_for_a_host_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**self._fake_env(root), "GATE_HOST_SLOTS": "1"}
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": env["XDG_RUNTIME_DIR"]}):
                held = run_gate.try_slot(1)
            assert held is not None
            log = root / "stdout"
            with log.open("w") as stdout:
                gate = subprocess.Popen([sys.executable, str(RUNNER), "skillz"], env=env,
                                        stdout=stdout, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 5
                while "waiting for a host gate slot" not in log.read_text() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertIn("… waiting for a host gate slot (1 busy; GATE_HOST_SLOTS sets the count)", log.read_text())
                time.sleep(.3)
                self.assertIsNone(gate.poll())
                events = (root / "events").read_text().splitlines()
                self.assertTrue(events[0].startswith("uv sync "), events)
                self.assertFalse(any(" pytest " in event for event in events))
                held.close()
                self.assertEqual(gate.wait(timeout=HANG_GUARD), 0, log.read_text())
                self.assertIn("✓ host gate slot", log.read_text())
                self.assertEqual(sum(" pytest " in event for event in (root / "events").read_text().splitlines()), 2)
            finally:
                held.close()
                if gate.poll() is None:
                    gate.kill()
                    _ = gate.wait()

    def test_gate_host_slots_zero_removes_the_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**self._fake_env(root), "GATE_HOST_SLOTS": "0"}
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": env["XDG_RUNTIME_DIR"]}):
                held = run_gate.try_slot(1)
            assert held is not None
            try:
                result = subprocess.run([sys.executable, str(RUNNER), "skillz"], env=env,
                                        capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            finally:
                held.close()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("waiting", result.stdout)

    def test_jobs_overlap_with_a_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = (
                "import pathlib, sys, time\n"
                "root = pathlib.Path(sys.argv[1]); name = sys.argv[2]\n"
                "(root / (name + '.start')).write_text(str(time.monotonic_ns()))\n"
                "deadline = time.monotonic() + 3\n"
                "while len(list(root.glob('*.start'))) < 2 and time.monotonic() < deadline: time.sleep(.01)\n"
                "time.sleep(.05)\n"
                "(root / (name + '.end')).write_text(str(time.monotonic_ns()))\n"
            )
            jobs = [(str(n), [sys.executable, "-c", script, directory, str(n)]) for n in range(4)]
            with redirect_stdout(io.StringIO()):
                self.assertTrue(run_gate.run_jobs(jobs, 2))
            events = sorted(
                (int(path.read_text()), 1 if path.suffix == ".start" else -1)
                for path in root.iterdir()
            )
            active = peak = 0
            for _, delta in events:
                active += delta
                peak = max(peak, active)
            self.assertEqual(peak, 2)
            self.assertEqual(active, 0)

    def test_failure_reports_full_output_and_reaps_other_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "finished"
            jobs = [
                ("failed", [sys.executable, "-c", "print('diagnostic line'); raise SystemExit(7)"]),
                ("other", [sys.executable, "-c", "import pathlib,time,sys; time.sleep(.1); pathlib.Path(sys.argv[1]).touch()", str(marker)]),
            ]
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertFalse(run_gate.run_jobs(jobs, 2))
            self.assertIn("diagnostic line", output.getvalue())
            self.assertIn("✗ failed", output.getvalue())
            self.assertTrue(marker.exists())

    def test_cli_prepares_before_readers_and_keeps_caches_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self._fake_env(root)
            result = subprocess.run([sys.executable, str(RUNNER), "fix"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            # The format steps run beside the workspace sync; all three end before the archive build.
            self.assertCountEqual(events[:3], [
                "just lint-md-fix", "just lint-yaml-fmt-fix",
                "uv sync --locked --all-packages --all-groups --all-extras",
            ])
            # Main tracks no archive (ADR-012), so the archives build before any test reads them.
            self.assertEqual(events[3], "uv run --locked --no-sync --project packages/wedge wedge bundle skills/skillz")
            # The offline e2e locks come from the workspace lock, so no step warms the uv index cache.
            self.assertFalse(any(event.startswith("uv lock") for event in events), events)
            readers = events[4:]
            self.assertFalse(any(event.startswith("uv sync") for event in readers))
            self.assertTrue(any("packages/wedge/tests" in event for event in readers))
            self.assertTrue(any("packages/skillz/tests" in event for event in readers))
            self.assertTrue(any("packages/fromargs/tests" in event for event in readers))
            self.assertTrue(any("packages/agent-session-analytics/tests" in event for event in readers))
            self.assertFalse(any("wedge bundle" in event for event in readers))
            self.assertTrue(all("--no-sync" in event for event in readers if event.startswith("uv run") and "--no-project" not in event))
            pytest_events = [event for event in readers if " pytest " in event]
            self.assertEqual(len(pytest_events), 5)
            e2e = "packages/skillz/tests/skillz_experiments/e2e"
            self.assertEqual(sum(f"pytest {e2e} " in event for event in pytest_events), 1)
            self.assertTrue(any(f"pytest packages/skillz/tests --ignore {e2e} " in event for event in pytest_events))
            self.assertTrue(all(" -n 3 " in event for event in pytest_events))
            caches = [Path(event.split("cache_dir=")[1]) for event in pytest_events]
            bases = [Path(event.split("--basetemp ")[1].split()[0]) for event in pytest_events]
            self.assertEqual(len(set(caches)), 5)
            self.assertEqual(len(set(bases)), 5)
            self.assertTrue(all(cache.is_absolute() and base.is_absolute() for cache, base in zip(caches, bases)))
            self.assertTrue(all(str(base).startswith("/tmp/") for base in bases))
            socket = bases[0].resolve() / "popen-gw0" / "test_a_sandbox_read_root_that_0" / "h" / "inner" / "s"
            self.assertLess(len(os.fsencode(socket)), 104)
            self.assertTrue(all(not path.exists() for path in caches + bases))

    def test_a_failed_format_step_stops_the_gate_before_the_archive_build(self) -> None:
        for mode, failing, step in (("fix", "lint-md-fix", "markdown"), ("check", "lint-yaml-fmt", "yaml-fmt")):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = {**self._fake_env(root), "FAIL_JUST": failing}
                result = subprocess.run([sys.executable, str(RUNNER), mode], env=env,
                                        capture_output=True, text=True, check=False, timeout=HANG_GUARD)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("format failure", result.stdout)
                self.assertIn(f"✗ {step}", result.stdout)
                events = (root / "events").read_text().splitlines()
                self.assertFalse(any("wedge bundle" in event or " pytest " in event for event in events), events)

    def test_without_test_workers_the_skillz_unit_lane_gets_half_the_cpus(self) -> None:
        # A fixed CPU count keeps the unit lane apart from the 4-worker lanes on any host, also a 4-vCPU runner.
        pinned = ("import os, runpy, sys; os.cpu_count = lambda: 40; sys.argv = [sys.argv[1], 'check']; "
                  "runpy.run_path(sys.argv[0], run_name='__main__')")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {name: value for name, value in self._fake_env(root).items() if name != "TEST_WORKERS"}
            result = subprocess.run([sys.executable, "-c", pinned, str(RUNNER)], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            pytest_events = [event for event in (root / "events").read_text().splitlines() if " pytest " in event]
            unit = [event for event in pytest_events if "pytest packages/skillz/tests --ignore" in event]
            self.assertEqual(len(unit), 1)
            self.assertIn(" -n 20 ", unit[0])
            others = [event for event in pytest_events if event not in unit]
            self.assertEqual(len(others), 4)
            self.assertTrue(all(" -n 4 " in event for event in others), others)

    def test_unit_workers_keep_an_explicit_count_and_never_drop_below_the_base(self) -> None:
        self.assertEqual(run_gate.unit_workers(3, True, 16), 3)
        self.assertEqual(run_gate.unit_workers(4, False, 4), 4)
        self.assertEqual(run_gate.unit_workers(4, False, None), 4)
        self.assertEqual(run_gate.unit_workers(4, False, 15), 7)
        self.assertEqual(run_gate.unit_workers(4, False, 32), 16)

    def test_steps_never_see_the_live_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**self._fake_env(root), "SKILLZ_LIVE": "1"}
            result = subprocess.run([sys.executable, str(RUNNER), "fix"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            self.assertTrue(any("packages/skillz/tests" in event for event in events))
            self.assertNotIn("live-flag-leaked", events)

    def test_standalone_fromargs_uses_short_base_and_keeps_python_pin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self._fake_env(root)
            result = subprocess.run([sys.executable, str(RUNNER), "fromargs", "3.11"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            event = (root / "events").read_text().strip()
            self.assertIn("uv run --locked --python 3.11 --project packages/fromargs pytest", event)
            base = Path(event.split("--basetemp ")[1].split()[0])
            cache = Path(event.split("cache_dir=")[1])
            self.assertTrue(str(base).startswith("/tmp/"))
            self.assertNotEqual(base, cache)
            self.assertFalse(base.exists())
            self.assertFalse(cache.exists())

    def test_standalone_agent_session_analytics_keeps_python_pin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self._fake_env(root)
            result = subprocess.run([sys.executable, str(RUNNER), "agent-session-analytics", "3.11"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            event = (root / "events").read_text().strip()
            self.assertIn("--project packages/agent-session-analytics pytest packages/agent-session-analytics/tests", event)
            self.assertIn("--python 3.11", event)

    def test_gate_skip_drops_only_the_named_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**self._fake_env(root), "GATE_SKIP": " skillz-e2e-pytest, skillz-pytest ,"}
            result = subprocess.run([sys.executable, str(RUNNER), "check"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            self.assertFalse(any("packages/skillz/tests" in event for event in events))
            pytest_events = [event for event in events if " pytest " in event]
            self.assertEqual(len(pytest_events), 3)
            self.assertTrue(any("packages/wedge/tests" in event for event in pytest_events))
            self.assertTrue(any("basedpyright" in event for event in events))
            self.assertIn("- skillz-pytest (skipped by GATE_SKIP)", result.stdout)
            self.assertIn("- skillz-e2e-pytest (skipped by GATE_SKIP)", result.stdout)

    def test_gate_skip_with_an_unknown_lane_fails_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**self._fake_env(root), "GATE_SKIP": "skillz-pytest,skilz-e2e-pytest"}
            result = subprocess.run([sys.executable, str(RUNNER), "check"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 2)
            self.assertIn("GATE_SKIP names unknown lanes: skilz-e2e-pytest", result.stderr)
            self.assertFalse((root / "events").exists())

    def test_skillz_mode_prepares_then_runs_only_the_skillz_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self._fake_env(root)
            result = subprocess.run([sys.executable, str(RUNNER), "skillz"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = [event for event in (root / "events").read_text().splitlines()
                      if not event.startswith("coverage-file ")]
            self.assertEqual(events[0], "uv sync --locked --all-packages --all-groups --all-extras")
            # Each CI shard builds the archives that its tests run (ADR-012).
            self.assertEqual(events[1], "uv run --locked --no-sync --project packages/wedge wedge bundle skills/skillz")
            e2e = "packages/skillz/tests/skillz_experiments/e2e"
            self.assertEqual(len(events), 4)
            self.assertTrue(any(f"pytest {e2e} " in event for event in events[2:]))
            self.assertTrue(any(f"pytest packages/skillz/tests --ignore {e2e} " in event for event in events[2:]))
            # Without GATE_SHARD, both lanes run whole, as in local `just build` and `just ci`.
            self.assertFalse(any("--splits" in event or "--durations-path" in event for event in events))

    def test_skillz_shard_runs_one_split_group_of_both_skillz_lanes_and_stores_its_durations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            env = {**self._fake_env(root), "GATE_SHARD": "2/4", "GATE_COVERAGE_DIR": str(data)}
            result = subprocess.run([sys.executable, str(RUNNER), "skillz"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            pytest_events = [event for event in events if " pytest " in event]
            self.assertEqual(len(pytest_events), 2)
            durations = data.resolve() / "durations"
            for lane in ("skillz-e2e-pytest", "skillz-pytest"):
                stored = durations / f"{lane}.2of4.json"
                split = (f"--junitxml {durations / f'{lane}.2of4.xml'} "
                         "--splits 4 --group 2 --splitting-algorithm least_duration "
                         f"--durations-path {stored} --store-durations --clean-durations")
                self.assertEqual(sum(event.endswith(" " + split) for event in pytest_events), 1, pytest_events)
                # The split reads a copy of the committed file; pytest-split then replaces it with this shard's data.
                self.assertEqual(stored.read_bytes(), run_gate.DURATIONS_FILE.read_bytes())
            self.assertEqual(sorted(path.name for path in durations.iterdir()),
                             ["skillz-e2e-pytest.2of4.json", "skillz-pytest.2of4.json"])
            self.assertFalse(any("--splits" in event for event in events if " pytest " not in event))
            # A retry in the same directory that skips a lane removes the earlier run's copy and JUnit report for
            # that lane, so the merge and the time ratchet see only this run's files.
            for stale in ("skillz-e2e-pytest.2of4.xml", "skillz-pytest.2of4.xml"):
                _ = (durations / stale).write_text("<testsuites />")
            env = {**env, "GATE_SKIP": "skillz-e2e-pytest"}
            result = subprocess.run([sys.executable, str(RUNNER), "skillz"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual([path.name for path in durations.iterdir()], ["skillz-pytest.2of4.json"])

    def test_the_gate_prints_the_time_report_and_never_fails_on_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self._fake_env(root)
            # The report exits 1 here; the gate still passes, because only the CI coverage job enforces the ceilings.
            failing = "\n".join(["if any(arg.endswith('/check_test_durations.py') for arg in args):",
                                 "    print('breach: tests/test_slow.py')", "    sys.exit(1)", ""])
            _ = (root / "uv").write_text(FAKE_TOOL + failing)
            result = subprocess.run([sys.executable, str(RUNNER), "check"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("• test-time-report (report only)\nbreach: tests/test_slow.py\n", result.stdout)
            events = (root / "events").read_text().splitlines()
            script = ".github/scripts/check_test_durations.py"
            # The report runs after the lanes and before the coverage lane.
            report = f"uv run --locked --no-sync --all-groups python {script} report {run_gate.DURATIONS_FILE}"
            self.assertEqual(events[-2], report)
            self.assertIn(" .github/scripts/check_coverage.py ", events[-1])

    def test_every_pytest_lane_writes_its_own_coverage_then_the_coverage_lane_runs_last(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "lane-data"
            data.mkdir()
            for name in ("old.cov", "old.cov.host.7.Xabc", "keep.txt"):
                _ = (data / name).write_text("")
            env = {**self._fake_env(root), "GATE_COVERAGE_DIR": str(data)}
            result = subprocess.run([sys.executable, str(RUNNER), "check"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            files = [event.removeprefix("coverage-file ") for event in events if event.startswith("coverage-file ")]
            self.assertEqual(sorted(Path(path).name for path in files), [
                "agent-session-analytics-pytest.cov", "fromargs-pytest.cov", "skillz-e2e-pytest.cov",
                "skillz-pytest.cov", "wedge-pytest.cov"])
            self.assertTrue(all(Path(path).parent == data.resolve() for path in files))
            pytest_events = [event for event in events if " pytest " in event]
            sources = ",".join(f"{RUNNER.parents[2]}/packages/{name}/src"
                                for name in ("agent-session-analytics", "fromargs", "skillz", "wedge"))
            self.assertEqual(len(pytest_events), 5)
            self.assertTrue(all(f" coverage run --parallel-mode --source={sources} -m pytest " in event
                                for event in pytest_events), pytest_events)
            gate = ".github/scripts/check_coverage.py"
            self.assertEqual(events[-1], f"uv run --locked --no-sync --all-groups python {gate} {data.resolve()}")
            self.assertIn("✓ coverage", result.stdout)
            # Stale lane data from an earlier run is gone; other files stay.
            self.assertEqual(sorted(path.name for path in data.iterdir()), ["keep.txt"])

    def test_the_coverage_lane_needs_every_pytest_lane(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**self._fake_env(root), "GATE_SKIP": "skillz-pytest"}
            result = subprocess.run([sys.executable, str(RUNNER), "check"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("- coverage (needs skillz-pytest)", result.stdout)
            self.assertFalse(any(" .github/scripts/check_coverage.py " in event
                                 for event in (root / "events").read_text().splitlines()))
            env = {**self._fake_env(root), "GATE_SKIP": "coverage"}
            result = subprocess.run([sys.executable, str(RUNNER), "check"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("- coverage (skipped by GATE_SKIP)", result.stdout)

    def test_a_failed_lane_stops_the_gate_before_the_coverage_lane(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = root / "just"
            env = self._fake_env(root)
            _ = tool.write_text(FAKE_TOOL + "if args == ['lint-yaml']:\n    sys.exit(3)\n")
            result = subprocess.run([sys.executable, str(RUNNER), "check"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn("coverage", result.stdout.replace("coverage-tests", ""))
            # A failed lane still prints the report-only time step.
            self.assertIn("• test-time-report (report only)", result.stdout)

    def test_a_shard_tags_its_coverage_files_and_skips_the_coverage_lane(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**self._fake_env(root), "GATE_SHARD": "2/6", "GATE_COVERAGE_DIR": str(root / "data")}
            result = subprocess.run([sys.executable, str(RUNNER), "skillz"], env=env,
                                    capture_output=True, text=True, check=False, timeout=HANG_GUARD)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            files = sorted(Path(event.split(" ", 1)[1]).name for event in events if event.startswith("coverage-file "))
            self.assertEqual(files, ["skillz-e2e-pytest.2of6.cov", "skillz-pytest.2of6.cov"])
            self.assertFalse(any(" .github/scripts/check_coverage.py " in event for event in events))

    def test_a_malformed_or_misplaced_shard_fails_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self._fake_env(root)
            cases = [("check", "1/2"), ("skillz", "0/2"), ("skillz", "3/2"), ("skillz", "x"), ("skillz", "1/")]
            for mode, value in cases:
                with self.subTest(mode=mode, value=value):
                    result = subprocess.run([sys.executable, str(RUNNER), mode], env={**env, "GATE_SHARD": value},
                                            capture_output=True, text=True, check=False, timeout=HANG_GUARD)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("GATE_SHARD", result.stderr)
                    self.assertFalse((root / "events").exists())

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is not installed")
    def test_the_six_split_groups_partition_the_collected_tests_under_xdist(self) -> None:
        """Spec test-suite-cost-gates AC-6: each collected test runs in exactly one shard."""
        with tempfile.TemporaryDirectory() as directory:
            # pytest resolves its rootdir; on macOS the temp dir is a symlink (/var -> /private/var).
            root = Path(directory).resolve()
            tests = root / "tests"
            tests.mkdir()
            for module in "abc":
                _ = (tests / f"test_{module}.py").write_text("".join(f"def test_{n}():\n    pass\n" for n in range(7)))
            # Uneven recorded durations for some tests; the rest get the average, as a new test does.
            committed = root / "committed.json"
            _ = committed.write_text(json.dumps({"tests/test_a.py::test_0": 9.0, "tests/test_b.py::test_3": 4.0,
                                                 "tests/test_c.py::test_6": 0.5, "tests/gone.py::test_x": 50.0}))
            env = self._fake_env(root)

            def pytest(*extra: str) -> str:
                result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra, "tests"],
                                        cwd=root, env=env, capture_output=True, text=True, check=False, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout

            everything = {line for line in pytest("--collect-only").splitlines() if "::" in line}
            stored = root / "stored"
            stored.mkdir()
            for index in range(1, 7):
                durations = stored / f"skillz-pytest.{index}of6.json"
                _ = shutil.copyfile(committed, durations)
                # The CI shard runs its lanes with xdist workers, so this run does too.
                _ = pytest("-n", "2", "--dist", "worksteal", *run_gate.split_arguments(index, 6, durations))
            ran = {path.name: set(cast(dict[str, float], json.loads(path.read_text()))) for path in stored.iterdir()}
            self.assertEqual(len(everything), 21)
            self.assertTrue(all(ran.values()), ran)
            # merge() fails when a test is in two shard files; the union is the whole collection.
            self.assertEqual(set(merge_durations.merge(stored)), everything)
            self.assertEqual(sum(len(tests) for tests in ran.values()), len(everything))

    def test_invalid_worker_count_fails_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self._fake_env(root)
            for name, value in (("GATE_JOBS", "0"), ("TEST_WORKERS", "auto"), ("TEST_WORKERS", "-1"),
                                ("GATE_HOST_SLOTS", "-1"), ("GATE_HOST_SLOTS", "two"),
                                ("GATE_STEP_SECONDS", "-1"), ("GATE_STEP_SECONDS", "1h")):
                with self.subTest(name=name, value=value):
                    result = subprocess.run([sys.executable, str(RUNNER), "fix"],
                                            env={**env, name: value}, capture_output=True,
                                            text=True, check=False)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(name, result.stderr)
                    self.assertFalse((root / "events").exists())

    def test_term_reaps_child_that_ignores_term(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**self._fake_env(root), "HANG_JUST": "1", "CHILD_PID": str(root / "pid")}
            runner = subprocess.Popen([sys.executable, str(RUNNER), "fix"], env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while not (root / "pid").exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((root / "pid").exists())
                child = int((root / "pid").read_text())
                runner.send_signal(signal.SIGTERM)
                stdout, stderr = runner.communicate(timeout=6)
                self.assertEqual(runner.returncode, 130, stdout + stderr)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child, 0)
            finally:
                if runner.poll() is None:
                    runner.kill()
                    _ = runner.wait()

    def test_a_step_past_its_deadline_is_killed_and_fails_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # The fake `just` ignores SIGTERM and sleeps 30 s, like a cleanup that waits with no deadline.
            env = {**self._fake_env(root), "HANG_JUST": "1", "CHILD_PID": str(root / "pid"), "GATE_STEP_SECONDS": "1"}
            started = time.monotonic()
            result = subprocess.run([sys.executable, str(RUNNER), "fix"], env=env,
                                    capture_output=True, text=True, check=False, timeout=20)
            self.assertLess(time.monotonic() - started, 15)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("✗ markdown: killed at the 1 s step deadline (GATE_STEP_SECONDS)", result.stdout)
            self.assertNotIn("✓ markdown", result.stdout)
            with self.assertRaises(ProcessLookupError):
                os.kill(int((root / "pid").read_text()), 0)

    def test_each_pytest_lane_sets_its_per_test_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mode in ("check", "fromargs"):
                result = subprocess.run([sys.executable, str(RUNNER), mode], env=self._fake_env(root),
                                        capture_output=True, text=True, check=False, timeout=HANG_GUARD)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            # One `<test tree> <limit>` pair per pytest lane: `coverage run -m pytest <tree>` or `pytest <tree>`.
            limits = sorted(event.split(" pytest ")[1].split()[0] + " " + event.split(" --timeout ")[1].split()[0]
                            for event in events if " pytest " in event)
            self.assertEqual(limits, [
                "packages/agent-session-analytics/tests 120", "packages/fromargs/tests 120",
                "packages/fromargs/tests 120", "packages/skillz/tests 120",
                "packages/skillz/tests/skillz_experiments/e2e 600", "packages/wedge/tests 120"])
            self.assertTrue(all(" --timeout-method signal " in event for event in events if " pytest " in event))
            self.assertIn("uv run --locked --no-sync --all-groups python .github/scripts/check_test_counts.py check",
                          events)
            self.assertIn("uv run --locked --no-sync --all-groups python .github/scripts/test_check_test_counts.py",
                          events)

    def test_the_lane_timeout_fails_a_hung_test_and_does_not_hang(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            test = Path(directory) / "test_hang.py"
            _ = test.write_text("import time\n\n\ndef test_hang():\n    time.sleep(60)\n\n\ndef test_quick():\n    pass\n")
            started = time.monotonic()
            # `-c os.devnull` sets confcutdir to /dev, and pytest then collects from the temp root down.
            # On a host with a busy temp root, that walk takes longer than the subprocess limit.
            # `--confcutdir` starts the collection at this directory.
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", os.devnull,
                                     "--rootdir", directory, "--confcutdir", directory, str(test),
                                     *run_gate.timeout_args(1)],
                                    cwd=directory, capture_output=True, text=True, check=False, timeout=30)
            self.assertLess(time.monotonic() - started, 30)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("Failed: Timeout (>1.0s) from pytest-timeout.", result.stdout)
            self.assertIn("1 failed, 1 passed", result.stdout)


if __name__ == "__main__":
    _ = unittest.main()

"""Behavior tests for the parallel quality gate."""
from __future__ import annotations

import importlib.util
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_gate  # noqa: E402

RUNNER = Path(run_gate.__file__).resolve()
FAKE_TOOL = """#!/usr/bin/env python3
import os, pathlib, signal, sys, time
args = sys.argv[1:]
with open(os.environ['EVENT_LOG'], 'a') as log:
    log.write(pathlib.Path(sys.argv[0]).name + ' ' + ' '.join(args) + '\\n')
    if 'SKILLZ_LIVE' in os.environ:
        log.write('live-flag-leaked\\n')
    if 'GATE_SHARD' in os.environ:
        log.write('pythonpath ' + os.environ.get('PYTHONPATH', '') + '\\n')
if os.environ.get('HANG_JUST') and pathlib.Path(sys.argv[0]).name == 'just':
    pid_file = pathlib.Path(os.environ['CHILD_PID'])
    pid_tmp = pid_file.with_name(pid_file.name + '.tmp')
    pid_tmp.write_text(str(os.getpid()))
    os.replace(pid_tmp, pid_file)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)
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
        # CI jobs set GATE_SKIP or GATE_SHARD; drop them so each test sets its own lane selection.
        inherited = {name: value for name, value in os.environ.items() if name not in {"GATE_SKIP", "GATE_SHARD"}}
        # A private lock directory keeps the fake gates out of the real host slots that the outer gate holds.
        return {**inherited, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "EVENT_LOG": str(root / "events"), "GATE_JOBS": "2", "TEST_WORKERS": "3",
                "GATE_EATMYDATA": "0", "XDG_RUNTIME_DIR": str(root / "run")}

    def _fake_eatmydata(self, root: Path) -> None:
        tool = root / "eatmydata"
        _ = tool.write_text(FAKE_EATMYDATA)
        tool.chmod(0o755)

    def test_only_the_pytest_lanes_run_under_eatmydata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fake_eatmydata(root)
            env = {name: value for name, value in self._fake_env(root).items() if name != "GATE_EATMYDATA"}
            result = subprocess.run([sys.executable, str(RUNNER), "fix"], env=env,
                                    capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            wrapped = [event.removeprefix("eatmydata ") for event in events if event.startswith("eatmydata ")]
            pytest_events = [event for event in events if " pytest " in event and not event.startswith("eatmydata ")]
            self.assertEqual(len(pytest_events), 5)
            self.assertEqual(sorted(wrapped), sorted(pytest_events))
            self.assertEqual(events[2], "uv sync --locked --all-packages --all-groups --all-extras")
            self.assertIn("→ skillz-e2e-pytest (eatmydata)", result.stdout)
            self.assertIn("→ fromargs-typecheck\n", result.stdout)

    def test_gate_eatmydata_zero_keeps_fsync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fake_eatmydata(root)
            result = subprocess.run([sys.executable, str(RUNNER), "skillz"], env=self._fake_env(root),
                                    capture_output=True, text=True, check=False, timeout=10)
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
                self.assertEqual(gate.wait(timeout=10), 0, log.read_text())
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
                                        capture_output=True, text=True, check=False, timeout=10)
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
                                    capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            self.assertEqual(events[:3], [
                "just lint-md-fix", "just lint-yaml-fmt-fix",
                "uv sync --locked --all-packages --all-groups --all-extras",
            ])
            self.assertTrue(events[3].startswith("uv lock --project "), events[3])
            readers = events[4:]
            self.assertFalse(any(event.startswith("uv sync") for event in readers))
            self.assertTrue(any("packages/wedge/tests" in event for event in readers))
            self.assertTrue(any("packages/skillz/tests" in event for event in readers))
            self.assertTrue(any("packages/fromargs/tests" in event for event in readers))
            self.assertTrue(any("packages/agent-session-analytics/tests" in event for event in readers))
            self.assertTrue(any("wedge bundle" in event for event in readers))
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

    def test_steps_never_see_the_live_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**self._fake_env(root), "SKILLZ_LIVE": "1"}
            result = subprocess.run([sys.executable, str(RUNNER), "fix"], env=env,
                                    capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            self.assertTrue(any("packages/skillz/tests" in event for event in events))
            self.assertNotIn("live-flag-leaked", events)

    def test_standalone_fromargs_uses_short_base_and_keeps_python_pin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self._fake_env(root)
            result = subprocess.run([sys.executable, str(RUNNER), "fromargs", "3.11"], env=env,
                                    capture_output=True, text=True, check=False, timeout=10)
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
                                    capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            event = (root / "events").read_text().strip()
            self.assertIn("--project packages/agent-session-analytics pytest packages/agent-session-analytics/tests", event)
            self.assertIn("--python 3.11", event)

    def test_gate_skip_drops_only_the_named_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**self._fake_env(root), "GATE_SKIP": " skillz-e2e-pytest, skillz-pytest ,"}
            result = subprocess.run([sys.executable, str(RUNNER), "check"], env=env,
                                    capture_output=True, text=True, check=False, timeout=10)
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
                                    capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn("GATE_SKIP names unknown lanes: skilz-e2e-pytest", result.stderr)
            self.assertFalse((root / "events").exists())

    def test_skillz_mode_prepares_then_runs_only_the_skillz_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self._fake_env(root)
            result = subprocess.run([sys.executable, str(RUNNER), "skillz"], env=env,
                                    capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            self.assertEqual(events[0], "uv sync --locked --all-packages --all-groups --all-extras")
            self.assertTrue(events[1].startswith("uv lock --project "), events[1])
            e2e = "packages/skillz/tests/skillz_experiments/e2e"
            self.assertEqual(len(events), 4)
            self.assertTrue(any(f"pytest {e2e} " in event for event in events[2:]))
            self.assertTrue(any(f"pytest packages/skillz/tests --ignore {e2e} " in event for event in events[2:]))

    def test_skillz_shard_loads_the_plugin_into_both_skillz_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**self._fake_env(root), "GATE_SHARD": "2/4", "PYTHONPATH": "/elsewhere"}
            result = subprocess.run([sys.executable, str(RUNNER), "skillz"], env=env,
                                    capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = (root / "events").read_text().splitlines()
            pytest_events = [event for event in events if " pytest " in event]
            self.assertEqual(len(pytest_events), 2)
            self.assertTrue(all(event.endswith(" -p pytest_shard") for event in pytest_events))
            paths = {event for event in events if event.startswith("pythonpath ")}
            self.assertEqual(paths, {f"pythonpath {RUNNER.parent}{os.pathsep}/elsewhere"})

    def test_a_malformed_or_misplaced_shard_fails_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self._fake_env(root)
            cases = [("check", "1/2"), ("skillz", "0/2"), ("skillz", "3/2"), ("skillz", "x"), ("skillz", "1/")]
            for mode, value in cases:
                with self.subTest(mode=mode, value=value):
                    result = subprocess.run([sys.executable, str(RUNNER), mode], env={**env, "GATE_SHARD": value},
                                            capture_output=True, text=True, check=False, timeout=10)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("GATE_SHARD", result.stderr)
                    self.assertFalse((root / "events").exists())

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is not installed")
    def test_the_shards_partition_the_collected_tests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _ = (root / "test_sample.py").write_text("".join(f"def test_{n}():\n    pass\n" for n in range(7)))
            env = {**self._fake_env(root), "PYTHONPATH": str(RUNNER.parent)}

            def collected(*extra: str, shard: str | None = None) -> list[str]:
                run_env = env if shard is None else {**env, "GATE_SHARD": shard}
                result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                                         *extra, str(root / "test_sample.py")], cwd=root, env=run_env,
                                        capture_output=True, text=True, check=False, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return [line for line in result.stdout.splitlines() if "::" in line]

            everything = collected()
            shards = [collected("-p", "pytest_shard", shard=f"{index}/3") for index in (1, 2, 3)]
            self.assertEqual(len(everything), 7)
            self.assertEqual([len(shard) for shard in shards], [3, 2, 2])
            self.assertEqual(sorted(test for shard in shards for test in shard), sorted(everything))

    def test_invalid_worker_count_fails_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self._fake_env(root)
            for name, value in (("GATE_JOBS", "0"), ("TEST_WORKERS", "auto"), ("TEST_WORKERS", "-1"),
                                ("GATE_HOST_SLOTS", "-1"), ("GATE_HOST_SLOTS", "two")):
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


if __name__ == "__main__":
    _ = unittest.main()

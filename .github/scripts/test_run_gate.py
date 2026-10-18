"""Behavior tests for the parallel quality gate."""
from __future__ import annotations

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


class GateRunnerTest(unittest.TestCase):
    def _fake_env(self, root: Path) -> dict[str, str]:
        for name in ("uv", "just"):
            tool = root / name
            _ = tool.write_text(FAKE_TOOL)
            tool.chmod(0o755)
        # CI's gate job sets GATE_SKIP; drop it so each test sets its own lane selection.
        inherited = {name: value for name, value in os.environ.items() if name != "GATE_SKIP"}
        return {**inherited, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "EVENT_LOG": str(root / "events"), "GATE_JOBS": "2", "TEST_WORKERS": "3"}

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
            readers = events[3:]
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
            e2e = "packages/skillz/tests/skillz_experiments/e2e"
            self.assertEqual(len(events), 3)
            self.assertTrue(any(f"pytest {e2e} " in event for event in events[1:]))
            self.assertTrue(any(f"pytest packages/skillz/tests --ignore {e2e} " in event for event in events[1:]))

    def test_invalid_worker_count_fails_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self._fake_env(root)
            for name, value in (("GATE_JOBS", "0"), ("TEST_WORKERS", "auto"), ("TEST_WORKERS", "-1")):
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

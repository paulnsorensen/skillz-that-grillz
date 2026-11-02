#!/usr/bin/env python3
"""Tests for check_test_durations.py: per-file sums, measured ceilings, missing data, and the one-way ratchet."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from collections.abc import Generator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_test_durations as durations  # noqa: E402

REASON = 'reason = "Measured."'
SLOW = "tests/test_slow.py"
FAST = "tests/test_fast.py"


@contextmanager
def _root() -> Generator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def _tree(root: Path, tests: dict[str, float], exceptions: str = "", limit: int = 20) -> Path:
    """Write a pyproject budget and a durations file; return the durations path."""
    _ = (root / "pyproject.toml").write_text(
        f"[tool.skillz.test-time]\nmax_seconds = {limit}\n\n[tool.skillz.test-time.exceptions]\n{exceptions}")
    path = root / ".test_durations"
    _ = path.write_text(json.dumps(tests))
    return path


def _junit(directory: Path, name: str, *counts: int, nested: bool = True) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    suites = "".join(f'<testsuite name="pytest" tests="{count}" />' for count in counts)
    _ = (directory / name).write_text(f"<testsuites>{suites}</testsuites>" if nested else suites)


def _run(root: Path, *args: str | Path) -> tuple[int, str]:
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        code = durations.main([str(arg) for arg in args], root=root)
    return code, output.getvalue()


def _file(seconds: float, tests: int, prefix: str = SLOW) -> dict[str, float]:
    return {f"{prefix}::test_{n}": seconds / tests for n in range(tests)}


class TestTimeTest(unittest.TestCase):
    def test_files_within_the_budget_pass(self) -> None:
        with _root() as root:
            path = _tree(root, {**_file(19.5, 3), **_file(4, 2, FAST)})
            self.assertEqual(_run(root, "check", path),
                             (0, "test-time ratchet: 2 files within budget (0 measured exceptions)\n"))

    def test_a_file_at_60_seconds_fails_and_names_the_file_the_seconds_and_the_ceiling(self) -> None:
        with _root() as root:
            path = _tree(root, {**_file(60, 4), **_file(1, 1, FAST)})
            code, output = _run(root, "check", path)
            self.assertEqual(code, 1)
            self.assertEqual(output, ("test-time ratchet: 1 file(s) above the ceiling (default 20 s)\n"
                                      f"{SLOW}: 60.00 s (ceiling 20 s); speed up or split the file\n"))

    def test_a_file_sums_setup_call_and_teardown_of_every_test_and_its_parameters(self) -> None:
        stored = {f"{SLOW}::test_a[1]": 8.0, f"{SLOW}::test_a[2]": 8.0, f"{SLOW}::Group::test_b": 4.5}
        self.assertEqual(durations.per_file(stored), {SLOW: durations.FileTime(20.5, 3)})

    def test_an_exception_holds_a_file_to_its_measured_ceiling(self) -> None:
        with _root() as root:
            path = _tree(root, _file(48.4, 484), f'"{SLOW}" = {{ max_seconds = 73, {REASON} }}\n')
            self.assertEqual(_run(root, "check", path)[0], 0)
            path = _tree(root, _file(73.5, 3), f'"{SLOW}" = {{ max_seconds = 73, {REASON} }}\n')
            code, output = _run(root, "check", path)
            self.assertEqual(code, 1)
            self.assertIn(f"{SLOW}: 73.50 s (ceiling 73 s)", output)

    def test_a_ceiling_is_one_and_a_half_times_the_measured_seconds_rounded_up(self) -> None:
        self.assertEqual(durations.ceiling_for(sum([0.1] * 484)), 73)
        self.assertEqual(durations.ceiling_for(20.0), 30)
        self.assertEqual(durations.ceiling_for(20.01), 31)

    def test_missing_empty_and_malformed_durations_fail_check_with_a_code(self) -> None:
        cases = {"TIME-MISSING": None, "TIME-EMPTY": "{}", "TIME-INVALID": "[1, 2]"}
        for code, text in cases.items():
            with self.subTest(code=code), _root() as root:
                path = _tree(root, {})
                if text is None:
                    path.unlink()
                else:
                    _ = path.write_text(text)
                status, output = _run(root, "check", path)
                self.assertEqual(status, 1)
                self.assertTrue(output.startswith(f"test-time ratchet [{code}]: {path}"), output)

    def test_a_duration_that_is_not_a_finite_non_negative_number_is_invalid(self) -> None:
        for value in ("true", '"1.0"', "-1", "NaN", "Infinity"):
            with self.subTest(value=value), _root() as root:
                path = _tree(root, {})
                _ = path.write_text(f'{{"{SLOW}::test_a": {value}}}')
                status, output = _run(root, "check", path)
                self.assertEqual(status, 1)
                self.assertIn("[TIME-INVALID]", output)

    def test_check_fails_when_the_durations_hold_fewer_ids_than_the_shards_selected(self) -> None:
        with _root() as root:
            path = _tree(root, _file(3, 3))
            collected = root / "durations"
            _junit(collected, "skillz-pytest.1of2.xml", 2)
            _junit(collected, "skillz-pytest.2of2.xml", 1, nested=False)
            _ = (collected / "skillz-pytest.1of2.json").write_text("{}")
            self.assertEqual(_run(root, "check", path, "--collected", collected)[0], 0)
            _junit(collected, "skillz-e2e-pytest.1of2.xml", 1, 0)
            code, output = _run(root, "check", path, "--collected", collected)
            self.assertEqual(code, 1)
            self.assertEqual(output, (f"test-time ratchet [TIME-SHORT]: {path} holds 3 test ids, fewer than the 4 "
                                      "tests that the shards selected\n"))

    def test_check_fails_when_no_collected_count_is_readable(self) -> None:
        with _root() as root:
            path = _tree(root, _file(3, 3))
            collected = root / "durations"
            self.assertIn("[TIME-NO-COUNT]", _run(root, "check", path, "--collected", collected)[1])
            for text in ("<testsuites>", "<testsuites><testsuite tests='x' /></testsuites>", "<testsuites />"):
                with self.subTest(text=text):
                    collected.mkdir(exist_ok=True)
                    _ = (collected / "lane.xml").write_text(text)
                    code, output = _run(root, "check", path, "--collected", collected)
                    self.assertEqual(code, 1)
                    self.assertIn("[TIME-NO-COUNT]: lane.xml", output)

    def test_check_reports_improved_and_stale_exceptions_but_passes(self) -> None:
        exceptions = (f'"{SLOW}" = {{ max_seconds = 73, {REASON} }}\n'
                      f'"{FAST}" = {{ max_seconds = 40, {REASON} }}\n'
                      f'"tests/test_gone.py" = {{ max_seconds = 30, {REASON} }}\n')
        with _root() as root:
            path = _tree(root, {**_file(30, 2), **_file(5, 1, FAST)}, exceptions)
            code, output = _run(root, "check", path)
            self.assertEqual(code, 0, output)
            self.assertIn(f"{SLOW}: 30.00 s, ceiling 73 s can drop to 45 s; run `tighten`", output)
            self.assertIn(f"{FAST}: 5.00 s fits the 20 s budget; run `tighten`", output)
            self.assertIn("tests/test_gone.py: the exception names a file with no durations", output)

    def test_tighten_lowers_and_removes_but_never_raises(self) -> None:
        over = "tests/test_over.py"
        exceptions = (f'"{SLOW}" = {{ max_seconds = 73, {REASON} }}  # seed\n'
                      f'\n"{FAST}" = {{ max_seconds = 40, {REASON} }}\n'
                      f'"{over}" = {{ {REASON}, max_seconds = 25 }}\n\n# trailer\n')
        with _root() as root:
            path = _tree(root, {**_file(30, 2), **_file(13.3, 1, FAST), **_file(24, 2, over)}, exceptions)
            self.assertEqual(_run(root, "tighten", path)[0], 0)
            text = (root / "pyproject.toml").read_text()
            self.assertIn((f'"{SLOW}" = {{ max_seconds = 45, {REASON} }}  # seed\n\n"{over}" = '
                           f'{{ {REASON}, max_seconds = 25 }}\n\n# trailer\n'), text)
            self.assertNotIn(FAST, text)
            self.assertEqual(_run(root, "check", path),
                             (0, "test-time ratchet: 3 files within budget (2 measured exceptions)\n"))

    def test_tighten_needs_the_durations_and_one_line_exceptions(self) -> None:
        with _root() as root:
            path = _tree(root, _file(30, 2), f'"{SLOW}" = {{ max_seconds = 73,\n {REASON} }}\n')
            self.assertEqual(_run(root, "tighten", path)[0], 2)
            path.unlink()
            self.assertEqual(_run(root, "tighten", path)[0], 1)

    def test_report_prints_the_slowest_files_and_never_fails(self) -> None:
        tests = {key: value for n in range(12) for key, value in _file(n + 1, 1, f"tests/test_{n:02}.py").items()}
        with _root() as root:
            path = _tree(root, {**tests, **_file(60, 2)}, f'"tests/test_00.py" = {{ max_seconds = 9, {REASON} }}\n')
            code, output = _run(root, "report", path)
            self.assertEqual(code, 0)
            lines = output.splitlines()
            self.assertEqual(lines[:3], [f"test-time report: 13 files, 14 tests, 138.0 s in {path}",
                                         "  seconds  ceiling  tests  file",
                                         f"    60.00       20      2  {SLOW}"])
            self.assertEqual(len([line for line in lines if line.endswith(".py") and "  tests/" in line]), 10)
            self.assertNotIn("tests/test_00.py", "\n".join(lines[:12]))
            self.assertIn(f"breach: {SLOW}: 60.00 s (ceiling 20 s)", output)
            self.assertIn("tests/test_00.py: 1.00 s fits the 20 s budget", output)
            path.unlink()
            code, output = _run(root, "report", path)
            missing = f"test-time ratchet [TIME-MISSING]: {path}: the durations file is missing\n"
            self.assertEqual((code, output), (0, missing))

    def test_a_bad_budget_is_an_input_error_but_report_still_exits_zero(self) -> None:
        blank = f'"{SLOW}" = {{ max_seconds = 9, reason = " " }}\n'
        for table in ("[tool.other]\n", "[tool.skillz.test-time]\nmax_seconds = 0\n",
                      f"[tool.skillz.test-time]\nmax_seconds = 20\n[tool.skillz.test-time.exceptions]\n{blank}"):
            with self.subTest(table=table), _root() as root:
                path = _tree(root, _file(3, 3))
                _ = (root / "pyproject.toml").write_text(table)
                self.assertEqual(_run(root, "check", path)[0], 2)
                self.assertEqual(_run(root, "report", path)[0], 0)

    def test_a_bad_command_is_a_usage_error(self) -> None:
        with _root() as root:
            path = _tree(root, _file(3, 3))
            for args in (("grow", path), ("check",), ("report", path, "--collected", root)):
                with self.subTest(args=args):
                    self.assertEqual(_run(root, *args)[0], 2)

    def test_the_committed_durations_hold_their_budget(self) -> None:
        committed = durations.ROOT / "packages/skillz/.test_durations"
        code, output = _run(durations.ROOT, "check", committed)
        self.assertEqual(code, 0, output)
        self.assertEqual(_run(durations.ROOT, "report", committed)[0], 0)


if __name__ == "__main__":
    _ = unittest.main()

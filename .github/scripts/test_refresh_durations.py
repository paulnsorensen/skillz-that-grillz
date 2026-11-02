#!/usr/bin/env python3
"""Tests for refresh_durations.py: the per-test maximum of the runs, the no-change and changed paths, and bad input."""
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

import check_test_durations as ratchet  # noqa: E402
import refresh_durations as refresh  # noqa: E402

SLOW = "tests/test_slow.py"
FAST = "tests/test_fast.py"
BUDGET = "[tool.skillz.test-time]\nmax_seconds = 20\n\n[tool.skillz.test-time.exceptions]\n"
LOOSE = 999
BOTH = "changed: packages/skillz/.test_durations, pyproject.toml\n"
DURATIONS_ONLY = "changed: packages/skillz/.test_durations\n"


def _waiver(ceiling: int) -> str:
    return f'"{SLOW}" = {{ max_seconds = {ceiling}, reason = "Measured." }}\n'


def _durations(slow: float, fast: float = 2.0) -> dict[str, float]:
    return {f"{SLOW}::test_a": slow / 2, f"{SLOW}::test_b": slow / 2, f"{FAST}::test_c": fast}


def _text(durations: dict[str, float]) -> str:
    return json.dumps(durations, indent=4, sort_keys=True) + "\n"


@contextmanager
def _tree(committed: dict[str, float], waiver: str) -> Generator[Path]:
    """Yield a repo root with a budget and a committed durations file."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _ = (root / "pyproject.toml").write_text(BUDGET + waiver)
        target = root / refresh.DURATIONS
        target.parent.mkdir(parents=True)
        _ = target.write_text(_text(committed))
        yield root


def _artifacts(root: Path, *runs: dict[str, float] | str) -> list[Path]:
    """Write one artifact per run, newest first; a string run is the raw file content."""
    paths: list[Path] = []
    for index, run in enumerate(runs):
        path = root / f"run{index}" / ".test_durations"
        path.parent.mkdir()
        _ = path.write_text(run if isinstance(run, str) else json.dumps(run))
        paths.append(path)
    return paths


def _run(root: Path, *args: str | Path) -> tuple[int, str]:
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        code = refresh.main([str(arg) for arg in args], root=root)
    return code, output.getvalue()


def _snapshot(root: Path) -> tuple[str, str]:
    return (root / "pyproject.toml").read_text(), (root / refresh.DURATIONS).read_text()


class CombineTest(unittest.TestCase):
    def test_each_test_of_the_newest_run_gets_its_maximum_over_the_runs(self) -> None:
        newest = {"a": 1.0, "c": 3.0}
        # `b` left the suite after the older run, so the newest run's ids decide the set.
        self.assertEqual(refresh.combine([newest, {"a": 5.0, "b": 9.0}, {"c": 1.0}]), {"a": 5.0, "c": 3.0})
        self.assertEqual(newest, {"a": 1.0, "c": 3.0})


class RefreshTest(unittest.TestCase):
    def test_three_runs_whose_maximum_equals_the_committed_file_are_no_change(self) -> None:
        with _tree(_durations(30), _waiver(ratchet.ceiling_for(30))) as root:
            before = _snapshot(root)
            runs = _artifacts(root, _durations(30), _durations(24), _durations(30))
            output = root / "github-output"
            self.assertEqual(_run(root, *runs, "--github-output", output), (0, "no change\n"))
            self.assertEqual(_snapshot(root), before)
            self.assertEqual(output.read_text(), "changed=false\n")

    def test_three_runs_commit_the_per_test_maximum_and_tighten_from_the_slowest_run(self) -> None:
        a, b, c = f"{SLOW}::test_a", f"{SLOW}::test_b", f"{FAST}::test_c"
        # Each run takes 12 s for the slow file, but each test peaks in a different run.
        newest, middle, oldest = {a: 10.0, b: 2.0, c: 1.0}, {a: 2.0, b: 10.0, c: 1.0}, {a: 6.0, b: 6.0, c: 1.0}
        with _tree(_durations(40), _waiver(LOOSE)) as root:
            runs = _artifacts(root, newest, middle, oldest)
            output = root / "github-output"
            self.assertEqual(_run(root, *runs, "--github-output", output), (0, BOTH))
            pyproject, durations = _snapshot(root)
            self.assertEqual(durations, _text({a: 10.0, b: 10.0, c: 1.0}))
            # The ceiling comes from the slowest run (12 s), not from the 20 s sum of the per-test maximum.
            self.assertGreater(ratchet.ceiling_for(12), 20)
            self.assertEqual(pyproject, BUDGET + _waiver(ratchet.ceiling_for(12)))
            self.assertEqual(output.read_text(), "changed=true\n")

    def test_the_slowest_run_sets_the_ceiling_not_the_newest(self) -> None:
        with _tree(_durations(40), _waiver(LOOSE)) as root:
            runs = _artifacts(root, _durations(22), _durations(28), _durations(25))
            self.assertEqual(_run(root, *runs), (0, BOTH))
            self.assertEqual(_snapshot(root), (BUDGET + _waiver(ratchet.ceiling_for(28)), _text(_durations(28))))

    def test_fewer_than_three_runs_refresh_the_durations_but_do_not_tighten(self) -> None:
        for count in (1, 2):
            with self.subTest(count=count), _tree(_durations(30), _waiver(LOOSE)) as root:
                runs = _artifacts(root, *[_durations(20)] * count)
                self.assertEqual(_run(root, *runs),
                                 (0, DURATIONS_ONLY + f"tighten skipped: {count} of 3 runs\n"))
                self.assertEqual(_snapshot(root), (BUDGET + _waiver(LOOSE), _text(_durations(20))))

    def test_slower_runs_replace_the_file_but_never_raise_a_ceiling(self) -> None:
        ceiling = ratchet.ceiling_for(30)
        with _tree(_durations(30), _waiver(ceiling)) as root:
            runs = _artifacts(root, *[_durations(40)] * 3)
            self.assertEqual(_run(root, *runs), (0, DURATIONS_ONLY))
            self.assertEqual(_snapshot(root)[0], BUDGET + _waiver(ceiling))

    def test_a_loose_ceiling_tightens_when_the_durations_are_equal(self) -> None:
        with _tree(_durations(30), _waiver(LOOSE)) as root:
            runs = _artifacts(root, *[_durations(30)] * 3)
            self.assertEqual(_run(root, *runs), (0, "changed: pyproject.toml\n"))
            self.assertEqual(_snapshot(root)[0], BUDGET + _waiver(ratchet.ceiling_for(30)))

    def test_a_file_that_fits_the_default_loses_its_waiver(self) -> None:
        self.assertLessEqual(ratchet.ceiling_for(5), 20)
        with _tree(_durations(30), _waiver(LOOSE)) as root:
            self.assertEqual(_run(root, *_artifacts(root, *[_durations(5)] * 3)), (0, BOTH))
            self.assertEqual(_snapshot(root)[0], BUDGET)

    def test_a_missing_or_malformed_committed_file_is_replaced(self) -> None:
        for committed in ("", "{}", "not json"):
            with self.subTest(committed=committed), _tree({}, _waiver(LOOSE)) as root:
                _ = (root / refresh.DURATIONS).write_text(committed)
                status, text = _run(root, *_artifacts(root, _durations(30)))
                self.assertEqual((status, text.splitlines()[0]), (0, DURATIONS_ONLY.strip()))
                self.assertEqual(_snapshot(root)[1], _text(_durations(30)))

    def test_a_bad_artifact_in_any_position_fails_with_a_code_and_writes_nothing(self) -> None:
        cases = ((None, "TIME-MISSING"), ("{}", "TIME-EMPTY"), ("[]", "TIME-INVALID"),
                 ('{"t.py::a": -1}', "TIME-INVALID"))
        for position in (0, 2):
            for content, code in cases:
                with (self.subTest(position=position, code=code, content=content),
                      _tree(_durations(30), _waiver(LOOSE)) as root):
                    before = _snapshot(root)
                    runs = _artifacts(root, *[_durations(20)] * 3)
                    runs[position] = root / "absent"
                    if content is not None:
                        _ = runs[position].write_text(content)
                    output = root / "github-output"
                    status, text = _run(root, *runs, "--github-output", output)
                    self.assertEqual(status, 1)
                    self.assertIn(f"[{code}]", text)
                    self.assertEqual(_snapshot(root), before)
                    self.assertFalse(output.exists())

    def test_a_waiver_that_tighten_cannot_edit_is_an_input_error_and_writes_nothing(self) -> None:
        # Valid TOML, but not the one-line inline table that `tighten` edits.
        split = f'\n[tool.skillz.test-time.exceptions."{SLOW}"]\nmax_seconds = {LOOSE}\nreason = "Measured."\n'
        with _tree(_durations(30), split) as root:
            before = _snapshot(root)
            status, text = _run(root, *_artifacts(root, *[_durations(20)] * 3))
            self.assertEqual(status, 2)
            self.assertIn("tighten needs the exception on one line", text)
            self.assertEqual(_snapshot(root), before)

    def test_a_bad_command_line_is_a_usage_error(self) -> None:
        with _tree(_durations(30), _waiver(LOOSE)) as root:
            for args in ((), ("--github-output", "o"), ("a", "--github-output"), ("a", "--github-output", "o", "b"),
                         ("a", "--output", "c")):
                with self.subTest(args=args):
                    self.assertEqual(_run(root, *args), (2, refresh.USAGE + "\n"))


if __name__ == "__main__":
    _ = unittest.main()

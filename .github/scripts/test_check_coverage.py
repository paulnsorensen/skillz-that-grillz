#!/usr/bin/env python3
"""Tests for check_coverage.py and check_dead_code_coverage.py, with real coverage data and a real Git base."""
from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Generator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_coverage as gate  # noqa: E402
import check_dead_code_coverage as dead_code  # noqa: E402

MODULE = """import functools


def used(flag=False):
    if flag:
        return 0
    return 1


def dead():
    value = 1
    return value


def oneliner(): return 2


class Shape:
    def dead_method(self):
        return 3


@functools.cache
def decorated():
    return 4


def allowed():
    return 5


def entry():
    return 6
"""
CONFIG = """[tool.vulture]
ignore_decorators = ["@functools.cache"]

[tool.coverage.report]
fail_under = {floor}

[tool.skillz.coverage]
diff_fail_under = 95

[tool.skillz.dead-code]
allow = ["p.mod:allowed"]
"""
QMODULE = "def helper():\n    return 7\n"
DRIVER = ("import sys\nsys.path[:0] = sys.argv[1:]\nfrom p.mod import used\nfrom q.mod import helper\n"
          "used()\nhelper()\n")


TRIMMED = MODULE.split("\n\n\ndef dead")[0] + "\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root,
                          check=True, capture_output=True, text=True).stdout.strip()


class _World:
    """A Git repository with two workspace packages `p` and `q`, committed as the diff base.

    Two packages match production, which measures every `packages/*/src` tree with comma-joined sources.
    """

    def __init__(self, root: Path) -> None:
        self.root: Path = root
        self.data: Path = root / "data"
        self.source: Path = root / "packages/p/src"
        self.other: Path = root / "packages/q/src"
        self.data.mkdir()
        (self.source / "p").mkdir(parents=True)
        (self.other / "q").mkdir(parents=True)
        _ = (self.source / "p/__init__.py").write_text("")
        _ = (self.source / "p/mod.py").write_text(MODULE)
        _ = (self.other / "q/__init__.py").write_text("")
        _ = (self.other / "q/mod.py").write_text(QMODULE)
        _ = (root / "packages/p/pyproject.toml").write_text('[project.scripts]\np = "p.mod:entry"\n')
        self.config(0)
        _ = (root / ".gitignore").write_text("data/\ndriver.py\n")
        _ = _git(root, "init", "-q")
        _ = _git(root, "add", ".")
        _ = _git(root, "commit", "-q", "-m", "base")
        self.base: str = _git(root, "rev-parse", "HEAD")

    def config(self, floor: int) -> None:
        _ = (self.root / "pyproject.toml").write_text(CONFIG.format(floor=floor))

    def module(self, text: str) -> None:
        _ = (self.source / "p/mod.py").write_text(text)

    def measure(self, name: str = "lane.cov") -> None:
        """Run the driver under coverage.py and write the data file `name`."""
        _ = (self.root / "driver.py").write_text(DRIVER)
        _ = subprocess.run([sys.executable, "-m", "coverage", "run", f"--data-file={self.data / name}",
                            f"--source={self.source},{self.other}", "driver.py", str(self.source), str(self.other)],
                           cwd=self.root, check=True, capture_output=True)

    def gate(self) -> tuple[int, str]:
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = gate.main([str(self.data), "--base", self.base], root=self.root)
        return code, output.getvalue()


@contextmanager
def _world() -> Generator[_World]:
    with tempfile.TemporaryDirectory() as directory:
        yield _World(Path(directory).resolve())


class CoverageGateTest(unittest.TestCase):
    def test_dead_code_finds_whole_zero_hit_functions_and_honors_exemptions(self) -> None:
        with _world() as world:
            world.measure()
            code, output = world.gate()
            self.assertEqual(code, 1)
            self.assertIn("packages/p/src/p/mod.py:10: p.mod:dead is whole-symbol dead code", output)
            self.assertIn("packages/p/src/p/mod.py:19: p.mod:Shape.dead_method is whole-symbol dead code", output)
            self.assertEqual(output.count("whole-symbol dead code"), 2, output)

    def test_module_names_cover_only_package_sources(self) -> None:
        with _world() as world:
            self.assertEqual(dead_code.module_name(world.source / "p/mod.py", world.root), "p.mod")
            self.assertEqual(dead_code.module_name(world.source / "p/__init__.py", world.root), "p")
            self.assertIsNone(dead_code.module_name(world.root / "scripts/tool.py", world.root))

    def test_an_untracked_untested_function_in_a_second_package_fails_dead_code_and_diff(self) -> None:
        with _world() as world:
            world.module(TRIMMED)
            _ = _git(world.root, "commit", "-q", "-am", "trim")
            world.base = _git(world.root, "rev-parse", "HEAD")
            # Not added to Git: a local run must still see a new file before `git add`.
            _ = (world.other / "q/extra.py").write_text("def fresh(value):\n    doubled = value * 2\n    return doubled\n")
            world.measure()
            code, output = world.gate()
            self.assertEqual(code, 1, output)
            self.assertIn("packages/q/src/q/extra.py:1: q.extra:fresh is whole-symbol dead code", output)
            # The file was never imported, so its `def` line counts as measured and missed too.
            self.assertIn("diff coverage 0% of 3 measured lines (floor 95%)", output)
            self.assertIn("  packages/q/src/q/extra.py: missing lines 1, 2, 3", output)

    def test_a_clean_change_passes_and_combines_every_lane(self) -> None:
        with _world() as world:
            world.module(TRIMMED)
            world.measure("one.cov")
            world.measure("two.1of2.cov")
            # The data file of a test subprocess, beside its lane file.
            world.measure("two.1of2.cov.host.42.Xabc")
            _ = (world.data / "notes.txt").write_text("not coverage data")
            code, output = world.gate()
            self.assertEqual(code, 0, output)
            self.assertIn("coverage: combined 3 lane data files", output)
            # The change only removes lines, so the diff check measures none and says so instead of a pass claim.
            self.assertIn("diff coverage: no measured source line in this diff against", output)
            self.assertTrue((world.data / "coverage.xml").is_file())

    def test_the_project_floor_fails_below_and_asks_for_a_raise_above(self) -> None:
        with _world() as world:
            world.module(TRIMMED)
            world.config(95)
            world.measure()
            code, output = world.gate()
            self.assertEqual(code, 1)
            self.assertIn("(floor 95%): below the floor; add tests", output)
            world.config(10)
            code, output = world.gate()
            self.assertEqual(code, 0, output)
            self.assertIn("raise [tool.coverage.report].fail_under to", output)

    def test_an_uncovered_added_line_fails_the_diff_check(self) -> None:
        with _world() as world:
            world.module(TRIMMED)
            _ = _git(world.root, "commit", "-q", "-am", "trim")
            world.base = _git(world.root, "rev-parse", "HEAD")
            world.module(TRIMMED.replace("        return 0\n", "        value = 0\n        return value\n"))
            world.measure()
            code, output = world.gate()
            self.assertEqual(code, 1, output)
            self.assertIn("Failure. Coverage is below 95%", output)
            self.assertIn("diff coverage 0% of 2 measured lines (floor 95%)", output)
            self.assertIn("  packages/p/src/p/mod.py: missing lines 6, 7", output)
            self.assertNotIn("below the floor", output)

    def test_missing_lane_data_is_an_input_error(self) -> None:
        with _world() as world:
            code, output = world.gate()
            self.assertEqual(code, 2)
            self.assertIn("holds no *.cov lane data", output)

    def test_a_bad_diff_threshold_is_an_input_error(self) -> None:
        with _world() as world:
            _ = (world.root / "pyproject.toml").write_text("[tool.skillz.coverage]\ndiff_fail_under = 101\n")
            world.measure()
            self.assertEqual(world.gate()[0], 2)


if __name__ == "__main__":
    _ = unittest.main()

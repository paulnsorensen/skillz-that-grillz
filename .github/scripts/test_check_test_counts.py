#!/usr/bin/env python3
"""Tests for check_test_counts.py: the caps, the measured waivers, and the one-way ratchet."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from collections.abc import Generator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_test_counts as counts  # noqa: E402

REASON = 'reason = "Pre-existing."'
WIDE = "packages/p/tests/test_wide.py"
SMALL = "packages/p/tests/test_small.py"
TABLE = f"{WIDE}::test_table"


@contextmanager
def _root() -> Generator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        # pytest resolves its rootdir; on macOS the temp dir is a symlink (/var -> /private/var).
        yield Path(directory).resolve()


def _config(files: str = "", functions: str = "", max_cases: int = 5, max_rows: int = 3) -> str:
    return (f"[tool.skillz.test-size]\nmax_cases = {max_cases}\nmax_rows = {max_rows}\n\n"
            f"[tool.skillz.test-size.files]\n{files}\n[tool.skillz.test-size.functions]\n{functions}")


def _tree(root: Path, tests: dict[str, str], config: str) -> None:
    _ = (root / "pyproject.toml").write_text(config)
    for name, source in tests.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(source)


def _table(rows: int) -> str:
    return f"import pytest\n\n\n@pytest.mark.parametrize('n', range({rows}))\ndef test_table(n):\n    assert n >= 0\n"


def _run(root: Path, command: str) -> tuple[int, str]:
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        code = counts.main([command], root=root)
    return code, output.getvalue()


def _waived(ceilings: dict[str, int] | None) -> dict[str, counts.Waiver]:
    return {name: counts.Waiver(ceiling, "Pre-existing.") for name, ceiling in (ceilings or {}).items()}


def _budget(files: dict[str, int] | None = None, functions: dict[str, int] | None = None) -> counts.Budget:
    return counts.Budget(5, 3, _waived(files), _waived(functions))


class TestCountTest(unittest.TestCase):
    def test_tally_counts_each_file_and_only_parametrized_functions(self) -> None:
        tallied = counts.tally([f"{WIDE}::test_plain", f"{WIDE}::Group::test_row[a-1]", f"{WIDE}::Group::test_row[b-2]",
                                f"{SMALL}::test_one[x]"])
        self.assertEqual(tallied.files, {SMALL: 1, WIDE: 3})
        self.assertEqual(tallied.functions, {f"{SMALL}::test_one": 1, f"{WIDE}::Group::test_row": 2})

    def test_a_thirteen_row_table_over_a_twelve_row_cap_fails_and_names_the_function(self) -> None:
        with _root() as root:
            _tree(root, {WIDE: _table(13), SMALL: _table(2)}, _config(max_cases=80, max_rows=12))
            code, output = _run(root, "check")
            self.assertEqual(code, 1, output)
            self.assertEqual(output, ("test-size ratchet: 1 violation(s) against the caps "
                                      "(80 cases per file, 12 rows per parametrized function)\n"
                                      f"{TABLE}: 13 rows (ceiling 12); cut redundant rows or split the table\n"))

    def test_a_file_over_the_case_cap_fails_and_names_the_file(self) -> None:
        with _root() as root:
            plain = "".join(f"\n\ndef test_{name}():\n    pass\n" for name in "abc")
            _tree(root, {WIDE: _table(3) + plain}, _config())
            code, output = _run(root, "check")
            self.assertEqual(code, 1, output)
            self.assertIn(f"{WIDE}: 6 cases (ceiling 5); split the file or cut redundant cases\n", output)
            self.assertNotIn(f"{TABLE}:", output)

    def test_tests_within_the_caps_pass(self) -> None:
        with _root() as root:
            _tree(root, {WIDE: _table(3), SMALL: _table(2)}, _config())
            self.assertEqual(_run(root, "check"), (0, (
                "test-size ratchet: 2 files and 2 parametrized functions within the caps "
                "(5 cases per file, 3 rows per parametrized function; 0 measured waivers)\n")))

    def test_a_waiver_holds_a_count_to_its_measured_ceiling(self) -> None:
        budget = _budget({WIDE: 9}, {TABLE: 7})
        self.assertEqual(counts.violations(budget, counts.Counts({WIDE: 9}, {TABLE: 7})), [])
        self.assertEqual(counts.violations(budget, counts.Counts({WIDE: 10}, {TABLE: 8})), [
            f"{WIDE}: 10 cases (ceiling 9); split the file or cut redundant cases",
            f"{TABLE}: 8 rows (ceiling 7); cut redundant rows or split the table",
        ])

    def test_a_fallen_count_reports_improved_and_a_stale_waiver_fails(self) -> None:
        budget = _budget({WIDE: 9, SMALL: 8, "packages/p/tests/test_gone.py": 8}, {TABLE: 7})
        failures = counts.violations(budget, counts.Counts({WIDE: 7, SMALL: 4}, {TABLE: 2}))
        self.assertEqual(failures, [
            f"improved {WIDE}: 7 cases, below its ceiling 9; run `tighten`",
            f"improved {SMALL}: 4 cases fit the default cap 5; run `tighten`",
            "packages/p/tests/test_gone.py: the waiver names no collected test; run `tighten`",
            f"improved {TABLE}: 2 rows fit the default cap 3; run `tighten`",
        ])

    def test_tighten_lowers_and_removes_but_never_raises(self) -> None:
        over = "packages/p/tests/test_over.py"
        files = (f'"{WIDE}" = {{ max_cases = 12, {REASON} }}  # split later\n'
                 f'"{SMALL}" = {{ max_cases = 8, {REASON} }}\n'
                 f'"{over}" = {{ {REASON}, max_cases = 7 }}\n')
        functions = (f'"{TABLE}" = {{ reason = "was max_rows = 40", max_rows = 9 }}\n'
                     f'"{WIDE}::test_gone" = {{ max_rows = 9, {REASON} }}\n')
        text = _config(files, functions)
        budget = _budget({WIDE: 12, SMALL: 8, over: 7}, {TABLE: 9, f"{WIDE}::test_gone": 9})
        tightened = counts.tighten(text, budget, counts.Counts({WIDE: 7, SMALL: 4, over: 8}, {TABLE: 6}))
        self.assertEqual(tightened, _config(
            f'"{WIDE}" = {{ max_cases = 7, {REASON} }}  # split later\n"{over}" = {{ {REASON}, max_cases = 7 }}\n',
            f'"{TABLE}" = {{ reason = "was max_rows = 40", max_rows = 6 }}\n'))

    def test_tighten_skips_a_ceiling_key_inside_the_reason_string(self) -> None:
        reasons = ('reason = "cut, max_rows = 40 before"', "reason = 'cut, max_rows = 40 before'",
                   'reason = "a \\" quote, max_rows = 40"')
        for reason in reasons:
            text = _config("", f'"{TABLE}" = {{ {reason}, max_rows = 9 }}  # max_rows = 9\n')
            tightened = counts.tighten(text, _budget(functions={TABLE: 9}), counts.Counts({}, {TABLE: 6}))
            self.assertEqual(tightened, _config("", f'"{TABLE}" = {{ {reason}, max_rows = 6 }}  # max_rows = 9\n'))

    def test_tighten_rewrites_the_pyproject_to_the_measured_counts(self) -> None:
        with _root() as root:
            _tree(root, {WIDE: _table(4)}, _config(f'"{WIDE}" = {{ max_cases = 9, {REASON} }}\n',
                                                   f'"{TABLE}" = {{ max_rows = 6, {REASON} }}\n'))
            code, output = _run(root, "check")
            self.assertEqual(code, 1)
            self.assertIn(f"improved {TABLE}: 4 rows, below its ceiling 6; run `tighten`", output)
            self.assertEqual(_run(root, "tighten"), (0, ""))
            self.assertEqual((root / "pyproject.toml").read_text(),
                             _config("", f'"{TABLE}" = {{ max_rows = 4, {REASON} }}\n'))
            self.assertEqual(_run(root, "check")[0], 0)

    def test_a_waiver_without_a_reason_is_an_input_error(self) -> None:
        with _root() as root:
            _tree(root, {}, _config(functions=f'"{TABLE}" = {{ max_rows = 9, reason = " " }}\n'))
            self.assertEqual(_run(root, "check"), (2, f"{TABLE}: a waiver needs a non-empty reason\n"))

    def test_a_missing_table_or_cap_is_an_input_error(self) -> None:
        with _root() as root:
            _ = (root / "pyproject.toml").write_text("[tool.other]\n")
            self.assertEqual(_run(root, "check")[0], 2)
            _ = (root / "pyproject.toml").write_text("[tool.skillz.test-size]\nmax_cases = 80\n")
            self.assertEqual(_run(root, "check"), (2, "[tool.skillz.test-size].max_rows must be a positive integer\n"))

    def test_a_waiver_collection_that_is_not_a_table_is_an_input_error(self) -> None:
        for table in ("files", "functions"):
            with _root() as root:
                _ = (root / "pyproject.toml").write_text(
                    f"[tool.skillz.test-size]\nmax_cases = 80\nmax_rows = 12\n{table} = []\n")
                self.assertEqual(_run(root, "check"), (2, f"[tool.skillz.test-size.{table}] must be a table\n"))

    def test_a_collection_error_is_an_input_error(self) -> None:
        with _root() as root:
            _tree(root, {WIDE: "def test_broken(:\n"}, _config())
            code, output = _run(root, "check")
            self.assertEqual(code, 2)
            self.assertIn("packages/p/tests: pytest collection failed (exit 2)", output)

    def test_a_bad_command_is_a_usage_error(self) -> None:
        with _root() as root:
            _tree(root, {}, _config())
            self.assertEqual(_run(root, "grow")[0], 2)


if __name__ == "__main__":
    _ = unittest.main()

#!/usr/bin/env python3
"""Tests for check_file_lengths.py: the budget, the measured exceptions, and the one-way ratchet."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from collections.abc import Generator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_file_lengths as lengths  # noqa: E402

REASON = 'reason = "Pre-existing."'
BIG = "packages/p/src/p/big.py"
SMALL = "packages/p/src/p/small.py"


@contextmanager
def _root() -> Generator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def _tree(root: Path, files: dict[str, int], exceptions: str = "", limit: int = 5) -> None:
    _ = (root / "pyproject.toml").write_text(
        f"[tool.skillz.file-size]\nmax_lines = {limit}\n\n[tool.skillz.file-size.exceptions]\n{exceptions}")
    for name, count in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text("".join(f"x{n} = {n}\n" for n in range(count)))


def _run(root: Path, command: str) -> tuple[int, str]:
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        code = lengths.main([command], root=root)
    return code, output.getvalue()


class FileLengthTest(unittest.TestCase):
    def test_files_within_the_budget_pass(self) -> None:
        with _root() as root:
            _tree(root, {SMALL: 5, "packages/p/tests/test_long.py": 50, "packages/p/src/p/data.txt": 50})
            self.assertEqual(_run(root, "check"),
                             (0, "file-size ratchet: 1 files within budget (0 measured exceptions)\n"))

    def test_a_file_over_the_budget_fails(self) -> None:
        with _root() as root:
            _tree(root, {SMALL: 6})
            code, output = _run(root, "check")
            self.assertEqual(code, 1)
            self.assertIn(f"{SMALL}: 6 lines (limit 5); split the file", output)

    def test_an_exception_holds_a_file_to_its_measured_ceiling(self) -> None:
        with _root() as root:
            _tree(root, {BIG: 9}, f'"{BIG}" = {{ max_lines = 9, {REASON} }}\n')
            self.assertEqual(_run(root, "check")[0], 0)
            _tree(root, {BIG: 10}, f'"{BIG}" = {{ max_lines = 9, {REASON} }}\n')
            code, output = _run(root, "check")
            self.assertEqual(code, 1)
            self.assertIn(f"{BIG}: 10 lines (limit 9)", output)

    def test_an_exception_without_a_reason_is_an_input_error(self) -> None:
        with _root() as root:
            _tree(root, {BIG: 9}, f'"{BIG}" = {{ max_lines = 9, reason = " " }}\n')
            code, output = _run(root, "check")
            self.assertEqual(code, 2)
            self.assertIn("needs a non-empty reason", output)

    def test_a_missing_table_is_an_input_error(self) -> None:
        with _root() as root:
            _ = (root / "pyproject.toml").write_text("[tool.other]\n")
            self.assertEqual(_run(root, "check")[0], 2)

    def test_stale_and_loose_exceptions_fail_the_ratchet(self) -> None:
        exceptions = (f'"{BIG}" = {{ max_lines = 12, {REASON} }}\n'
                      f'"{SMALL}" = {{ max_lines = 8, {REASON} }}\n'
                      f'"packages/p/src/p/gone.py" = {{ max_lines = 8, {REASON} }}\n')
        with _root() as root:
            _tree(root, {BIG: 9, SMALL: 4}, exceptions)
            code, output = _run(root, "check")
            self.assertEqual(code, 1)
            self.assertIn(f"{BIG}: 9 lines, below its ceiling 12; run `tighten`", output)
            self.assertIn(f"{SMALL}: 4 lines fit the 5-line budget; run `tighten`", output)
            self.assertIn("packages/p/src/p/gone.py: the exception names a file that does not exist", output)

    def test_tighten_lowers_and_removes_but_never_raises(self) -> None:
        over = "packages/p/src/p/over.py"
        exceptions = (f'"{BIG}" = {{ max_lines = 12, {REASON} }}\n'
                      f'"{SMALL}" = {{ max_lines = 8, {REASON} }}\n'
                      f'"{over}" = {{ max_lines = 7, {REASON} }}\n')
        with _root() as root:
            _tree(root, {BIG: 9, SMALL: 4, over: 8}, exceptions)
            self.assertEqual(_run(root, "tighten")[0], 0)
            text = (root / "pyproject.toml").read_text()
            self.assertIn(f'"{BIG}" = {{ max_lines = 9, {REASON} }}\n', text)
            self.assertNotIn(SMALL, text)
            self.assertIn(f'"{over}" = {{ max_lines = 7, {REASON} }}\n', text)
            code, output = _run(root, "check")
            self.assertEqual(code, 1)
            self.assertEqual(output.count("\n"), 2, output)
            self.assertIn(f"{over}: 8 lines (limit 7)", output)

    def test_tighten_rewrites_only_the_key_and_keeps_comments_and_spacing(self) -> None:
        tricky = 'reason = "was max_lines = 400, see #12"'
        exceptions = (f'"{BIG}" = {{ {tricky}, max_lines = 12 }}  # split later\n'
                      f'\n"{SMALL}" = {{ max_lines = 8, {REASON} }}\n\n# trailer\n')
        with _root() as root:
            _tree(root, {BIG: 9, SMALL: 4}, exceptions)
            self.assertEqual(_run(root, "tighten")[0], 0)
            text = (root / "pyproject.toml").read_text()
            self.assertIn(f'"{BIG}" = {{ {tricky}, max_lines = 9 }}  # split later\n\n\n# trailer\n', text)
            self.assertEqual(_run(root, "check")[0], 0)

    def test_line_counts_match_newlines_not_form_feeds(self) -> None:
        with _root() as root:
            _tree(root, {})
            path = root / SMALL
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text("a = 1\n\f\nb = ' '\nc = 3\nd = 4\ne = 5")
            self.assertEqual(lengths.measure(root), {SMALL: 6})
            self.assertEqual(_run(root, "check")[0], 1)
    def test_a_bad_command_is_a_usage_error(self) -> None:
        with _root() as root:
            _tree(root, {})
            self.assertEqual(_run(root, "grow")[0], 2)

    def test_the_repository_holds_its_budget(self) -> None:
        code, output = _run(lengths.ROOT, "check")
        self.assertEqual(code, 0, output)


if __name__ == "__main__":
    _ = unittest.main()

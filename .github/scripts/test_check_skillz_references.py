#!/usr/bin/env python3
"""Unit tests for check_skillz_references.py."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import check_skillz_references as check  # noqa: E402


def _run(references: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = check.main(references)
    return code, out.getvalue(), err.getvalue()


class CheckSkillzReferencesTest(unittest.TestCase):
    def test_missing_directory_fails_with_message(self):
        with tempfile.TemporaryDirectory() as directory:
            code, _, err = _run(Path(directory) / "absent")
        self.assertEqual(code, 2)
        self.assertIn("references directory not found", err)

    def test_link_and_backtick_mentions_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text("See [b](b.md) and `references/c.md`.\n")
            (root / "b.md").write_text("No mention.\n")
            (root / "c.md").write_text("Reads `engine/references/other.md` only.\n")
            code, out, _ = _run(root)
        self.assertEqual(code, 1)
        self.assertIn("a.md:1: names b.md", out)
        self.assertIn("a.md:1: names c.md", out)
        self.assertNotIn("c.md:1", out)

    def test_independent_references_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text("Plain text.\n")
            (root / "b.md").write_text("More text.\n")
            self.assertEqual(_run(root)[0], 0)

    def test_shipped_references_pass(self):
        code, out, _ = _run(check.REFERENCES)
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()

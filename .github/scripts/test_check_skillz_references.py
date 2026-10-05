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

    def test_link_bare_and_backtick_mentions_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text(
                "See [b](b.md) and `references/c.md`.\nRead d.md next.\n"
            )
            (root / "b.md").write_text("No mention.\n")
            (root / "c.md").write_text("No mention.\n")
            (root / "d.md").write_text("No mention.\n")
            code, out, _ = _run(root)
        self.assertEqual(code, 1)
        self.assertIn("a.md:1: names b.md", out)
        self.assertIn("a.md:1: names c.md", out)
        self.assertIn("a.md:2: names d.md", out)

    def test_engine_references_are_allowed_but_mixed_line_still_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text(
                "Reads `engine/references/b.md` and [x](../engine/references/b.md).\n"
                "Reads `engine/references/b.md` then b.md.\n"
            )
            (root / "b.md").write_text("No mention.\n")
            code, out, _ = _run(root)
        self.assertEqual(code, 1)
        self.assertNotIn("a.md:1", out)
        self.assertIn("a.md:2: names b.md", out)

    def test_independent_references_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text("Plain text.\n")
            (root / "b.md").write_text("More text.\n")
            self.assertEqual(_run(root)[0], 0)

    def test_shipped_references_pass(self):
        self.assertTrue(check.reference_dirs())
        code, out, _ = _run(None)
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()

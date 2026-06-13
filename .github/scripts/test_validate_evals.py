#!/usr/bin/env python3
"""Unit tests for validate_evals.py.

The script ships an embedded `--self-test` (14 fixtures) that thoroughly
covers `validate_file`'s accept/reject contract. These tests cover the seam
the self-test does NOT reach: `main()`'s tree scan, glob discovery, the
empty / missing cases, the aggregate exit code, and — as a standing
regression guard — that the eval files actually shipped in this repo pass.
`just ci` runs `validate_evals.py` WITHOUT `--self-test`, so `main()` over
the live tree is the real gate; before this file it had no unit coverage.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import validate_evals  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parent.parent

VALID_EVALS = {
    "skill_name": "foo",
    "evals": [
        {"id": 0, "name": "a", "prompt": "p", "expected_output": "e", "files": []},
    ],
}


class ValidateEvalsMainTest(unittest.TestCase):
    """Exercise main() — the path `just ci` runs, untouched by --self-test."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        self.tmpdir = Path(tempfile.mkdtemp(prefix="validate-evals-"))
        self.addCleanup(self._restore)
        os.chdir(self.tmpdir)

    def _restore(self) -> None:
        os.chdir(self._cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_eval(self, skill: str, payload: object) -> None:
        path = self.tmpdir / "skills" / skill / "evals" / "evals.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _run(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = validate_evals.main()
        return rc, out.getvalue(), err.getvalue()

    def test_valid_tree_passes(self) -> None:
        self._write_eval("foo", VALID_EVALS)
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("validated 1", out)

    def test_skills_dir_missing(self) -> None:
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("skills/ directory not found", err)

    def test_no_eval_files(self) -> None:
        # An empty skills/ tree must fail loudly, not silently pass — otherwise
        # a botched rename that orphans every evals.json reports green.
        (self.tmpdir / "skills").mkdir()
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("no skills/*/evals/evals.json files found", err)

    def test_old_filename_not_discovered(self) -> None:
        # The contract is one filename: evals.json. A file left at the old
        # `eval_set.json` name must NOT be picked up — if it were, the rename
        # would be a no-op and the dual-schema split would persist.
        path = self.tmpdir / "skills" / "foo" / "evals" / "eval_set.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(VALID_EVALS), encoding="utf-8")
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("no skills/*/evals/evals.json files found", err)

    def test_one_bad_file_fails_whole_run(self) -> None:
        self._write_eval("good", VALID_EVALS)
        self._write_eval("bad", {"skill_name": "bad", "evals": []})
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("at least one entry", err)
        self.assertIn("1 error", err)

    def test_aggregates_across_files(self) -> None:
        self._write_eval("good", VALID_EVALS)
        self._write_eval("alsogood", {"skill_name": "x", "evals": [VALID_EVALS["evals"][0]]})
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("validated 2", out)


class ShippedEvalsTest(unittest.TestCase):
    """Regression guard: every eval file in THIS repo satisfies the contract."""

    def test_shipped_eval_files_validate(self) -> None:
        eval_files = sorted((REPO_ROOT / "skills").glob("*/evals/evals.json"))
        self.assertTrue(eval_files, "expected at least one shipped evals.json file")
        for ef in eval_files:
            with self.subTest(file=str(ef.relative_to(REPO_ROOT))):
                self.assertEqual(validate_evals.validate_file(ef), [])


class SelfTestPassesTest(unittest.TestCase):
    """The embedded --self-test fixtures must all pass (rc 0)."""

    def test_self_test_green(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = validate_evals.self_test()
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn(
            f"{len(validate_evals._SELF_TEST_CASES)}/{len(validate_evals._SELF_TEST_CASES)} passed",
            out.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()

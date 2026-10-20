#!/usr/bin/env python3
"""Unit tests for check_architecture.py."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import check_architecture as check  # noqa: E402

USER = "packages/app/src/app/use.py"


def _workspace(root: Path, source: str) -> None:
    """Write a workspace: `app` (import packages `app` and `app_extra`) and `lib` (import package `lib`)."""
    for package in ("app/src/app", "app/src/app_extra", "lib/src/lib"):
        (root / "packages" / package).mkdir(parents=True)
        _ = (root / "packages" / package / "__init__.py").write_text("")
    _ = (root / "packages/lib/src/lib/_core.py").write_text("VALUE = 1\n")
    _ = (root / "packages/app/src/app_extra/_inner.py").write_text("VALUE = 1\n")
    _ = (root / USER).write_text(source)


def _run(source: str, allowed: frozenset[tuple[str, str]] | None = None) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _workspace(root, source)
        out = io.StringIO()
        with redirect_stdout(out):
            code = check.main(root, allowed or frozenset())
    return code, out.getvalue()


class CheckArchitectureTest(unittest.TestCase):
    def test_each_import_form_of_another_package_private_module_fails(self):
        code, out = _run("from lib._core import VALUE\nimport lib._core\nfrom lib import _core\n")
        self.assertEqual(code, 1)
        for line in (1, 2, 3):
            self.assertIn(f"{USER}:{line}: imports lib._core, a private module of workspace package lib", out)
        self.assertIn("add it to ALLOWED", out)

    def test_a_private_import_inside_one_type_checking_block_fails(self):
        code, out = _run("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from lib._core import VALUE\n")
        self.assertEqual(code, 1)
        self.assertIn(f"{USER}:3: imports lib._core", out)

    def test_an_attribute_chain_through_an_imported_package_fails(self):
        code, out = _run("import lib\nimport lib as alias\nX = lib._core.VALUE\nY = alias._core\nZ = lib.public\n")
        self.assertEqual(code, 1)
        for line in (3, 4):
            self.assertIn(f"{USER}:{line}: imports lib._core", out)
        self.assertNotIn(f"{USER}:5:", out)

    def test_public_dunder_and_same_package_imports_pass(self):
        source = "import lib\nfrom lib import __version__\nfrom app_extra._inner import VALUE\nfrom . import _local\nimport _thread\n"
        self.assertEqual(_run(source), (0, ""))

    def test_an_allowed_import_passes(self):
        self.assertEqual(_run("from lib._core import VALUE\n", frozenset({(USER, "lib._core")})), (0, ""))

    def test_an_allowed_entry_without_its_import_fails(self):
        code, out = _run("import lib\n", frozenset({(USER, "lib._core")}))
        self.assertEqual(code, 1)
        self.assertIn(f"{USER}: ALLOWED names lib._core, but the import is gone: remove the entry", out)

    def test_the_repository_matches_its_allowlist(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = check.main()
        self.assertEqual(code, 0, out.getvalue())


if __name__ == "__main__":
    _ = unittest.main()

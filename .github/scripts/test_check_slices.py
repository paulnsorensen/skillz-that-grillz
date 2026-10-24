#!/usr/bin/env python3
"""Tests for check_slices.py: each rule fails on a planted violation and passes once it is reverted."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from typing_extensions import override

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import check_slices as guard  # noqa: E402

SRC = "packages/shop/src/shop"
MAP = f"""version = 1

[policy]
language = "python"
unmapped_files = "error"
suppressions = "human-only"
roots = ["packages/shop/src"]

[[slice]]
name = "orders"
paths = ["{SRC}/orders"]
crust = ["{SRC}/orders/__init__.py"]
shape = "thin-facade"
may_depend_on = ["pricing"]
requires = ["pricing"]

[[slice]]
name = "pricing"
paths = ["{SRC}/pricing"]
crust = ["{SRC}/pricing/__init__.py"]
shape = "thin-facade"
may_depend_on = []

[[spine]]
name = "app"
paths = ["{SRC}/__init__.py", "{SRC}/_cli.py"]
may_depend_on = ["orders", "pricing"]
"""
TREE = {
    "__init__.py": "",
    "_cli.py": "from shop.orders import place\n\nprint(place)\n",
    "orders/__init__.py": "from shop.orders._place import place\n\n__all__ = ['place']\n",
    "orders/_place.py": "from shop.pricing import price\n\n\ndef place():\n    return price()\n",
    "pricing/__init__.py": "from shop.pricing._tiers import price\n\n__all__ = ['price']\n",
    "pricing/_tiers.py": "def price():\n    return 1\n",
}


def _git(root: Path, *args: str) -> None:
    _ = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                       capture_output=True)


class GuardTest(unittest.TestCase):
    root: Path = Path()

    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name, text in TREE.items():
            self.write(f"{SRC}/{name}", text)
        self.write("slices.toml", MAP)
        _git(self.root, "init", "-q")

    def write(self, path: str, text: str) -> None:
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        _ = (self.root / path).write_text(text)

    def commit(self) -> None:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "--allow-empty", "-m", "base")

    def run_guard(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = guard.main(list(args), self.root)
        return code, out.getvalue()

    def adopt(self) -> None:
        """Commit an adopted tree: the slice map and an empty baseline."""
        self.assertEqual(self.run_guard("baseline", "--init")[0], 0)
        self.commit()

    def assert_fails(self, *expected: str) -> str:
        code, out = self.run_guard("check", "--base", "HEAD", "--strict")
        self.assertEqual(code, 1, out)
        for line in expected:
            self.assertIn(line, out)
        return out

    def assert_passes(self) -> None:
        code, out = self.run_guard("check", "--base", "HEAD", "--strict")
        self.assertEqual((code, "slice guard: pass" in out), (0, True), out)

    def test_the_clean_fixture_passes(self) -> None:
        self.adopt()
        self.assert_passes()

    def test_sb00_an_unresolved_first_party_import_fails_and_blocks_init(self) -> None:
        self.write(f"{SRC}/_cli.py", "import shop.missing\nfrom shop.orders import place\n")
        code, out = self.run_guard("baseline", "--init")
        self.assertEqual(code, 1)
        self.assertIn("SB-00 unresolved import: packages/shop/src/shop/_cli.py -> shop.missing", out)
        self.assertFalse((self.root / guard.BASELINE_FILE).exists())
        self.write(guard.BASELINE_FILE, '{"version": 1, "entries": [{"rule": "SB-00", '
                   + f'"from": "{SRC}/_cli.py", "to": "shop.missing"}}]}}\n')
        self.commit()
        _ = self.assert_fails("SB-00 unresolved import")  # A baseline entry never hides SB-00.

    def test_sb01_each_form_of_a_crust_bypass_fails(self) -> None:
        self.adopt()
        for source in ("from shop.orders._place import place\n", "import shop.orders._place\n",
                       "from shop.orders import _place\n", "from shop import orders\norders._place.place()\n",
                       "from .orders import _place\n"):
            with self.subTest(source=source):
                self.write(f"{SRC}/_cli.py", source + "from shop.orders import place\n")
                _ = self.assert_fails(f"SB-01 crust bypass: {SRC}/_cli.py -> {SRC}/orders/_place.py")
        self.write(f"{SRC}/_cli.py", TREE["_cli.py"])
        self.assert_passes()

    def test_sb02_and_sb03_an_undeclared_back_edge_fails_as_a_cycle(self) -> None:
        self.adopt()
        self.write(f"{SRC}/pricing/_tiers.py", "from shop.orders import place\n\n\ndef price():\n    return 1\n")
        _ = self.assert_fails(f"SB-02 undeclared dependency: {SRC}/pricing/_tiers.py -> {SRC}/orders/__init__.py",
                              f"SB-03 dependency cycle: {SRC}/orders/_place.py -> {SRC}/pricing/__init__.py",
                              f"SB-03 dependency cycle: {SRC}/pricing/_tiers.py -> {SRC}/orders/__init__.py")

    def test_sb03_a_new_edge_inside_a_baselined_cycle_fails(self) -> None:
        self.write(f"{SRC}/pricing/_tiers.py", "from shop.orders import place\n\n\ndef price():\n    return 1\n")
        self.adopt()
        self.assert_passes()
        self.write(f"{SRC}/pricing/__init__.py", TREE["pricing/__init__.py"] + "from shop.orders import place\n")
        out = self.assert_fails(f"SB-03 dependency cycle: {SRC}/pricing/__init__.py -> {SRC}/orders/__init__.py")
        self.assertNotIn("_tiers.py ->", out)

    def test_sb04_an_unmapped_file_and_a_symlink_fail(self) -> None:
        self.adopt()
        self.write(f"{SRC}/_extra.py", "")
        os.symlink(self.root / SRC / "pricing/_tiers.py", self.root / SRC / "pricing/_link.py")
        _ = self.assert_fails(f"SB-04 unmapped source file: {SRC}/_extra.py -> unmapped",
                              f"SB-04 unmapped source file: {SRC}/pricing/_link.py -> symlink")

    def test_sb06_a_missing_required_edge_fails(self) -> None:
        self.adopt()
        self.write(f"{SRC}/orders/_place.py", "def place():\n    return 1\n")
        _ = self.assert_fails("SB-06 missing required dependency: orders -> pricing")

    def test_sb07_an_inline_directive_fails_and_a_string_does_not(self) -> None:
        self.adopt()
        self.write(f"{SRC}/_cli.py", TREE["_cli.py"] + "NOTE = '# sbguard-ignore'\n")
        self.assert_passes()
        self.write(f"{SRC}/_cli.py", TREE["_cli.py"] + "import os  # sbguard-ignore\n")
        _ = self.assert_fails(f"SB-07 suppression or baseline growth: {SRC}/_cli.py -> inline:sbguard-ignore:")

    def test_sb07_baseline_growth_and_deletion_fail_against_the_base(self) -> None:
        self.adopt()
        self.write(f"{SRC}/_extra.py", "")
        self.write(guard.BASELINE_FILE, '{"version": 1, "entries": [{"rule": "SB-04", '
                   + f'"from": "{SRC}/_extra.py", "to": "unmapped"}}]}}\n')
        _ = self.assert_fails(f"baseline:grew:SB-04|{SRC}/_extra.py|unmapped")
        (self.root / SRC / "_extra.py").unlink()
        (self.root / guard.BASELINE_FILE).unlink()
        _ = self.assert_fails(f"SB-07 suppression or baseline growth: {guard.BASELINE_FILE} -> baseline:deleted")

    def test_the_adoption_change_may_add_the_baseline(self) -> None:
        _ = (self.root / "slices.toml").rename(self.root / "draft.toml")
        self.commit()
        _ = (self.root / "draft.toml").rename(self.root / "slices.toml")
        self.write(f"{SRC}/_extra.py", "")
        self.assertEqual(self.run_guard("baseline", "--init")[0], 0)
        self.assert_passes()

    def test_a_fixed_violation_cannot_pay_for_a_new_one(self) -> None:
        self.write(f"{SRC}/_old.py", "")
        self.adopt()
        (self.root / SRC / "_old.py").unlink()
        self.write(f"{SRC}/_new.py", "")
        out = self.assert_fails(f"{SRC}/_new.py -> unmapped")
        self.assertIn("STALE baseline entry", out)
        code, out = self.run_guard("check", "--base", "HEAD")
        self.assertEqual(code, 1)
        self.assertNotIn("STALE", out)

    def test_shrink_removes_fixed_entries_and_never_adds(self) -> None:
        self.write(f"{SRC}/_old.py", "")
        self.adopt()
        (self.root / SRC / "_old.py").unlink()
        self.write(f"{SRC}/_new.py", "")
        self.assertEqual(self.run_guard("baseline", "--shrink")[0], 0)
        self.assertEqual(guard.decode((self.root / guard.BASELINE_FILE).read_text(), "baseline"), set())
        self.assertEqual(self.run_guard("baseline", "--init")[0], 1)  # init refuses to overwrite.

    def test_a_suppression_with_a_reason_accepts_one_identity_and_goes_stale(self) -> None:
        self.adopt()
        self.write("slices.toml", MAP + f'\n[[suppress]]\nrule = "SB-04"\nfrom = "{SRC}/_extra.py"\nto = "unmapped"\n'
                   + 'reason = "generated file"\n')
        self.write(f"{SRC}/_extra.py", "")
        self.assert_passes()
        (self.root / SRC / "_extra.py").unlink()
        _ = self.assert_fails("STALE [[suppress]] entry")

    def test_invalid_maps_are_input_errors(self) -> None:
        self.adopt()
        broken = {
            "glob in paths": MAP.replace(f'paths = ["{SRC}/pricing"]', f'paths = ["{SRC}/pri*"]'),
            "overlap": MAP.replace(f'paths = ["{SRC}/pricing"]', f'paths = ["{SRC}/pricing", "{SRC}/orders/_place.py"]'),
            "unknown field": MAP.replace('shape = "thin-facade"', 'shape = "thin-facade"\nowner = "x"', 1),
            "crust outside": MAP.replace(f'crust = ["{SRC}/pricing/__init__.py"]', f'crust = ["{SRC}/_cli.py"]'),
            "SB-00 suppression": MAP + '\n[[suppress]]\nrule = "SB-00"\nfrom = "a"\nto = "b"\nreason = "r"\n',
            "empty reason": MAP + '\n[[suppress]]\nrule = "SB-04"\nfrom = "a"\nto = "b"\nreason = ""\n',
            "requires outside may_depend_on": MAP.replace('requires = ["pricing"]', 'requires = ["app"]'),
        }
        for label, text in broken.items():
            with self.subTest(label):
                self.write("slices.toml", text)
                self.assertEqual(self.run_guard("check", "--base", "HEAD")[0], 2)


if __name__ == "__main__":
    _ = unittest.main()

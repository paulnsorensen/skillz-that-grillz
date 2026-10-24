#!/usr/bin/env python3
"""Tests for check_depth.py: the metric definitions, the ratchet modes, tighten, and the gaming cases."""
from __future__ import annotations

import ast
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast

from typing_extensions import override

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import check_depth as depth  # noqa: E402
import slice_map  # noqa: E402

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
may_depend_on = []

[[spine]]
name = "app"
paths = ["{SRC}/__init__.py", "{SRC}/_cli.py"]
may_depend_on = ["orders"]
"""
CRUST = ("from shop.orders._place import place, unused\n\n__all__ = ['place', 'total', 'unused']\n\n\n"
         "def total(items):\n    return sum(items)\n")
INTERNAL = ("def place(order, retry=1):\n    if order and retry:\n        order.save()\n        order.log()\n"
            "    return order\n\n\ndef unused():\n    return 0\n")
TREE = {
    "__init__.py": "",
    "_cli.py": "from shop.orders import place, total\n\nprint(place, total)\n",
    "orders/__init__.py": CRUST,
    "orders/_place.py": INTERNAL,
}


def _statements(source: str) -> int:
    return depth.logic_statements(ast.parse(source))


def _complexity(source: str) -> int:
    return depth.complexity(ast.parse(source))


class MetricTest(unittest.TestCase):
    def test_logic_statements_count_function_bodies_only(self) -> None:
        self.assertEqual(_statements("def f(x):\n    '''Doc.'''\n    if x:\n        a = 1\n        b = 2\n"), 3)
        self.assertEqual(_statements("X = [i for i in range(3)]\nif X:\n    Y = 1\n"), 0)
        self.assertEqual(_statements("F = lambda x: x + 1\n"), 1)
        self.assertEqual(_statements("def f():\n    def g():\n        return 1\n    return g\n"), 2)
        self.assertEqual(_statements("class C:\n    X = 1\n\n    def m(self):\n        return self.X\n"), 1)

    def test_reformatting_does_not_change_a_count(self) -> None:
        one = "def f(x):\n    return [y for y in x if y]\n"
        two = "def f(\n    x,\n):\n    return [\n        y\n        for y in x\n        if y\n    ]\n"
        self.assertEqual((_statements(one), _complexity(one)), (_statements(two), _complexity(two)))

    def test_complexity_counts_decisions_and_defaults_per_function(self) -> None:
        self.assertEqual(_complexity("def f():\n    return 1\n"), 1)
        self.assertEqual(_complexity("def f(a, b=1, *, c=2):\n    if a and b or c:\n        pass\n"), 6)
        self.assertEqual(_complexity("def f(x):\n    for i in x:\n        try:\n            pass\n"
                                     + "        except ValueError:\n            pass\n    return 1 if x else 2\n"), 4)
        self.assertEqual(_complexity("def f(x):\n    match x:\n        case 1 if x:\n            pass\n"
                                     + "        case _:\n            pass\n"), 3)
        self.assertEqual(_complexity("def f(x):\n    return [y for y in x if y]\n"), 3)
        self.assertEqual(_complexity("def f(x):\n    def g(y):\n        if y:\n            pass\n    return g\n"), 3)

    def test_exports_read_a_literal_all_or_the_public_names(self) -> None:
        self.assertEqual(depth.exports(ast.parse("from a import b, _c\n__all__ = ('b', 'b')\n")), ["b"])
        self.assertEqual(depth.exports(ast.parse("from a import b, _c\nX = 1\n_Y = 2\ndef f(): pass\n")),
                         ["X", "b", "f"])
        with self.assertRaises(slice_map.MapError):
            _ = depth.exports(ast.parse("__all__ = names()\n"))


def _git(root: Path, *args: str) -> None:
    _ = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                       capture_output=True)


class RatchetTest(unittest.TestCase):
    root: Path = Path()

    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.write("slices.toml", MAP)
        for name, text in TREE.items():
            self.write(f"{SRC}/{name}", text)

    def write(self, path: str, text: str) -> None:
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        _ = (self.root / path).write_text(text)

    def run_depth(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = depth.main(list(args), self.root)
        return code, out.getvalue()

    def values(self) -> dict[str, str]:
        code, out = self.run_depth("measure", "--json")
        self.assertEqual(code, 0, out)
        document = cast(dict[str, dict[str, dict[str, str]]], json.loads(out))
        return document["orders"]["values"]

    def baseline(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads((self.root / depth.BASELINE_FILE).read_text()))

    def caps(self) -> dict[str, object]:
        return cast(dict[str, dict[str, object]], self.baseline()["slices"])["orders"]

    def init(self, mode: str = "block") -> None:
        self.assertEqual(self.run_depth("init", "--mode", mode)[0], 0)

    def test_the_fixture_numbers(self) -> None:
        self.assertEqual(self.values(), {
            "M08.crust_logic_statements": "1", "M08.share": "1/6", "M09.public_surface": "3",
            "M10.crust_complexity": "1", "M10.share": "1/6", "M12.unused_exports": "1",
        })

    def test_m12_counts_each_use_form_through_the_crust(self) -> None:
        forms = {
            "from shop.orders import unused\n\nprint(unused)\n": "0",
            "from shop.orders import unused\n": "1",  # An import that the file never references is not a use.
            "from shop import orders\n\norders.unused()\n": "0",
            "import shop.orders\n\nshop.orders.unused()\n": "0",
            "import shop.orders as o\n\nprint(o)\n": "0",  # A bare module use uses every export.
            "from shop.orders import *\n": "0",
            "from shop.orders._place import unused\n\nprint(unused)\n": "1",  # A crust bypass is not a use.
            "from shop.orders import unused\n\n__all__ = ['unused']\n": "0",  # A re-export is a use.
        }
        for source, unused in forms.items():
            with self.subTest(source=source):
                self.write(f"{SRC}/__init__.py", source)
                self.assertEqual(self.values()["M12.unused_exports"], unused)

    def test_crust_growth_blocks_in_block_mode_and_reports_in_report_mode(self) -> None:
        self.init("block")
        self.assertEqual(self.run_depth("check")[0], 0)
        self.write(f"{SRC}/orders/__init__.py", CRUST + "    print(items)\n")
        code, out = self.run_depth("check")
        self.assertEqual(code, 1, out)
        self.assertIn("regressed orders M08.crust_logic_statements 1 -> 2", out)
        self.assertIn("regressed orders M08.share 1/6 -> 2/7", out)
        (self.root / depth.BASELINE_FILE).unlink()
        self.write(f"{SRC}/orders/__init__.py", CRUST)
        self.init("report")
        self.write(f"{SRC}/orders/__init__.py", CRUST + "    print(items)\n")
        code, out = self.run_depth("check")
        self.assertEqual(code, 0, out)
        self.assertIn("regressed orders M08.crust_logic_statements", out)

    def test_internal_padding_cannot_hide_crust_growth(self) -> None:
        self.init()
        padding = "".join(f"\n\ndef pad{n}():\n    return {n}\n" for n in range(20))
        self.write(f"{SRC}/orders/_place.py", INTERNAL + padding)
        self.write(f"{SRC}/orders/__init__.py", CRUST + "    print(items)\n")
        code, out = self.run_depth("check")
        self.assertEqual(code, 1)
        self.assertIn("regressed orders M08.crust_logic_statements 1 -> 2", out)
        self.assertNotIn("regressed orders M08.share", out)

    def test_new_exports_and_unused_exports_regress(self) -> None:
        self.init()
        self.write(f"{SRC}/orders/__init__.py", CRUST.replace("'unused']", "'unused', 'extra']\nextra = 1"))
        code, out = self.run_depth("check")
        self.assertEqual(code, 1)
        self.assertIn("regressed orders M09.public_surface 3 -> 4", out)
        self.assertIn("regressed orders M12.unused_exports 1 -> 2", out)

    def test_an_internal_simplification_reports_denominator_shrink(self) -> None:
        self.init()
        self.write(f"{SRC}/orders/_place.py", "def place(order):\n    return order\n\n\ndef unused():\n    return 0\n")
        code, out = self.run_depth("check")
        self.assertEqual(code, 1)
        self.assertIn("regressed orders M08.share 1/6 -> 1/3 denominator-shrink", out)

    def test_tighten_lowers_improved_caps_and_never_raises_one(self) -> None:
        self.init()
        self.write(f"{SRC}/orders/__init__.py", CRUST.replace(", 'unused']", "]").replace(", unused", ""))
        code, out = self.run_depth("check")
        self.assertEqual(code, 0, out)
        self.assertIn("improved orders M09.public_surface 3 -> 2", out)
        self.assertEqual(self.run_depth("tighten")[0], 0)
        self.assertEqual((self.caps()["M09.public_surface"], self.caps()["M12.unused_exports"]), (2, 0))
        self.write(f"{SRC}/orders/__init__.py", CRUST)
        code, out = self.run_depth("tighten")
        self.assertEqual(code, 1)
        self.assertIn("tighten refuses", out)
        self.assertEqual(self.caps()["M09.public_surface"], 2)

    def test_tighten_holds_a_share_gain_from_internal_growth(self) -> None:
        self.init()
        self.write(f"{SRC}/orders/_place.py", INTERNAL + "\n\ndef more():\n    return place(None)\n")
        code, out = self.run_depth("tighten")
        self.assertEqual(code, 0, out)
        self.assertIn("held orders M08.share 1/6 -> 1/7", out)
        self.assertEqual(self.caps()["M08.share"], "1/6")
        self.assertEqual(self.run_depth("tighten", "--accept-denominator-gains")[0], 0)
        self.assertEqual(self.caps()["M08.share"], "1/7")

    def test_new_and_orphaned_slices(self) -> None:
        self.init()
        renamed = MAP.replace('name = "orders"', 'name = "sales"').replace('["orders"]', '["sales"]')
        self.write("slices.toml", renamed)
        code, out = self.run_depth("check")
        self.assertEqual(code, 1)
        self.assertIn("new sales: no baseline", out)
        self.assertIn("orphan orders", out)
        self.assertEqual(self.run_depth("tighten", "--adopt-new")[0], 1)  # An orphan blocks tighten.

    def test_adopt_new_adds_a_baseline_for_a_new_slice(self) -> None:
        self.write("slices.toml", MAP.replace("[[slice]]", "[[slice]]\nname = \"extra\"\npaths = [\""
                                              + f"{SRC}/extra\"]\ncrust = [\"{SRC}/extra/__init__.py\"]\n"
                                              + "shape = \"thin-facade\"\nmay_depend_on = []\n\n[[slice]]", 1))
        self.write(f"{SRC}/extra/__init__.py", "")
        self.init()
        document = self.baseline()
        del cast(dict[str, object], document["slices"])["extra"]
        _ = (self.root / depth.BASELINE_FILE).write_text(json.dumps(document))
        self.assertIn("new extra: no baseline", self.run_depth("check")[1])
        self.assertEqual(self.run_depth("tighten", "--adopt-new")[0], 0)
        self.assertEqual(self.run_depth("check")[1].strip(), "depth ratchet: pass (2 slices, mode block)")

    def test_init_refuses_to_overwrite_and_an_old_measurement_is_an_input_error(self) -> None:
        self.init()
        self.assertEqual(self.run_depth("init")[0], 1)
        text = (self.root / depth.BASELINE_FILE).read_text()
        _ = (self.root / depth.BASELINE_FILE).write_text(text.replace('"measurement_version": 1', '"measurement_version": 0'))
        self.assertEqual(self.run_depth("check")[0], 2)

    def test_diff_baseline_lists_loosening(self) -> None:
        self.init()
        _git(self.root, "init", "-q")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")
        document = self.baseline()
        document["mode"] = "report"
        slices = cast(dict[str, dict[str, object]], document["slices"])
        slices["orders"]["M09.public_surface"] = 9
        _ = (self.root / depth.BASELINE_FILE).write_text(json.dumps(document))
        code, out = self.run_depth("diff-baseline", "--base", "HEAD")
        self.assertEqual(code, 0)
        self.assertIn("loosened mode block -> report", out)
        self.assertIn("loosened raised orders M09.public_surface 3 -> 9", out)


if __name__ == "__main__":
    _ = unittest.main()

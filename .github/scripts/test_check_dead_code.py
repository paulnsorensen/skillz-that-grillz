"""Behavior tests for the repository Vulture gate."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCANNER = Path(__file__).with_name("check_dead_code.py").resolve()


def _scan(source: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sample.py"
        _ = path.write_text(source)
        return subprocess.run(
            [sys.executable, str(SCANNER), str(path)],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )


class DeadCodeTest(unittest.TestCase):
    def test_typed_dict_field_is_live(self) -> None:
        result = _scan("from typing import TypedDict\nclass Data(TypedDict):\n    value: int\nData\n")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_subclass_of_local_typed_dict_is_live(self) -> None:
        result = _scan(
            "from typing import TypedDict\n" +
            "class Base(TypedDict):\n    base: int\n" +
            "class Child(Base):\n    child: int\n" +
            "Child\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_subclass_of_ordinary_class_is_reported(self) -> None:
        result = _scan(
            "from typing import TypedDict\n" +
            "class Base(TypedDict):\n    base: int\n" +
            "class Plain: pass\n" +
            "class Child(Plain):\n    stale: int\n" +
            "Base; Child\n"
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("unused variable 'stale'", result.stdout)

    def test_try_except_import_fallback_is_live(self) -> None:
        result = _scan(
            "try:\n    from typing import TypedDict\n" +
            "except ImportError:\n    from typing_extensions import TypedDict\n" +
            "class Data(TypedDict):\n    value: int\n" +
            "Data\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_type_checking_import_is_live(self) -> None:
        result = _scan(
            "from typing import TYPE_CHECKING\n" +
            "if TYPE_CHECKING:\n    from typing_extensions import TypedDict\n" +
            "class Data(TypedDict):\n    value: int\n" +
            "Data\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_same_named_ordinary_field_is_reported(self) -> None:
        result = _scan(
            "from typing import TypedDict\n" +
            "class Data(TypedDict):\n    stale: int\n" +
            "class Regular:\n    stale: int\n" +
            "Data; Regular\n"
        )
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout.count("unused variable 'stale'"), 1)

    def test_typing_import_aliases_are_resolved(self) -> None:
        result = _scan(
            "import typing as ty\n" +
            "from typing import TypedDict as TD\n" +
            "class First(TD):\n    first: int\n" +
            "class Second(ty.TypedDict):\n    second: str\n" +
            "First; Second\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_local_impostor_is_not_exempt(self) -> None:
        result = _scan("class TypedDict: pass\nclass Fake(TypedDict):\n    stale: int\nFake\n")
        self.assertEqual(result.returncode, 3)
        self.assertIn("unused variable 'stale'", result.stdout)

    def test_shadowed_import_is_not_exempt(self) -> None:
        result = _scan(
            "from typing import TypedDict as TD\n" +
            "TD = object\n" +
            "class Fake(TD):\n    stale: int\n" +
            "Fake\n"
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("unused variable 'stale'", result.stdout)

    def test_conditional_shadow_is_not_exempt(self) -> None:
        result = _scan(
            "from typing import TypedDict as TD\n" +
            "if True:\n    TD = object\n" +
            "class Fake(TD):\n    stale: int\n" +
            "Fake\n"
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("unused variable 'stale'", result.stdout)

    def test_module_attribute_rebinding_is_not_exempt(self) -> None:
        result = _scan(
            "import typing as ty\n" +
            "ty.TypedDict = object\n" +
            "class Fake(ty.TypedDict):\n    stale: int\n" +
            "Fake\n"
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("unused variable 'stale'", result.stdout)

    def test_branch_specific_alias_is_not_exempt(self) -> None:
        for source in (
            "import sys\nif sys.argv:\n    TD = object\nelse:\n    from typing import TypedDict as TD\n",
            "try:\n    TD = object\nexcept ImportError:\n    from typing import TypedDict as TD\n",
        ):
            with self.subTest(source=source):
                result = _scan(source + "class Fake(TD):\n    stale: int\nFake\n")
                self.assertEqual(result.returncode, 3)
                self.assertIn("unused variable 'stale'", result.stdout)

    def test_function_local_rebinding_keeps_module_alias(self) -> None:
        result = _scan(
            "from typing import TypedDict as TD\n" +
            "def helper() -> object:\n    TD = object\n    return TD\n" +
            "class Data(TD):\n    value: int\n" +
            "helper; Data\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_global_rebinding_in_function_is_not_exempt(self) -> None:
        result = _scan(
            "from typing import TypedDict as TD\n" +
            "def helper() -> None:\n    global TD\n    TD = object\n" +
            "class Fake(TD):\n    stale: int\n" +
            "helper; Fake\n"
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("unused variable 'stale'", result.stdout)

    def test_real_dead_code_fails(self) -> None:
        result = _scan("def unused() -> None: pass\n")
        self.assertEqual(result.returncode, 3)
        self.assertIn("unused function 'unused'", result.stdout)

    def test_syntax_error_fails_as_invalid_input(self) -> None:
        result = _scan("def broken(:\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid syntax", result.stderr)

    def test_missing_path_fails_as_invalid_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(SCANNER), str(Path(directory) / "missing.py")],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not be found", result.stderr)

    def test_invalid_option_fails_as_bad_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(SCANNER), "--not-an-option"],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)


if __name__ == "__main__":
    _ = unittest.main()

#!/usr/bin/env python3
"""Behavior tests for check_ac_trace.py, driven as a subprocess on temporary repositories."""
from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check_ac_trace.py"
REPO = SCRIPT.parents[2]
TABLE_HEAD = "# Demo\n\nSource: a test fixture\n\n| id | kind | criterion | verified by |\n| --- | --- | --- | --- |\n"
MARKED = 'import pytest\n\n\n@pytest.mark.ac("demo:AC-1")\ndef test_one() -> None:\n    pass\n'


def _row(identifier: str = "AC-1", kind: str = "test", verified_by: str = "") -> str:
    return f"| {identifier} | {kind} | does a thing | {verified_by} |\n"


def _check(root: Path) -> tuple[int, str]:
    done = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, check=False)
    return done.returncode, done.stdout + done.stderr


def _build(root: Path, criteria: dict[str, str] | None = None, tests: dict[str, str] | None = None) -> Path:
    for name, text in (criteria or {}).items():
        path = root / "docs/agents/criteria" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text)
    for relative, text in (tests or {"packages/wedge/tests/test_demo.py": MARKED}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text)
    return root


class TraceGateTest(unittest.TestCase):
    def _expect(self, criteria: dict[str, str], tests: dict[str, str] | None, fragment: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, output = _check(_build(Path(directory), criteria, tests))
        self.assertEqual(code, 1, output)
        self.assertIn(fragment, output)

    def _expect_clean(self, criteria: dict[str, str], tests: dict[str, str] | None = None) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, output = _check(_build(Path(directory), criteria, tests))
        self.assertEqual((code, output), (0, ""))

    def test_a_traced_test_criterion_passes(self) -> None:
        self._expect_clean({"demo": TABLE_HEAD + _row()})

    @pytest.mark.ac("gh-267:AC-3")
    def test_a_test_criterion_without_a_marked_test_fails_and_is_named(self) -> None:
        self._expect({"demo": TABLE_HEAD + _row() + _row("AC-2")}, None, "demo:AC-2: test criterion has no marked test")

    @pytest.mark.ac("gh-267:AC-4")
    def test_a_marker_for_an_unknown_source_names_its_file_and_line(self) -> None:
        self._expect(
            {"demo": TABLE_HEAD + _row()},
            {"packages/fromargs/tests/test_x.py": MARKED.replace("demo:", "other:")},
            "packages/fromargs/tests/test_x.py:4: marker other:AC-1 names unknown source 'other'",
        )

    @pytest.mark.ac("gh-267:AC-4")
    def test_a_marker_for_an_unknown_id_names_its_file_and_line(self) -> None:
        self._expect(
            {"demo": TABLE_HEAD + _row()},
            {"packages/skillz/tests/test_x.py": MARKED.replace("AC-1", "AC-9")},
            "packages/skillz/tests/test_x.py:4: marker demo:AC-9 names unknown id 'AC-9'",
        )

    @pytest.mark.ac("gh-267:AC-5")
    def test_a_process_row_needs_verified_by(self) -> None:
        self._expect({"demo": TABLE_HEAD + _row("AC-1") + _row("AC-2", "process")}, None, "demo:AC-2: process criterion needs a non-empty verified by")

    @pytest.mark.ac("gh-267:AC-5")
    def test_a_kind_other_than_test_or_process_fails(self) -> None:
        self._expect({"demo": TABLE_HEAD + _row() + _row("AC-2", "manual", "me")}, None, "demo:AC-2: kind 'manual' must be test or process")

    @pytest.mark.ac("gh-267:AC-5")
    def test_a_process_row_with_evidence_is_exempt_from_a_marker(self) -> None:
        self._expect_clean({"demo": TABLE_HEAD + _row() + _row("AC-2", "process", "pre-gate: PR #1 review")})

    @pytest.mark.ac("gh-267:AC-9")
    def test_a_malformed_marker_argument_names_its_file_and_line(self) -> None:
        cases = {
            "non-literal": ('ID = "demo:AC-1"\n\n\n@pytest.mark.ac(ID)\ndef test_a() -> None:\n    pass\n', "exactly one string literal"),
            "bare id": ('@pytest.mark.ac("AC-1")\ndef test_a() -> None:\n    pass\n', "'AC-1' must match <source>:<id>"),
            "upper-case source": ('@pytest.mark.ac("Demo:AC-1")\ndef test_a() -> None:\n    pass\n', "must match <source>:<id>"),
            "two arguments": ('@pytest.mark.ac("demo:AC-1", "demo:AC-2")\ndef test_a() -> None:\n    pass\n', "exactly one string literal"),
            "no call": ("@pytest.mark.ac\ndef test_a() -> None:\n    pass\n", "exactly one string literal"),
        }
        for label, (body, fragment) in cases.items():
            with self.subTest(label):
                self._expect({"demo": TABLE_HEAD + _row()}, {"packages/wedge/tests/test_bad.py": "import pytest\n\n\n" + body}, fragment)

    @pytest.mark.ac("gh-267:AC-9")
    def test_a_duplicate_id_names_its_file_and_line(self) -> None:
        self._expect({"demo": TABLE_HEAD + _row() + _row()}, None, "docs/agents/criteria/demo.md:8: duplicate id AC-1")

    @pytest.mark.ac("gh-267:AC-9")
    def test_a_malformed_criteria_file_names_its_file(self) -> None:
        cases = {
            "no table": ("# Demo\n\nSource: x\n", "demo.md:1: missing the criteria table"),
            "wrong header": (TABLE_HEAD.replace("verified by", "proof") + _row(), "demo.md:5: table header must be"),
            "no separator": (TABLE_HEAD.replace("| --- | --- | --- | --- |\n", "") + _row(), "demo.md:6: table header needs a separator row"),
            "short row": (TABLE_HEAD + "| AC-1 | test | text |\n", "demo.md:7: row needs 4 cells, found 3"),
            "bad id": (TABLE_HEAD + _row("one"), "demo.md:7: row needs an id such as AC-1"),
            "no rows": (TABLE_HEAD, "demo.md:5: the criteria table has no rows"),
            "no source line": (TABLE_HEAD.replace("Source: a test fixture\n", "") + _row(), "demo.md:1: missing a non-empty Source: line"),
            "no H1": (TABLE_HEAD.replace("# Demo", "Demo") + _row(), "demo.md:1: the first non-blank line must be an H1"),
        }
        for label, (text, fragment) in cases.items():
            with self.subTest(label):
                self._expect({"demo": text}, None, fragment)

    @pytest.mark.ac("gh-267:AC-10")
    def test_every_marker_form_counts_as_a_marked_test(self) -> None:
        forms = {
            "decorator": MARKED,
            "module pytestmark": 'import pytest\n\npytestmark = pytest.mark.ac("demo:AC-1")\n',
            "module pytestmark list": 'import pytest\n\npytestmark = [pytest.mark.ac("demo:AC-1")]\n',
            "class pytestmark": 'import pytest\n\n\nclass TestA:\n    pytestmark = pytest.mark.ac("demo:AC-1")\n',
            "pytest.param marks": 'import pytest\n\nCASES = [pytest.param(1, marks=pytest.mark.ac("demo:AC-1"))]\n',
            "class decorator": 'import pytest\n\n\n@pytest.mark.ac("demo:AC-1")\nclass TestA:\n    pass\n',
        }
        for label, body in forms.items():
            with self.subTest(label):
                self._expect_clean({"demo": TABLE_HEAD + _row()}, {"packages/skillz/tests/test_form.py": body})

    @pytest.mark.ac("gh-267:AC-10")
    def test_a_docstring_or_comment_mention_is_not_a_marker(self) -> None:
        body = '"""Mentions @pytest.mark.ac("demo:AC-1")."""\n# @pytest.mark.ac("demo:AC-1")\n'
        self._expect({"demo": TABLE_HEAD + _row()}, {"packages/skillz/tests/test_doc.py": body}, "demo:AC-1: test criterion has no marked test")

    @pytest.mark.ac("gh-267:AC-6")
    def test_a_path_that_resolves_outside_the_repository_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere)
            _ = (outside / "outer.md").write_text(TABLE_HEAD + _row())
            _ = (outside / "test_outer.py").write_text(MARKED)
            root = _build(Path(directory), {}, {"packages/wedge/tests/test_demo.py": ""})
            (root / "docs/agents/criteria").mkdir(parents=True)
            (root / "docs/agents/criteria/outer.md").symlink_to(outside / "outer.md")
            (root / "packages/skillz/tests").mkdir(parents=True)
            (root / "packages/skillz/tests/test_outer.py").symlink_to(outside / "test_outer.py")
            code, output = _check(root)
        self.assertEqual(code, 1, output)
        self.assertIn("docs/agents/criteria/outer.md:1: path resolves outside the repository", output)
        self.assertIn("packages/skillz/tests/test_outer.py:1: path resolves outside the repository", output)

    @pytest.mark.ac("gh-267:AC-6")
    def test_the_script_imports_only_the_standard_library(self) -> None:
        tree = ast.parse(SCRIPT.read_text())
        roots = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
        }
        self.assertTrue(roots)
        self.assertEqual(roots - set(sys.stdlib_module_names), set())

    @pytest.mark.ac("gh-267:AC-2")
    def test_the_repository_is_fully_traced(self) -> None:
        code, output = _check(REPO)
        self.assertEqual((code, output), (0, ""))

    @pytest.mark.ac("gh-267:AC-2")
    def test_a_bare_marker_in_the_repository_shape_is_reported(self) -> None:
        self._expect({"demo": TABLE_HEAD + _row()}, {"packages/fromargs/tests/e2e/test_bare.py": MARKED.replace("demo:", "")}, "packages/fromargs/tests/e2e/test_bare.py:4:")


if __name__ == "__main__":
    _ = unittest.main()

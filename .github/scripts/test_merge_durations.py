#!/usr/bin/env python3
"""Tests for merge_durations.py: the per-shard durations union that the CI coverage job uploads."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))

import merge_durations  # noqa: E402


def _run(*argv: str) -> tuple[int, str]:
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        code = merge_durations.main(list(argv))
    return code, output.getvalue()


class MergeDurationsTest(unittest.TestCase):
    def test_the_union_of_every_shard_file_is_written_with_sorted_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shards = root / "shards"
            shards.mkdir()
            _ = (shards / "skillz-pytest.2of6.json").write_text(json.dumps({"tests/b.py::t": 2.5, "tests/a.py::t": 1}))
            _ = (shards / "skillz-e2e-pytest.1of6.json").write_text(json.dumps({"tests/e2e/c.py::t": 0.25}))
            _ = (shards / "notes.txt").write_text("not a durations file")
            out = root / "merged" / ".test_durations"
            code, output = _run(str(shards), str(out))
            self.assertEqual(code, 0, output)
            text = out.read_text()
            self.assertEqual(list(cast(dict[str, float], json.loads(text)).items()),
                             [("tests/a.py::t", 1.0), ("tests/b.py::t", 2.5), ("tests/e2e/c.py::t", 0.25)])
            self.assertIn("merged 3 test durations", output)
            # A second merge of the same input writes the same bytes.
            again = root / "again.json"
            self.assertEqual(_run(str(shards), str(again))[0], 0)
            self.assertEqual(again.read_text(), text)

    def test_a_test_in_two_shards_fails_and_names_both_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _ = (root / "skillz-pytest.1of6.json").write_text(json.dumps({"tests/a.py::t": 1.0}))
            _ = (root / "skillz-pytest.2of6.json").write_text(json.dumps({"tests/a.py::t": 1.0}))
            code, output = _run(str(root), str(root / "out" / "merged.json"))
            self.assertEqual(code, 1)
            self.assertIn("tests/a.py::t is in both skillz-pytest.1of6.json and skillz-pytest.2of6.json", output)
            self.assertFalse((root / "out").exists())

    def test_missing_or_malformed_input_fails_without_output(self) -> None:
        cases = {"empty": {}, "list": {"a.json": "[1, 2]"}, "text value": {"a.json": '{"t": "slow"}'},
                 "bool value": {"a.json": '{"t": true}'}, "bad json": {"a.json": "{"}}
        for name, files in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for file, text in files.items():
                    _ = (root / file).write_text(text)
                out = root / "merged.json"
                code, output = _run(str(root), str(out))
                self.assertEqual(code, 1, output)
                self.assertIn("merge_durations: ", output)
                self.assertFalse(out.exists())

    def test_wrong_argument_count_is_a_usage_error(self) -> None:
        code, output = _run("only-one")
        self.assertEqual(code, 2)
        self.assertIn("usage: merge_durations.py <dir> <out>", output)


if __name__ == "__main__":
    _ = unittest.main()

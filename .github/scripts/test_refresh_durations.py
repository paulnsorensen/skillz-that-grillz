#!/usr/bin/env python3
"""Tests for refresh_durations.py: the no-change path, the changed path, and bad input that writes nothing."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from collections.abc import Generator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import refresh_durations as refresh  # noqa: E402

SLOW = "tests/test_slow.py"
FAST = "tests/test_fast.py"
BUDGET = "[tool.skillz.test-time]\nmax_seconds = 20\n\n[tool.skillz.test-time.exceptions]\n"


def _waiver(ceiling: int) -> str:
    return f'"{SLOW}" = {{ max_seconds = {ceiling}, reason = "Measured." }}\n'


def _durations(slow: float, fast: float = 2.0) -> dict[str, float]:
    return {f"{SLOW}::test_a": slow / 2, f"{SLOW}::test_b": slow / 2, f"{FAST}::test_c": fast}


@contextmanager
def _tree(committed: dict[str, float], waiver: str) -> Generator[Path]:
    """Yield a repo root with a budget and a committed durations file."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _ = (root / "pyproject.toml").write_text(BUDGET + waiver)
        target = root / refresh.DURATIONS
        target.parent.mkdir(parents=True)
        _ = target.write_text(json.dumps(committed, indent=4, sort_keys=True) + "\n")
        yield root


def _artifact(root: Path, content: str) -> Path:
    path = root / "artifact" / ".test_durations"
    path.parent.mkdir(exist_ok=True)
    _ = path.write_text(content)
    return path


def _run(root: Path, *args: str | Path) -> tuple[int, str]:
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        code = refresh.main([str(arg) for arg in args], root=root)
    return code, output.getvalue()


def _snapshot(root: Path) -> tuple[str, str]:
    return (root / "pyproject.toml").read_text(), (root / refresh.DURATIONS).read_text()


class RefreshTest(unittest.TestCase):
    def test_equal_durations_and_tight_waivers_are_no_change(self) -> None:
        # 30 s needs a 45 s ceiling, the committed ceiling; the artifact differs only in its format.
        with _tree(_durations(30), _waiver(45)) as root:
            before = _snapshot(root)
            artifact = _artifact(root, json.dumps(_durations(30)))
            output = root / "github-output"
            self.assertEqual(_run(root, artifact, "--github-output", output), (0, "no change\n"))
            self.assertEqual(_snapshot(root), before)
            self.assertEqual(output.read_text(), "changed=false\n")

    def test_new_durations_replace_the_file_and_tighten_the_improved_ceiling(self) -> None:
        with _tree(_durations(30), _waiver(45)) as root:
            content = json.dumps(_durations(20.5), indent=4, sort_keys=True) + "\n"
            artifact = _artifact(root, content)
            output = root / "github-output"
            self.assertEqual(_run(root, artifact, "--github-output", output),
                             (0, "changed: packages/skillz/.test_durations, pyproject.toml\n"))
            pyproject, durations = _snapshot(root)
            self.assertEqual(durations, content)
            # 20.5 s x 1.5 = 30.75 s, rounded up to 31 s.
            self.assertEqual(pyproject, BUDGET + _waiver(31))
            self.assertEqual(output.read_text(), "changed=true\n")

    def test_a_slower_run_replaces_the_file_but_never_raises_a_ceiling(self) -> None:
        with _tree(_durations(30), _waiver(45)) as root:
            artifact = _artifact(root, json.dumps(_durations(40)))
            self.assertEqual(_run(root, artifact), (0, "changed: packages/skillz/.test_durations\n"))
            self.assertEqual(_snapshot(root)[0], BUDGET + _waiver(45))

    def test_a_loose_ceiling_tightens_when_the_durations_are_equal(self) -> None:
        with _tree(_durations(30), _waiver(60)) as root:
            artifact = _artifact(root, json.dumps(_durations(30)))
            self.assertEqual(_run(root, artifact), (0, "changed: pyproject.toml\n"))
            self.assertEqual(_snapshot(root)[0], BUDGET + _waiver(45))

    def test_a_file_that_fits_the_default_loses_its_waiver(self) -> None:
        with _tree(_durations(30), _waiver(45)) as root:
            artifact = _artifact(root, json.dumps(_durations(10)))
            self.assertEqual(_run(root, artifact)[0], 0)
            self.assertEqual(_snapshot(root)[0], BUDGET)

    def test_a_missing_or_malformed_committed_file_is_replaced(self) -> None:
        for committed in ("", "{}", "not json"):
            with self.subTest(committed=committed), _tree({}, _waiver(45)) as root:
                _ = (root / refresh.DURATIONS).write_text(committed)
                content = json.dumps(_durations(30))
                self.assertEqual(_run(root, _artifact(root, content)),
                                 (0, "changed: packages/skillz/.test_durations\n"))
                self.assertEqual(_snapshot(root)[1], content)

    def test_a_bad_artifact_fails_with_a_code_and_writes_nothing(self) -> None:
        for content, code in ((None, "TIME-MISSING"), ("{}", "TIME-EMPTY"), ("[]", "TIME-INVALID"),
                              ('{"t.py::a": -1}', "TIME-INVALID")):
            with self.subTest(code=code, content=content), _tree(_durations(30), _waiver(60)) as root:
                before = _snapshot(root)
                artifact = root / "absent" if content is None else _artifact(root, content)
                output = root / "github-output"
                status, text = _run(root, artifact, "--github-output", output)
                self.assertEqual(status, 1)
                self.assertIn(f"[{code}]", text)
                self.assertEqual(_snapshot(root), before)
                self.assertFalse(output.exists())

    def test_a_waiver_that_tighten_cannot_edit_is_an_input_error_and_writes_nothing(self) -> None:
        # Valid TOML, but not the one-line inline table that `tighten` edits.
        split = f'\n[tool.skillz.test-time.exceptions."{SLOW}"]\nmax_seconds = 60\nreason = "Measured."\n'
        with _tree(_durations(30), split) as root:
            before = _snapshot(root)
            status, text = _run(root, _artifact(root, json.dumps(_durations(20))))
            self.assertEqual(status, 2)
            self.assertIn("tighten needs the exception on one line", text)
            self.assertEqual(_snapshot(root), before)

    def test_a_bad_command_line_is_a_usage_error(self) -> None:
        with _tree(_durations(30), _waiver(45)) as root:
            for args in ((), ("a", "b"), ("a", "--output", "c"), ("a", "--github-output")):
                with self.subTest(args=args):
                    self.assertEqual(_run(root, *args), (2, refresh.USAGE + "\n"))


if __name__ == "__main__":
    unittest.main()

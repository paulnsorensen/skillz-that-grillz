from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._evals_format import parse_evals
from skillz_experiments._seed import seed_cases

REPO_ROOT = Path(__file__).resolve().parents[4]


def _validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_evals_under_test", REPO_ROOT / ".github/scripts/validate_evals.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entry(**extra: object) -> dict[str, object]:
    return {"id": 1, "name": "a", "kind": "task", "prompt": "p", "expected_output": "e", "files": [], **extra}


def _files(**fixtures: str) -> Callable[[Path], None]:
    def make(skill: Path) -> None:
        for name, text in fixtures.items():
            (skill / "evals/files").mkdir(parents=True, exist_ok=True)
            _ = (skill / "evals/files" / name).write_text(text, encoding="utf-8")
    return make


def _symlink(skill: Path) -> None:
    _files(**{"real.md": "f\n"})(skill)
    (skill / "evals/files/link.md").symlink_to("real.md")


def _no_setup(skill: Path) -> None:
    del skill


DOCUMENTS: dict[str, tuple[list[dict[str, object]], bool, Callable[[Path], None]]] = {
    "valid": ([_entry()], True, _no_setup),
    "bogus-kind": ([_entry(kind="bogus")], False, _no_setup),
    "duplicate-name": ([_entry(id=1), _entry(id=2)], False, _no_setup),
    "contradiction": ([_entry(kind="trigger", assertions=["x"])], False, _no_setup),
    "fixture": ([_entry(files=["evals/files/ok.md"])], True, _files(**{"ok.md": "f\n"})),
    "fixture-missing": ([_entry(files=["evals/files/ok.md"])], False, _no_setup),
    "fixture-empty": ([_entry(files=["evals/files/ok.md"])], False, _files(**{"ok.md": ""})),
    "fixture-symlink": ([_entry(files=["evals/files/link.md"])], False, _symlink),
    "fixture-symlink-in-unclean-entry": ([_entry(files=["evals/files/link.md"], kind="bogus")], False, _symlink),
}


@pytest.mark.ac("gh-366:AC-6")
@pytest.mark.parametrize("label", sorted(DOCUMENTS))
def test_ci_validator_and_seed_parser_give_the_same_verdict(tmp_path: Path, label: str) -> None:
    entries, accepted, setup = DOCUMENTS[label]
    skill = tmp_path / "skills/x"
    (skill / "evals").mkdir(parents=True)
    setup(skill)
    path = skill / "evals/evals.json"
    _ = path.write_text(json.dumps({"skill_name": "x", "evals": entries}), encoding="utf-8")
    validate = cast(Callable[[Path], list[str]], getattr(_validator(), "validate_file"))
    errors = validate(path)
    try:
        _ = seed_cases(skill)
        seed_accepts = True
    except CodedError as error:
        assert error.code == "evals-unreadable"
        seed_accepts = False
    assert (errors == []) == seed_accepts == accepted
    if label.startswith("fixture-symlink"):
        assert any("link.md" in error for error in errors)


def _lenient(*entries: dict[str, object]) -> tuple[list[str], list[str]]:
    parsed = parse_evals({"evals": list(entries)}, strict=False)
    return list(parsed.errors), [entry.identifier for entry in parsed.entries]


@pytest.mark.ac("gh-366:AC-6")
def test_lenient_reader_rejects_a_name_that_equals_a_resolved_id() -> None:
    errors, _ = _lenient({"name": "eval-1", "prompt": "p", "expected_output": "e"},
                         {"id": 1, "prompt": "p", "expected_output": "e"})
    assert errors == ["evals[1]: duplicate case id 'eval-1'"]


@pytest.mark.ac("gh-366:AC-4")
def test_lenient_reader_ignores_an_invalid_id_when_the_entry_has_a_name() -> None:
    named = {"name": "a", "prompt": "p", "expected_output": "e"}
    assert _lenient({**named, "id": "bad id!"}, {**named, "name": "b", "id": "bad id!"}) == ([], ["a", "b"])
    errors, _ = _lenient({"id": "bad id!", "prompt": "p", "expected_output": "e"})
    assert errors == ["evals[0]: id must be an int or short text of letters, digits, '.', '_', '-'"]

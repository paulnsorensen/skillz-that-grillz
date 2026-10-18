from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from skillz_experiments._cases import CodedError
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


DOCUMENTS: dict[str, tuple[list[dict[str, object]], bool]] = {
    "valid": ([_entry()], True),
    "bogus-kind": ([_entry(kind="bogus")], False),
    "duplicate-resolved-id": ([_entry(id=1), _entry(id=2)], False),
    "contradiction": ([_entry(kind="trigger", assertions=["x"])], False),
}


@pytest.mark.ac("gh-366:AC-6")
@pytest.mark.parametrize("label", sorted(DOCUMENTS))
def test_ci_validator_and_seed_parser_give_the_same_verdict(tmp_path: Path, label: str) -> None:
    entries, accepted = DOCUMENTS[label]
    skill = tmp_path / "skills/x"
    (skill / "evals").mkdir(parents=True)
    path = skill / "evals/evals.json"
    _ = path.write_text(json.dumps({"skill_name": "x", "evals": entries}), encoding="utf-8")
    validate = cast(Callable[[Path], list[str]], getattr(_validator(), "validate_file"))
    ci_accepts = validate(path) == []
    try:
        _ = seed_cases(skill)
        seed_accepts = True
    except CodedError as error:
        assert error.code == "evals-unreadable"
        seed_accepts = False
    assert ci_accepts == seed_accepts == accepted

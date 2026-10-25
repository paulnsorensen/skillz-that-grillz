"""The run `Session` is the one importer of its phase modules; no other run module imports `_session`."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import skillz_experiments

RUN = Path(skillz_experiments.__file__).parent / "run"
SESSION = "skillz_experiments.run._session"


def _imports_of_session(path: Path) -> list[str]:
    """Name what `path` imports from the session module, `*` when it imports the module itself."""
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module == SESSION:
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module == "skillz_experiments.run":
            names.extend("*" for alias in node.names if alias.name == "_session")
        elif isinstance(node, ast.Import):
            names.extend("*" for alias in node.names if alias.name == SESSION)
    return names


@pytest.mark.ac("gh-400:AC-1")
def test_only_the_run_entry_point_imports_the_session_and_it_imports_only_the_class() -> None:
    importers = {path.name: _imports_of_session(path) for path in RUN.glob("*.py") if path.name != "_session.py"}
    assert {name: found for name, found in importers.items() if found} == {"_run.py": ["Session"]}


@pytest.mark.ac("gh-400:AC-1")
def test_the_session_class_lives_in_the_session_module() -> None:
    tree = ast.parse((RUN / "_session.py").read_text())
    assert any(isinstance(node, ast.ClassDef) and node.name == "Session" for node in tree.body)

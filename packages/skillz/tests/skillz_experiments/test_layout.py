"""Module layout rules for `skillz_experiments`: slices expose a facade and keep the dependency direction."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import skillz_experiments

PACKAGE = Path(skillz_experiments.__file__).parent
PREFIX = "skillz_experiments"
SLICES = ("run", "search", "gate", "harness", "wrapper")
LEAF_SLICES = ("search", "gate")
FORBIDDEN_FROM_LEAVES = ("run", "harness")
REMOVED_FLAT_FILES = ("_workflow.py", "_search.py", "_gate.py", "_harness.py", "_claude.py")


def _modules() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def _slice_of(path: Path) -> str | None:
    parts = path.relative_to(PACKAGE).parts
    return parts[0] if len(parts) > 1 else None


def _imported(path: Path) -> set[str]:
    """Return each absolute dotted module path that `path` imports, including `from package import module` forms."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path}: use absolute imports"
            base = node.module or ""
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def _slice_modules(imported: set[str], slice_name: str) -> set[str]:
    root = f"{PREFIX}.{slice_name}"
    return {name for name in imported if name == root or name.startswith(root + ".")}


@pytest.mark.ac("gh-223:AC-1")
def test_each_slice_has_a_facade_and_the_flat_modules_are_gone() -> None:
    for slice_name in SLICES:
        assert (PACKAGE / slice_name / "__init__.py").is_file(), slice_name
    present = [name for name in REMOVED_FLAT_FILES if (PACKAGE / name).exists()]
    assert present == []


@pytest.mark.ac("gh-223:AC-3")
def test_search_and_gate_import_neither_run_nor_harness() -> None:
    checked = [path for path in _modules() if _slice_of(path) in LEAF_SLICES]
    assert checked
    broken = [f"{path.relative_to(PACKAGE)} imports {name}"
              for path in checked for forbidden in FORBIDDEN_FROM_LEAVES
              for name in sorted(_slice_modules(_imported(path), forbidden))]
    assert broken == []


@pytest.mark.ac("gh-223:AC-3")
def test_importing_search_and_gate_loads_neither_run_nor_harness() -> None:
    program = ("import sys, skillz_experiments.search, skillz_experiments.gate\n"
               "print([name for name in sys.modules if name.startswith(('skillz_experiments.run', 'skillz_experiments.harness'))])")
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join([str(PACKAGE.parent), os.environ.get("PYTHONPATH", "")])}
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True, timeout=60,
                            env=environment)
    assert result.stdout.strip() == "[]"


def _private_chains(tree: ast.AST, home: str | None) -> list[str]:
    """Return each `slice._private` access on a slice reached by `from package import slice`, `import package.slice as name`, or the full chain."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == PREFIX:
            aliases.update({alias.asname or alias.name: alias.name for alias in node.names
                            if alias.name in SLICES and alias.name != home})
        elif isinstance(node, ast.Import):
            aliases.update({alias.asname: alias.name.removeprefix(f"{PREFIX}.") for alias in node.names
                            if alias.asname and alias.name.startswith(f"{PREFIX}.")
                            and alias.name.removeprefix(f"{PREFIX}.") in SLICES and alias.name.removeprefix(f"{PREFIX}.") != home})
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr.startswith("_")):
            continue
        owner = node.value
        if isinstance(owner, ast.Name) and owner.id in aliases:
            found.append(f"{aliases[owner.id]}.{node.attr}")
        elif (isinstance(owner, ast.Attribute) and owner.attr in SLICES and owner.attr != home
              and isinstance(owner.value, ast.Name) and owner.value.id == PREFIX):
            found.append(f"{owner.attr}.{node.attr}")
    return found


@pytest.mark.ac("gh-223:AC-4")
def test_modules_outside_a_slice_import_it_only_through_its_facade() -> None:
    broken: list[str] = []
    for path in _modules():
        home = _slice_of(path)
        for slice_name in SLICES:
            if home == slice_name:
                continue
            for name in sorted(_slice_modules(_imported(path), slice_name)):
                if name != f"{PREFIX}.{slice_name}" and not _is_facade_member(name, slice_name):
                    broken.append(f"{path.relative_to(PACKAGE)} imports {name}")
        broken.extend(f"{path.relative_to(PACKAGE)} reaches {name}"
                      for name in _private_chains(ast.parse(path.read_text()), home))
    assert broken == []


@pytest.mark.ac("gh-223:AC-4")
def test_an_attribute_chain_into_a_private_submodule_is_caught() -> None:
    tree = ast.parse("from skillz_experiments import harness\nharness._nono.x\nharness.Facade\n")
    assert _private_chains(tree, "run") == ["harness._nono"]
    assert _private_chains(tree, "harness") == []


@pytest.mark.ac("gh-223:AC-4")
def test_an_aliased_module_import_into_a_private_submodule_is_caught() -> None:
    tree = ast.parse("import skillz_experiments.harness as h\nh._nono.x\nh.Facade\n")
    assert _private_chains(tree, "run") == ["harness._nono"]
    assert _private_chains(tree, "harness") == []


@pytest.mark.ac("gh-223:AC-4")
def test_a_full_attribute_chain_into_a_private_submodule_is_caught() -> None:
    tree = ast.parse("import skillz_experiments.harness\nskillz_experiments.harness._nono.x\nskillz_experiments.harness.Facade\n")
    assert _private_chains(tree, "run") == ["harness._nono"]
    assert _private_chains(tree, "harness") == []


def test_the_cli_names_no_gemini_and_imports_only_the_wrapper_barrel() -> None:
    assert "gemini" not in (PACKAGE / "_cli.py").read_text().lower()
    wrapper = _slice_modules(_imported(PACKAGE / "_cli.py"), "wrapper")
    assert wrapper <= {f"{PREFIX}.wrapper", f"{PREFIX}.wrapper.register"}


@pytest.mark.ac("gh-223:AC-4")
def test_a_private_chain_through_the_wrapper_barrel_is_caught() -> None:
    from_import = ast.parse("from skillz_experiments import wrapper\nwrapper._gemini.IMAGE\n")
    full_chain = ast.parse("import skillz_experiments.wrapper\nskillz_experiments.wrapper._commands.respond\n")
    assert _private_chains(from_import, "run") == ["wrapper._gemini"]
    assert _private_chains(full_chain, "run") == ["wrapper._commands"]


def test_the_wrapper_register_call_through_the_barrel_is_allowed() -> None:
    tree = ast.parse("from skillz_experiments import wrapper\nwrapper.register(group)\n")
    assert _private_chains(tree, "run") == []


def test_the_wrapper_slice_does_not_import_the_cli_framework_at_runtime() -> None:
    program = ("import sys, skillz_experiments.wrapper\n"
               "print([name for name in sys.modules if name.split('.')[0] in ('fromargs', 'cyclopts')])")
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join([str(PACKAGE.parent), os.environ.get("PYTHONPATH", "")])}
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True, timeout=60,
                            env=environment)
    assert result.stdout.strip() == "[]"


def _is_facade_member(name: str, slice_name: str) -> bool:
    """Return whether `name` is `slice.<attr>` where `<attr>` is a facade export, not a private submodule."""
    attribute = name.removeprefix(f"{PREFIX}.{slice_name}.")
    return "." not in attribute and not (PACKAGE / slice_name / f"{attribute}.py").exists() \
        and not (PACKAGE / slice_name / attribute).is_dir()

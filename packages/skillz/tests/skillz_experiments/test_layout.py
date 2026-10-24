"""Module layout rules for `skillz_experiments`: slices expose a facade and keep the dependency direction."""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import skillz_experiments

PACKAGE = Path(skillz_experiments.__file__).parent
PREFIX = "skillz_experiments"
SLICES = ("run", "search", "gate", "harness", "wrapper", "evaluation")
LEAF_SLICES = ("search", "gate")
FORBIDDEN_FROM_LEAVES = ("run", "harness")
REMOVED_FLAT_FILES = ("_workflow.py", "_search.py", "_gate.py", "_harness.py", "_claude.py", "_evaluator.py")
TESTS = Path(__file__).resolve().parent
DECISIONS = Path(__file__).resolve().parents[4] / "docs" / "agents" / "decisions"
# An ADR id is `<decision file stem>:ADR-NNN`, because each file in `docs/agents/decisions/` numbers its own records.
ADR_011 = "skillz-autoimprove:ADR-011"
# Each module at the package root, with the decision that keeps it there. A new flat module goes into a slice (ADR-011).
ROOT_MODULES: dict[str, str] = {
    "__init__": ADR_011,
    "__main__": ADR_011,
    "_audit": ADR_011,
    "_budget": ADR_011,
    "_candidate": ADR_011,
    "_cases": ADR_011,
    "_cli": "harness-module-interface:ADR-004",
    "_contract": ADR_011,
    "_evals_format": ADR_011,
    "_facts": ADR_011,
    "_frontmatter": ADR_011,
    "_gate_files": ADR_011,
    "_graders": ADR_011,
    "_intake": ADR_011,
    "_journey_gate": ADR_011,
    "_new_cli": ADR_011,
    "_protocol": "harness-module-interface:ADR-002",
    "_records": ADR_011,
    "_redact": ADR_011,
    "_runtime": ADR_011,
    "_scaffold": ADR_011,
    "_schemas": ADR_011,
    "_seed": ADR_011,
    "_validate": ADR_011,
}
# The root modules that a slice may import, with the decision that shares each one. A slice keeps other root code out.
KERNEL: dict[str, str] = {
    "_audit": ADR_011,
    "_budget": ADR_011,
    "_candidate": ADR_011,
    "_cases": ADR_011,
    "_contract": ADR_011,
    "_frontmatter": ADR_011,
    "_graders": ADR_011,
    "_intake": ADR_011,  # move into run
    "_new_cli": ADR_011,
    "_protocol": "harness-module-interface:ADR-002",
    "_records": ADR_011,
    "_redact": ADR_011,
    "_runtime": ADR_011,
    "_seed": ADR_011,  # move into run
}


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
@pytest.mark.ac("gh-247:AC-4")
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


# The private-chain hits in test files, as `<file> reaches <slice>.<attr>`. Shrink-only: a stale entry fails the test.
# Each `__all__` read pins a facade's public export list; it is a dunder, not a private submodule.
TEST_PRIVATE_CHAINS = frozenset({
    "test_module_interface.py reaches evaluation.__all__",
    "test_module_interface.py reaches harness.__all__",
    "test_module_interface.py reaches wrapper.__all__",
})


def _private_chains_under(root: Path) -> set[str]:
    """Return `<file> reaches <slice>.<attr>` for each private chain in a `.py` file below `root`, which is outside every slice."""
    return {f"{path.relative_to(root).as_posix()} reaches {name}" for path in sorted(root.rglob("*.py"))
            for name in _private_chains(ast.parse(path.read_text()), None)}


def test_test_files_reach_no_slice_private_through_a_facade() -> None:
    found = _private_chains_under(TESTS)
    added, stale = sorted(found - TEST_PRIVATE_CHAINS), sorted(TEST_PRIVATE_CHAINS - found)
    assert added == [], f"{added}: import the name from the slice facade, not through a private attribute chain"
    assert stale == [], f"{stale} no longer match: remove them from TEST_PRIVATE_CHAINS in test_layout.py"


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


def test_a_test_file_with_a_private_chain_is_caught(tmp_path: Path) -> None:
    probe = tmp_path / "nested" / "test_probe.py"
    probe.parent.mkdir()
    _ = probe.write_text("from skillz_experiments import harness\nharness._nono.x\nharness.Facade\n")
    _ = (tmp_path / "test_clean.py").write_text("import skillz_experiments.harness as h\nh.Facade\n")
    assert _private_chains_under(tmp_path) == {"nested/test_probe.py reaches harness._nono"}


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


def _imported_modules(path: Path) -> list[str]:
    package = (PREFIX, *path.relative_to(PACKAGE).parts[:-1])
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join([*package[:len(package) - node.level + 1], *([node.module] if node.module else [])])
            found += [f"{base}.{alias.name}" if alias.name.startswith("_") else base for alias in node.names]
    return found


@pytest.mark.ac("gh-389:AC-1")
def test_harness_modules_take_evaluation_code_only_from_the_evaluation_facade() -> None:
    evaluation = f"{PREFIX}.evaluation"
    wrong = [f"{path.relative_to(PACKAGE)} imports {module}"
             for path in sorted((PACKAGE / "harness").rglob("*.py"))
             for module in _imported_modules(path)
             if module.startswith(f"{evaluation}.") or module.startswith(f"{PREFIX}._evaluation")]
    assert wrong == []


def test_the_package_root_holds_only_the_frozen_root_modules() -> None:
    on_disk = {path.stem for path in PACKAGE.glob("*.py")}
    added, removed = sorted(on_disk - ROOT_MODULES.keys()), sorted(ROOT_MODULES.keys() - on_disk)
    assert added == [], (f"new flat modules {added}: put it in a slice (ADR-011: evaluation/, cases/, candidate/) "
                         "or add it here with an ADR id (ROOT_MODULES in test_layout.py)")
    assert removed == [], f"root modules {removed} are gone: remove their ROOT_MODULES entries in test_layout.py"


def _decision_ids() -> set[str]:
    """Return `<file stem>:ADR-NNN` for each `### ADR-NNN` heading in `docs/agents/decisions/*.md`."""
    return {f"{path.stem}:{match[1]}" for path in DECISIONS.glob("*.md")
            for line in path.read_text().splitlines() if (match := re.match(r"### (ADR-\d+)\b", line))}


def test_each_frozen_set_entry_cites_a_recorded_decision() -> None:
    cited = {f"{label}[{name!r}]": adr for label, frozen in (("ROOT_MODULES", ROOT_MODULES), ("KERNEL", KERNEL))
             for name, adr in frozen.items()}
    known = _decision_ids()
    unknown = sorted(f"{entry} = {adr!r}" for entry, adr in cited.items() if adr not in known)
    assert unknown == [], (f"{unknown}: cite `<file stem>:ADR-NNN` for a `### ADR-NNN` heading "
                           "in docs/agents/decisions/<file stem>.md, or write that ADR")


def _root_imports_from_slices() -> dict[str, set[str]]:
    """Map each root module that a slice module imports to the importing files, relative to the package."""
    roots = {path.stem for path in PACKAGE.glob("*.py")}
    importers: dict[str, set[str]] = {}
    for path in _modules():
        if _slice_of(path) is None:
            continue
        for module in _imported_modules(path):
            parts = module.split(".")
            if parts[0] == PREFIX and len(parts) > 1 and parts[1] in roots:
                importers.setdefault(parts[1], set()).add(str(path.relative_to(PACKAGE)))
    return importers


def test_slices_import_only_kernel_root_modules() -> None:
    outside = sorted(f"{file} imports {PREFIX}.{name}" for name, files in _root_imports_from_slices().items()
                     if name not in KERNEL for file in files)
    assert outside == [], (f"{outside}: move the root module into the slice that uses it (ADR-011), "
                           "or add it to KERNEL in test_layout.py with an ADR id")


def test_each_kernel_entry_has_a_slice_importer() -> None:
    unused = sorted(KERNEL.keys() - _root_imports_from_slices().keys())
    assert unused == [], f"no slice imports {unused}: remove their KERNEL entries in test_layout.py"

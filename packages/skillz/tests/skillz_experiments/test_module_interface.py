"""Guard the slice boundaries of `harness/` and `wrapper/`: barrels only, no adapter branching outside the slice.

The wrapper imports the harness only through its barrel. The harness never imports the wrapper.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

import skillz_experiments.harness as harness_barrel
import skillz_experiments.wrapper as wrapper_barrel
from skillz_experiments.harness import Transport
from skillz_experiments.harness._claude import ClaudeCode
from skillz_experiments.harness._codex import Codex
from skillz_experiments.harness._command import Command

SOURCE = Path(__file__).resolve().parents[2] / "src/skillz_experiments"
SLICES = ("harness", "wrapper")
ADAPTER_CLASSES = {"ClaudeCode", "Codex", "Command"}
BRANCH_TYPES = ADAPTER_CLASSES | {"Transport"}
ADAPTER_NAMES = {"claude", "codex"}
ADAPTER_NAME_ALLOWED: dict[str, str] = {}
CAPABILITY_PROBES = {
    "check_name", "check_candidate", "rejection", "credential_kind", "claude_sandbox", "refuses_claude_options",
    "host_checks", "reuse_key", "resume", "run_facts", "adopt_login"}
CREDENTIAL_NAMES = {"SUBSCRIPTION_LOGIN", "API_KEY", "UNVERIFIED_CREDENTIAL", "MISSING_CREDENTIAL"}


def _modules() -> list[Path]:
    return sorted(path for path in SOURCE.rglob("*.py") if "__pycache__" not in path.parts)


def _slice(path: Path) -> str | None:
    relative = path.relative_to(SOURCE)
    return relative.parts[0] if relative.parts[0] in SLICES else None


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _runtime_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Walk every node except the body of an `if TYPE_CHECKING:` block."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(node, ast.If) and child in node.body and _is_type_checking(node.test):
            continue
        yield from _runtime_nodes(child)


def _imported(path: Path, tree: ast.Module | None = None) -> Iterator[tuple[str, list[str]]]:
    """Yield each runtime import as `(absolute module, names)`. `import a.b` gives `("a.b", [])`."""
    package = ["skillz_experiments", *path.relative_to(SOURCE).parent.parts]
    for node in _runtime_nodes(tree or _tree(path)):
        if isinstance(node, ast.ImportFrom):
            base = package[:len(package) - node.level + 1] if node.level else []
            module = ".".join([*base, *([node.module] if node.module else [])])
            yield module, [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, []


def _reaches(module: str, names: list[str], target: str) -> bool:
    """True when this import loads `skillz_experiments.<target>` or any module below it."""
    parts = module.split(".")
    return parts[:2] == ["skillz_experiments", target] or (parts == ["skillz_experiments"] and target in names)


def _internal(module: str, names: list[str]) -> str | None:
    """Return the slice whose private module this import reaches, or None."""
    parts = module.split(".")
    if parts[:1] != ["skillz_experiments"] or len(parts) < 2 or parts[1] not in SLICES:
        return None
    if len(parts) > 2:
        return parts[1]
    return parts[1] if any(name.startswith("_") for name in names) else None


def test_no_import_reaches_into_a_slice_from_outside_it() -> None:
    found = [f"{path.relative_to(SOURCE)} imports {module}" for path in _modules()
             for module, names in _imported(path) if (reached := _internal(module, names)) and reached != _slice(path)]
    assert found == []


def test_the_import_walk_resolves_relative_and_package_imports() -> None:
    sample = SOURCE / "wrapper" / "_sample.py"
    assert _internal("skillz_experiments.harness._claude", []) == "harness"
    assert _reaches("skillz_experiments", ["harness"], "harness") and not _reaches("skillz_experiments", ["wrapper"], "harness")
    assert not _reaches("skillz_experiments.harnessed", [], "harness")
    tree = ast.parse("from ..harness._claude import X\nif TYPE_CHECKING:\n    from .. import harness\n"
                     + "if not TYPE_CHECKING:\n    from ..harness import _roles\n")
    assert list(_imported(sample, tree)) == [("skillz_experiments.harness._claude", ["X"]),
                                             ("skillz_experiments.harness", ["_roles"])]


def _constants(node: ast.AST) -> set[str]:
    return {item.value for item in ast.walk(node) if isinstance(item, ast.Constant) and isinstance(item.value, str)}


def _adapter_name_branches(tree: ast.Module) -> Iterator[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and ADAPTER_NAMES & _constants(node):
            yield node
        elif isinstance(node, ast.MatchValue) and ADAPTER_NAMES & _constants(node.value):
            yield node


def _describe(node: ast.AST) -> str | None:
    """Name the adapter branching in `node`, or None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id
        if name == "isinstance" and len(node.args) == 2 and BRANCH_TYPES & {
                item.id for item in ast.walk(node.args[1]) if isinstance(item, ast.Name)}:
            return "isinstance against an adapter class or Transport"
        if name == "hasattr" and len(node.args) >= 2 and (
                not isinstance(node.args[1], ast.Constant) or node.args[1].value in CAPABILITY_PROBES):
            return "hasattr probe"
        if name == "getattr" and len(node.args) >= 2:
            probe = node.args[1]
            if isinstance(probe, ast.Constant) and probe.value in CAPABILITY_PROBES:
                return f"getattr probe for {probe.value}"
    if isinstance(node, ast.Compare) and isinstance(node.left, ast.Call) and isinstance(node.left.func, ast.Name) \
            and node.left.func.id == "type" and BRANCH_TYPES & {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}:
        return "type(...) comparison against an adapter class"
    return None


def test_no_adapter_branching_outside_the_harness_slice() -> None:
    found: list[str] = []
    for path in _modules():
        if _slice(path) == "harness":
            continue
        relative = str(path.relative_to(SOURCE))
        tree = _tree(path)
        for node in ast.walk(tree):
            if reason := _describe(node):
                found.append(f"{relative}:{getattr(node, 'lineno', 0)} {reason}")
        found += [f"{relative}:{getattr(node, 'lineno', 0)} compares with an adapter name"
                  for node in _adapter_name_branches(tree) if relative not in ADAPTER_NAME_ALLOWED]
    assert found == []


@pytest.mark.parametrize("source", [
    'adapter in ("claude", "codex")', 'adapter in {"codex"}', 'adapter in ["claude"]', '"claude" == adapter',
    'match adapter:\n    case "claude":\n        pass', 'hasattr(p, "check_name")', 'isinstance(p, Transport)',
    'type(p) is ClaudeCode', 'getattr(p, "refuses_claude_options")'])
def test_the_branching_guard_catches_each_evasion(source: str) -> None:
    tree = ast.parse(source)
    assert any(_describe(node) for node in ast.walk(tree)) or list(_adapter_name_branches(tree))


def test_the_wrapper_slice_imports_the_harness_slice_only_through_its_barrel() -> None:
    found = [f"{path.relative_to(SOURCE)} imports {module}" for path in _modules() if _slice(path) == "wrapper"
             for module, names in _imported(path)
             if _reaches(module, names, "harness") and module not in {"skillz_experiments", "skillz_experiments.harness"}]
    assert found == []


def test_the_harness_slice_never_imports_the_wrapper_slice() -> None:
    found = [f"{path.relative_to(SOURCE)} imports {module}" for path in _modules() if _slice(path) == "harness"
             for module, names in _imported(path) if _reaches(module, names, "wrapper")]
    assert found == []


def test_importing_the_harness_loads_no_wrapper_module() -> None:
    code = "import sys, skillz_experiments.harness; print([m for m in sys.modules if m.startswith('skillz_experiments.wrapper')])"
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def _module_id(path: object) -> str:
    return str(cast(Path, path).relative_to(SOURCE))


@pytest.mark.parametrize("path", [path for path in _modules() if _slice(path) is not None], ids=_module_id)
def test_each_slice_module_imports_alone_in_a_fresh_interpreter(path: Path) -> None:
    relative = path.relative_to(SOURCE).with_suffix("")
    name = ".".join(("skillz_experiments", *relative.parts)).removesuffix(".__init__")
    result = subprocess.run([sys.executable, "-I", "-c", f"import {name}"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_transport_and_the_credential_kinds_are_defined_only_in_the_harness_slice() -> None:
    found: list[str] = []
    for path in _modules():
        if _slice(path) == "harness":
            continue
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef) and node.name == "Transport":
                found.append(f"{path.relative_to(SOURCE)} defines Transport")
            if isinstance(node, ast.Assign):
                found += [f"{path.relative_to(SOURCE)} assigns {target.id}" for target in node.targets
                          if isinstance(target, ast.Name) and target.id in CREDENTIAL_NAMES]
    assert found == []


def test_the_evaluator_imports_nothing_from_the_harness_slice_at_runtime() -> None:
    found = [module for module, names in _imported(SOURCE / "_evaluator.py") if _reaches(module, names, "harness")]
    assert found == []


def _as_transports(claude: ClaudeCode, codex: Codex, command: Command) -> list[Transport]:
    """Basedpyright accepts this only while each adapter satisfies `Transport`."""
    first: Transport = claude
    second: Transport = codex
    third: Transport = command
    return [first, second, third]


def test_every_adapter_is_a_transport() -> None:
    adapters = (object.__new__(ClaudeCode), object.__new__(Codex), object.__new__(Command))
    assert [type(item) for item in _as_transports(*adapters)] == [ClaudeCode, Codex, Command]


def test_the_barrels_export_exactly_the_contract_names() -> None:
    assert set(harness_barrel.__all__) == {
        "Configuration", "Harness", "EnvironmentDiffers", "BUILTIN_ADAPTERS", "Transport", "doctor", "host_report",
        "ClaudeOptions", "Effort", "NONO_REMOVED", "command_profile", "stop_kind"}
    assert set(wrapper_barrel.__all__) == {"respond", "Runtime", "IMAGE"}


@pytest.mark.parametrize(("code", "kind"), [
    ("isolation-failed", "terminal"), ("credential-changed", "terminal"),
    ("credential-rotated", "notice"), ("credential-refreshed", "notice"),
    ("budget-exhausted", None), (None, None),
])
def test_stop_kind_classifies_claude_stop_codes(code: str | None, kind: str | None) -> None:
    assert harness_barrel.stop_kind(code) == kind

"""Select and compile the bytecode shipped beside the source closure."""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
from importlib.resources import files
import json
import modulefinder
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, IO, cast

if TYPE_CHECKING:
    from wedge._config import WedgeConfig


class _SourceFinder(modulefinder.ModuleFinder):
    def find_module(  # pyright: ignore[reportImplicitOverride]
        self, name: str, path: str | None, parent: modulefinder.Module | None = None
    ) -> tuple[IO[bytes] | None, str | None, tuple[str, str, int]]:
        try:
            return super().find_module(name, path, parent)
        except AttributeError:
            spec = importlib.machinery.PathFinder.find_spec(name, cast(list[str], path or self.path))
            if spec is not None and spec.loader is None:
                raise ImportError(f"namespace package {name!r} has no source loader") from None
            raise


def _site_modules(site_dir: Path, entry: str) -> set[Path]:
    finder = _SourceFinder(path=[str(site_dir), *(path for path in sys.path if Path(path).is_dir())])
    pending = {entry.split(":", 1)[0]}
    entry_file = site_dir / (next(iter(pending)).replace(".", "/") + ".py")
    if entry_file.is_file():
        try:
            _ = ast.parse(entry_file.read_bytes(), filename=str(entry_file))
        except SyntaxError as error:
            raise ValueError(f"invalid source while selecting bytecode: {error}") from error
    scanned: set[Path] = set()
    while pending:
        module = pending.pop()
        try:
            _ = finder.import_hook(module)
        except ImportError:
            continue
        except SyntaxError as error:
            raise ValueError(f"invalid source while selecting bytecode: {error}") from error
        for found in finder.modules.values():
            filename = cast(str | None, getattr(found, "__file__", None))
            if filename is None:
                continue
            path = Path(filename)
            if path.suffix != ".py" or not path.is_relative_to(site_dir) or path in scanned:
                continue
            scanned.add(path)
            try:
                tree = ast.parse(path.read_bytes(), filename=str(path))
            except SyntaxError as error:
                raise ValueError(f"invalid source while selecting bytecode: {error}") from error
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "importlib"
                    and node.func.attr == "import_module"
                ):
                    argument = node.args[0] if node.args else next(
                        (keyword.value for keyword in node.keywords if keyword.arg == "name"), None
                    )
                    if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                        continue
                    name = argument.value
                    if name.startswith("."):
                        package = node.args[1] if len(node.args) > 1 else next(
                            (keyword.value for keyword in node.keywords if keyword.arg == "package"), None
                        )
                        if not isinstance(package, ast.Constant) or not isinstance(package.value, str):
                            continue
                        try:
                            name = importlib.util.resolve_name(name, package.value)
                        except ImportError:
                            continue
                    pending.add(name)
    return scanned


def _python_for_pin(pin: str) -> str:
    minor = ".".join(pin.split(".")[:2])
    uv = shutil.which("uv")
    if uv is None:
        if "UV_PYTHON_INSTALL_DIR" in os.environ:
            root = Path(os.environ["UV_PYTHON_INSTALL_DIR"])
        else:
            root = Path(sys.executable).resolve().parents[2]
            if root.name != "python" or root.parent.name != "uv":
                raise ValueError(f"uv is required to resolve managed CPython {pin}")
        matches = sorted(root.glob(f"cpython-{pin}-*/bin/python{minor}"))
        if len(matches) != 1:
            raise ValueError(f"expected one installed managed CPython {pin} in {root}")
        python = str(matches[0])
    else:
        root = Path(subprocess.run([uv, "python", "dir"], check=True, capture_output=True, text=True).stdout.strip())
        matches = sorted(root.glob(f"cpython-{pin}-*/bin/python{minor}"))
        if len(matches) == 1:
            python = str(matches[0])
        else:
            request = [uv, "python", "find", "--no-project", "--managed-python", "--no-python-downloads", pin]
            found = subprocess.run(request, capture_output=True, text=True)
            if found.returncode != 0:
                _ = subprocess.run([uv, "python", "install", pin], check=True, capture_output=True, text=True)
                found = subprocess.run(request, check=True, capture_output=True, text=True)
            python = found.stdout.strip()
    actual = subprocess.run(
        [python, "-I", "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if actual != pin:
        raise ValueError(f"expected managed CPython {pin}, found {actual}")
    return python


def _copy_selected(site_dir: Path, tree: Path, selected: set[Path], first_party: frozenset[str]) -> None:
    for top in sorted(first_party):
        source = site_dir / top
        target = tree / top
        if source.is_dir():
            _ = shutil.copytree(source, target)
        else:
            _ = shutil.copy2(source, target)
    for source in sorted(selected):
        relative = source.relative_to(site_dir)
        if relative.parts[0] in first_party:
            continue
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copy2(source, target)
    package_roots = {path.relative_to(site_dir).parts[0] for path in selected} - first_party
    for root in sorted(package_roots):
        package = site_dir / root
        if not package.is_dir():
            continue
        for source in sorted(package.rglob("*")):
            if source.is_file() and source.suffix not in {".py", ".pyc"}:
                target = tree / source.relative_to(site_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                _ = shutil.copy2(source, target)


def build_bytecode_trees(
    site_dir: Path, scratch: Path, entry: str, config: WedgeConfig, first_party: frozenset[str]
) -> dict[str, Path]:
    """Compile isolated trees; never write through links into the cached layer."""
    if not config.bytecode:
        return {}
    from wedge._config import BYTECODE_PYTHONS

    pins = dict(BYTECODE_PYTHONS)
    analyzer_pin = next(iter(pins.values()))
    analyzer = _python_for_pin(analyzer_pin)
    result = subprocess.run(
        [analyzer, "-I", "-S", "-c", files("wedge").joinpath("_bytecode.py").read_text(), str(site_dir), entry],
        capture_output=True, text=True,
    )
    if result.returncode:
        raise ValueError(f"managed CPython selector {analyzer} failed: {result.stderr.strip()}")
    selected = {Path(name) for name in cast(list[str], json.loads(result.stdout))}
    trees: dict[str, Path] = {}
    for minor in config.bytecode:
        python = analyzer if pins[minor] == analyzer_pin else _python_for_pin(pins[minor])
        tag = subprocess.run(
            [python, "-I", "-c", "import sys; print(sys.implementation.cache_tag)"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        tree = scratch / tag
        tree.mkdir()
        _copy_selected(site_dir, tree, selected, first_party)
        for directory in tree.rglob("*"):
            if directory.is_dir():
                directory.chmod(directory.stat().st_mode | 0o200)
        _ = subprocess.run(
            [python, "-I", "-m", "compileall", "-q", "-b", "--invalidation-mode", "unchecked-hash",
             "-s", str(tree), "-p", "site-packages", str(tree)],
            check=True, capture_output=True, text=True,
        )
        for source in sorted(tree.rglob("*.py")):
            if source.relative_to(tree).parts[0] not in first_party:
                source.unlink()
        trees[tag] = tree
    return trees


if __name__ == "__main__":
    print(json.dumps(sorted(str(path) for path in _site_modules(Path(sys.argv[1]), sys.argv[2]))))

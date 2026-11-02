"""Bytecode trees run through the real layer builder and stock zip importer."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast
from zipfile import ZipFile

import pytest

from wedge._build import SiteLayer, build_from_layer, site_layer_key
from wedge._bytecode import _python_for_pin, _site_modules, build_bytecode_trees  # pyright: ignore[reportPrivateUsage]
from wedge._config import BYTECODE_PYTHONS, WedgeConfig


@pytest.fixture(scope="module")
def built_archives(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("bytecode")
    project = root / "project"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")
    layer_dir = root / "layer"
    package = layer_dir / "third"
    package.mkdir(parents=True)
    _ = (package / "__init__.py").write_text("from . import active\n")
    _ = (package / "active.py").write_text(
        'import importlib\nVALUE = importlib.import_module(".deep", "third").VALUE\n'
        + 'KEYWORD = importlib.import_module(name=".keyword", package="third").VALUE\n'
        + 'def boom():\n    raise ValueError("from third-party bytecode")\n'
    )
    _ = (package / "deep.py").write_text("VALUE = 7\n")
    _ = (package / "keyword.py").write_text("VALUE = 8\n")
    _ = (package / "late.py").write_text("VALUE = 9\n")
    late_package = package / "late_package"
    late_package.mkdir()
    _ = (late_package / "__init__.py").write_text("VALUE = 17\n")
    _ = (late_package / "data.txt").write_text("late resource\n")
    _ = (package / "unused.py").write_text("VALUE = 11\n")
    _ = (package / "data.txt").write_text("resource from tree\n")
    sources = root / "sources"
    sources.mkdir()
    _ = (sources / "app.py").write_text(
        "import importlib\nimport importlib.resources\nimport json\nimport linecache\nimport os\nimport sys\nimport third\n"
        + "def main():\n"
        + "    if '--chdir' in sys.argv:\n        os.chdir('/')\n"
        + "    if '--late-package' in sys.argv:\n"
        + "        package = importlib.import_module('third.' + 'late_package')\n"
        + "        print(package.VALUE, importlib.resources.files(package).joinpath('data.txt').read_text().strip())\n"
        + "        return\n"
        + "    if '--cache-lines' in sys.argv:\n"
        + "        import __main__\n        from zipfile import ZipFile\n        opens = []\n"
        + "        def counted(path):\n            opens.append(path)\n            return ZipFile(path)\n"
        + "        __main__.ZipFile = counted\n"
        + "        lines = [linecache.getlines('site-packages/third/active.py') for _ in range(5)]\n"
        + "        print(len(opens), lines[0][0].strip(), all(lines[0] == item for item in lines))\n"
        + "        return\n"
        + "    if '--trace' in sys.argv:\n        third.active.boom()\n"
        + "    if '--help' in sys.argv:\n"
        + "        print(json.dumps({name: module.__file__ for name, module in sys.modules.items() if name == 'third' or name.startswith('third.')}))\n"
        + "        return\n"
        + "    suffix = 'late'\n"
        + "    late = importlib.import_module('third.' + suffix)\n"
        + "    print(third.active.VALUE, late.VALUE, importlib.resources.files(third).joinpath('data.txt').read_text().strip())\n"
        + "    print(third.active.__file__)\n"
    )
    layer = SiteLayer(layer_dir.resolve(), site_layer_key(project, ()), (), project)
    default = WedgeConfig(name="app", entry="app:main", source="app.py", repo="o/n")
    fallback_minor = "3.12" if sys.version_info[:2] == (3, 11) else "3.11"
    subset = WedgeConfig(name="source-fallback", entry="app:main", source="app.py", repo="o/n", bytecode=(fallback_minor,))
    return (
        build_from_layer(layer, sources, default, root / "default"),
        build_from_layer(layer, sources, subset, root / "subset"),
    )


def _run(pyz: Path, *args: str, python: str = sys.executable) -> subprocess.CompletedProcess[str]:
    return subprocess.run([python, "-I", str(pyz), *args], capture_output=True, text=True, cwd="/")


@pytest.mark.ac("gh-581:AC-2")
@pytest.mark.ac("gh-581:AC-5")
def test_default_trees_hold_exact_static_third_party_set(built_archives: tuple[Path, Path]) -> None:
    pyz, _ = built_archives
    with ZipFile(pyz) as archive:
        names = set(archive.namelist())
    for tag in ("cpython-311", "cpython-312", "cpython-313", "cpython-314", "cpython-315"):
        assert {f"{tag}/third/__init__.pyc", f"{tag}/third/active.pyc", f"{tag}/third/deep.pyc", f"{tag}/third/keyword.pyc"} <= names
        assert {name for name in names if name.startswith(f"{tag}/third/") and name.endswith(".pyc")} == {
            f"{tag}/third/__init__.pyc", f"{tag}/third/active.pyc", f"{tag}/third/deep.pyc", f"{tag}/third/keyword.pyc"
        }
        assert f"{tag}/app.py" in names and f"{tag}/app.pyc" in names
    assert "site-packages/third/late.py" in names
    assert "site-packages/third/late_package/__init__.py" in names
    assert "site-packages/third/unused.py" in names


@pytest.mark.ac("gh-581:AC-2")
@pytest.mark.ac("gh-581:AC-18")
def test_help_loads_all_third_party_modules_from_matching_pyc(built_archives: tuple[Path, Path]) -> None:
    pyz, _ = built_archives
    result = subprocess.run([sys.executable, "-I", "-v", str(pyz), "--help"], capture_output=True, text=True, cwd="/")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        name: f"{pyz}/{sys.implementation.cache_tag}/" + name.replace(".", "/") + ("/__init__.pyc" if name == "third" else ".pyc")
        for name in ("third", "third.active", "third.deep", "third.keyword")
    }


@pytest.mark.ac("gh-581:AC-4")
def test_missing_submodule_and_resources_use_stock_importers(built_archives: tuple[Path, Path]) -> None:
    pyz, _ = built_archives
    result = _run(pyz)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "7 9 resource from tree"
    assert lines[1] == f"{pyz}/{sys.implementation.cache_tag}/third/active.pyc"


@pytest.mark.ac("gh-581:AC-4")
def test_late_source_subpackage_keeps_initializer_and_resources(built_archives: tuple[Path, Path]) -> None:
    pyz, _ = built_archives
    result = _run(pyz, "--late-package")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "17 late resource\n"


def test_relative_archive_keeps_late_imports_after_chdir(built_archives: tuple[Path, Path]) -> None:
    pyz, _ = built_archives
    result = subprocess.run(
        [sys.executable, "-I", pyz.name, "--chdir", "--late-package"],
        cwd=pyz.parent, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "17 late resource\n"


def test_traceback_source_cache_avoids_reopening_archive(built_archives: tuple[Path, Path]) -> None:
    pyz, _ = built_archives
    result = _run(pyz, "--cache-lines")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1 import importlib True\n"


@pytest.mark.ac("gh-581:AC-3")
def test_unmatched_tag_uses_source_with_identical_output(built_archives: tuple[Path, Path]) -> None:
    pyz, subset = built_archives
    matching = _run(pyz)
    fallback = _run(subset)
    assert matching.returncode == fallback.returncode == 0
    assert matching.stdout.splitlines()[0] == fallback.stdout.splitlines()[0] == "7 9 resource from tree"
    assert fallback.stdout.splitlines()[1] == f"{subset}/site-packages/third/active.py"


@pytest.mark.ac("gh-581:AC-6")
@pytest.mark.parametrize("pin", [pin for _, pin in BYTECODE_PYTHONS])
def test_third_party_bytecode_traceback_has_source_line(
    built_archives: tuple[Path, Path], pin: str,
) -> None:
    pyz, _ = built_archives
    result = _run(pyz, "--trace", python=_python_for_pin(pin))
    assert result.returncode != 0
    assert 'File "site-packages/third/active.py", line 5, in boom' in result.stderr
    assert 'raise ValueError("from third-party bytecode")' in result.stderr
    assert "third/active.pyc" not in result.stderr


@pytest.mark.ac("gh-581:AC-6")
def test_archive_preserves_a_caller_exception_hook(built_archives: tuple[Path, Path]) -> None:
    pyz, _ = built_archives
    bootstrap = (
        "import runpy, sys\n"
        "archive = sys.argv[1]\n"
        "sys.argv = [archive, '--trace']\n"
        "sys.excepthook = lambda kind, value, trace: print(f'custom {kind.__name__}: {value}', file=sys.stderr)\n"
        "runpy.run_path(archive, run_name='__main__')\n"
    )
    result = subprocess.run(
        [_python_for_pin("3.12.16"), "-I", "-c", bootstrap, str(pyz)],
        capture_output=True, text=True, cwd="/",
    )
    assert result.returncode != 0
    assert result.stderr.strip() == "custom ValueError: from third-party bytecode"


@pytest.mark.ac("gh-581:AC-14")
def test_empty_setting_builds_no_bytecode_trees(built_archives: tuple[Path, Path], tmp_path: Path) -> None:
    pyz, _ = built_archives
    root = pyz.parent.parent
    project = root / "project"
    layer = SiteLayer((root / "layer").resolve(), site_layer_key(project, ()), (), project)
    target = WedgeConfig(name="source-only", entry="app:main", source="app.py", repo="o/n", bytecode=())
    source_only = build_from_layer(layer, root / "sources", target, tmp_path)
    with ZipFile(source_only) as archive:
        names = archive.namelist()
    assert "site-packages/third/active.py" in names
    assert not any(name.startswith("cpython-") for name in names)
    result = _run(source_only)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "7 9 resource from tree"


@pytest.mark.ac("gh-581:AC-15")
def test_skillz_archives_stay_below_github_warning_size() -> None:
    root = Path(__file__).resolve().parents[4]
    scripts = root / "skills" / "skillz" / "scripts"
    for name in ("skillz-experiment", "inspect-skill", "wedge", "agent-session-analytics"):
        archive = scripts / f"{name}.pyz"
        assert archive.is_file(), archive
        assert archive.stat().st_size < 50_000_000, (name, archive.stat().st_size)


@pytest.mark.ac("gh-581:AC-6")
@pytest.mark.parametrize("minor,pin", BYTECODE_PYTHONS)
def test_coding_cookie_source_compiles_and_supplies_traceback(tmp_path: Path, minor: str, pin: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")
    layer_dir = tmp_path / "layer"
    package = layer_dir / "third"
    package.mkdir(parents=True)
    _ = (package / "__init__.py").write_bytes(b"# coding: latin-1\nfrom . import active\n")
    _ = (package / "active.py").write_bytes(
        b"# coding: latin-1\ndef boom():\n    raise ValueError('caf\xe9')\n"
    )
    sources = tmp_path / "sources"
    sources.mkdir()
    _ = (sources / "app.py").write_text("from third.active import boom\ndef main():\n    boom()\n")
    layer = SiteLayer(layer_dir.resolve(), site_layer_key(project, ()), (), project)
    target = WedgeConfig(name="latin", entry="app:main", source="app.py", repo="o/n", bytecode=(minor,))
    pyz = build_from_layer(layer, sources, target, tmp_path / "out")
    result = _run(pyz, python=_python_for_pin(pin))
    assert result.returncode != 0
    assert "raise ValueError('café')" in result.stderr
    assert "UnicodeDecodeError" not in result.stderr


@pytest.mark.ac("gh-581:AC-6")
@pytest.mark.parametrize("minor,pin", BYTECODE_PYTHONS)
def test_unicode_separator_in_string_keeps_traceback_line(tmp_path: Path, minor: str, pin: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")
    package = tmp_path / "layer" / "third"
    package.mkdir(parents=True)
    _ = (package / "__init__.py").write_text("from .active import boom\n")
    _ = (package / "active.py").write_text('def boom():\n    text = "a\u2028b"\n    raise ValueError("marker")\n')
    sources = tmp_path / "sources"
    sources.mkdir()
    _ = (sources / "app.py").write_text("from third import boom\ndef main():\n    boom()\n")
    layer = SiteLayer(package.parent.resolve(), site_layer_key(project, ()), (), project)
    target = WedgeConfig(name="unicode", entry="app:main", source="app.py", repo="o/n", bytecode=(minor,))
    result = _run(build_from_layer(layer, sources, target, tmp_path / "out"), python=_python_for_pin(pin))
    assert result.returncode != 0
    assert 'raise ValueError("marker")' in result.stderr


def test_static_source_analysis_reports_build_error(tmp_path: Path) -> None:
    site = tmp_path / "site"
    package = site / "third"
    package.mkdir(parents=True)
    _ = (package / "__init__.py").write_text("VALUE = 1\n")
    _ = (site / "app.py").write_text("def broken(:\n")
    minor = ".".join(str(part) for part in sys.version_info[:2])
    target = WedgeConfig(name="invalid", entry="app:main", source="app.py", repo="o/n", bytecode=(minor,))

    with pytest.raises(ValueError, match="invalid source while selecting bytecode"):
        _ = _site_modules(site, target.entry)
    with pytest.raises(ValueError, match="invalid source while selecting bytecode"):
        _ = build_bytecode_trees(site, tmp_path / "scratch", target.entry, target, frozenset({"app.py"}))


@pytest.mark.ac("gh-581:AC-5")
def test_production_selection_ignores_different_builder_packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from wedge._bytecode import _python_for_pin  # pyright: ignore[reportPrivateUsage]

    installed_root = Path(subprocess.run(
        ["uv", "python", "dir"], check=True, capture_output=True, text=True,
    ).stdout.strip())
    managed_root = tmp_path / "managed-python"
    managed_root.mkdir()
    for pin in ("3.11.18", "3.13.18"):
        installed = sorted(installed_root.glob(f"cpython-{pin}-*"))
        if len(installed) == 1:
            (managed_root / installed[0].name).symlink_to(installed[0], target_is_directory=True)
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(managed_root))
    monkeypatch.setenv("UV_PYTHON_INSTALL_BIN", "0")
    site = tmp_path / "site"
    package = site / "third"
    package.mkdir(parents=True)
    _ = (site / "app.py").write_text("import ambient_bridge\nimport third\n")
    _ = (package / "__init__.py").write_text("from . import active\n")
    _ = (package / "active.py").write_text("VALUE = 1\n")
    _ = (package / "extra.py").write_text("VALUE = 2\n")
    ambient_with_bridge = tmp_path / "with-bridge"
    ambient_with_bridge.mkdir()
    _ = (ambient_with_bridge / "ambient_bridge.py").write_text("import third.extra\n")
    ambient_without_bridge = tmp_path / "without-bridge"
    ambient_without_bridge.mkdir()
    bootstrap = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]).parents[1] / 'fromargs' / 'src'), sys.argv[2], sys.argv[5]]\n"
        "from wedge._bytecode import _site_modules, build_bytecode_trees\n"
        "from wedge._config import WedgeConfig\n"
        "site, scratch = Path(sys.argv[3]), Path(sys.argv[4])\n"
        "scratch.mkdir()\n"
        "ambient = sorted(str(path.relative_to(site)) for path in _site_modules(site, 'app:main'))\n"
        "config = WedgeConfig(name='app', entry='app:main', source='app.py', repo='o/n', bytecode=('3.11',))\n"
        "trees = build_bytecode_trees(site, scratch, 'app:main', config, frozenset({'app.py'}))\n"
        "selected = sorted(str(path.relative_to(next(iter(trees.values())))) for tree in trees.values() "
        "for path in tree.rglob('third/*.pyc'))\n"
        "print(json.dumps({'ambient': ambient, 'selected': selected}))\n"
    )
    wedge_src = Path(__file__).resolve().parents[2] / "src"
    results: list[dict[str, list[str]]] = []
    for pin, ambient in (("3.11.18", ambient_with_bridge), ("3.13.18", ambient_without_bridge)):
        result = subprocess.run(
            [_python_for_pin(pin), "-I", "-c", bootstrap, str(wedge_src), str(ambient), str(site),
             str(tmp_path / pin), str(Path(pytest.__file__).resolve().parent.parent)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        results.append(cast(dict[str, list[str]], json.loads(result.stdout)))
    assert "third/extra.py" in results[0]["ambient"]
    assert "third/extra.py" not in results[1]["ambient"]
    assert results[0]["selected"] == results[1]["selected"] == [
        "third/__init__.pyc", "third/active.pyc",
    ]


@pytest.mark.ac("gh-581:AC-2")
def test_offline_compiler_uses_explicit_managed_root_in_real_process(tmp_path: Path) -> None:
    pin = "3.11.18"
    installed_root = Path(subprocess.run(
        ["uv", "python", "dir"], check=True, capture_output=True, text=True,
    ).stdout.strip())
    (installed,) = sorted(installed_root.glob(f"cpython-{pin}-*"))
    managed_root = tmp_path / "managed-python"
    managed_root.mkdir()
    (managed_root / installed.name).symlink_to(installed, target_is_directory=True)
    site = tmp_path / "site"
    site.mkdir()
    _ = (site / "app.py").write_text("def main():\n    return 0\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    host_python = tmp_path / "non-uv" / "bin" / "python3"
    host_python.parent.mkdir(parents=True)
    _ = shutil.copy2(sys.executable, host_python)
    _ = (host_python.parent.parent / "pyvenv.cfg").write_text(f"home = {Path(sys.base_prefix) / 'bin'}\n")
    host = subprocess.run([str(host_python), "--version"], capture_output=True, text=True)
    assert host.returncode == 0, host.stderr
    bootstrap = (
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path[:0] = sys.argv[1:4]\n"
        "from wedge._bytecode import build_bytecode_trees\n"
        "from wedge._config import WedgeConfig\n"
        "config = WedgeConfig(name='app', entry='app:main', source='app.py', repo='o/n', bytecode=('3.11',))\n"
        "trees = build_bytecode_trees(Path(sys.argv[4]), Path(sys.argv[5]), 'app:main', config, frozenset({'app.py'}))\n"
        "print(sys.executable)\n"
        "print(sorted((tag, (tree / 'app.pyc').is_file()) for tag, tree in trees.items()))\n"
    )
    env = os.environ.copy()
    env.update(PATH=str(tmp_path / "no-bin"), UV_OFFLINE="1", UV_PYTHON_INSTALL_DIR=str(managed_root))
    packages = Path(__file__).resolve().parents[3]
    sources = [str(packages / "wedge" / "src"), str(packages / "fromargs" / "src")]
    site_packages = str(Path(pytest.__file__).resolve().parent.parent)
    command = [str(host_python), "-I", "-c", bootstrap, *sources, site_packages, str(site), str(scratch)]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(host_python), "[('cpython-311', True)]"]

    env["UV_PYTHON_INSTALL_DIR"] = str(tmp_path / "empty-root")
    rejected = subprocess.run(command, env=env, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert f"expected one installed managed CPython {pin} in {tmp_path / 'empty-root'}" in rejected.stderr


def test_missing_entry_import_has_no_selected_sources(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    assert _site_modules(site, "missing:main") == set()


def test_relative_literal_without_package_is_not_selected(tmp_path: Path) -> None:
    site = tmp_path / "site"
    package = site / "third"
    package.mkdir(parents=True)
    _ = (site / "app.py").write_text("import third\n")
    _ = (package / "__init__.py").write_text(
        'import importlib\nimportlib.import_module(".missing")\n'
    )

    assert _site_modules(site, "app:main") == {site / "app.py", package / "__init__.py"}


@pytest.mark.ac("gh-581:AC-5")
@pytest.mark.parametrize("arguments", ['".deep", "third"', 'name=".deep", package="third"'])
def test_relative_literals_select_nested_and_ignore_outside_sources(tmp_path: Path, arguments: str) -> None:
    site = tmp_path / "site"
    package = site / "third"
    package.mkdir(parents=True)
    _ = (site / "app.py").write_text("import third\n")
    _ = (package / "__init__.py").write_text(
        f'import importlib\nimportlib.import_module({arguments})\n'
        + 'try:\n    importlib.import_module("..optional", "third")\nexcept ImportError:\n    pass\n'
    )
    _ = (package / "deep.py").write_text("VALUE = 7\n")
    _ = (site / "optional.py").write_text("VALUE = 8\n")

    assert _site_modules(site, "app:main") == {
        site / "app.py", package / "__init__.py", package / "deep.py",
    }


@pytest.mark.ac("gh-581:AC-5")
def test_caught_beyond_root_relative_import_does_not_stop_build(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")
    package = tmp_path / "layer" / "third"
    package.mkdir(parents=True)
    _ = (package / "__init__.py").write_text(
        'import importlib\ntry:\n    importlib.import_module("..optional", "third")\nexcept ImportError:\n    pass\nVALUE = 1\n'
    )
    sources = tmp_path / "sources"
    sources.mkdir()
    _ = (sources / "app.py").write_text("import third\ndef main():\n    print(third.VALUE)\n")
    layer = SiteLayer(package.parent.resolve(), site_layer_key(project, ()), (), project)
    minor = ".".join(str(part) for part in sys.version_info[:2])
    target = WedgeConfig(name="optional", entry="app:main", source="app.py", repo="o/n", bytecode=(minor,))

    result = _run(build_from_layer(layer, sources, target, tmp_path / "out"))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1\n"


def test_matching_analyzer_pin_resolves_once_and_compiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import wedge._bytecode as bytecode

    site = tmp_path / "site"
    site.mkdir()
    _ = (site / "app.py").write_text("def main(): pass\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    original = bytecode._python_for_pin  # pyright: ignore[reportPrivateUsage]
    resolved: list[str] = []

    def resolve(pin: str) -> str:
        resolved.append(pin)
        return original(pin)

    monkeypatch.setattr(bytecode, "_python_for_pin", resolve)
    config = WedgeConfig(name="app", entry="app:main", source="app.py", repo="o/n", bytecode=("3.11",))
    trees = build_bytecode_trees(site, scratch, config.entry, config, frozenset({"app.py"}))
    assert resolved == ["3.11.18"]
    assert (trees["cpython-311"] / "app.pyc").is_file()


def test_single_file_third_party_module_builds_bytecode(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    _ = (site / "app.py").write_text("import third\n")
    _ = (site / "third.py").write_text("VALUE = 1\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    minor = ".".join(str(part) for part in sys.version_info[:2])
    target = WedgeConfig(name="app", entry="app:main", source="app.py", repo="o/n", bytecode=(minor,))

    trees = build_bytecode_trees(site, scratch, target.entry, target, frozenset({"app.py"}))

    tree = next(iter(trees.values()))
    assert (tree / "third.pyc").is_file()
    assert not (tree / "third.py").exists()


def test_namespace_package_without_source_loader_is_ignored(tmp_path: Path) -> None:
    site = tmp_path / "site"
    namespace = site / "namespace"
    namespace.mkdir(parents=True)
    _ = (site / "app.py").write_text("import namespace\n")
    _ = (namespace / "unused.py").write_text("VALUE = 1\n")

    assert _site_modules(site, "app:main") == {site / "app.py"}


def test_real_compiler_error_stops_tree_build(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    _ = (site / "app.py").write_text("def main(): pass\n")
    _ = (site / "broken.py").write_text("def broken(:\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    minor = ".".join(str(part) for part in sys.version_info[:2])
    target = WedgeConfig(name="app", entry="app:main", source="app.py", repo="o/n", bytecode=(minor,))

    with pytest.raises(subprocess.CalledProcessError):
        _ = build_bytecode_trees(site, scratch, target.entry, target, frozenset({"app.py", "broken.py"}))


@pytest.mark.ac("gh-581:AC-5")
def test_readonly_first_party_package_compiles_without_changing_source_modes(tmp_path: Path) -> None:
    site = tmp_path / "site"
    package = site / "first"
    nested = package / "nested"
    nested.mkdir(parents=True)
    _ = (package / "__init__.py").write_text("from . import nested\ndef main(): print(nested.VALUE)\n")
    _ = (nested / "__init__.py").write_text("VALUE = 42\n")
    for path in (package, nested):
        path.chmod(0o555)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    target = WedgeConfig(name="first", entry="first:main", source="first", repo="o/n", bytecode=("3.11",))
    try:
        trees = build_bytecode_trees(site, scratch, target.entry, target, frozenset({"first"}))
        tree = trees["cpython-311"]
        for relative in (Path("first"), Path("first/nested")):
            assert (tree / relative / "__init__.pyc").is_file()
            assert (site / relative).stat().st_mode & 0o777 == 0o555
            assert not (site / relative / "__init__.pyc").exists()
    finally:
        for path in (package, nested):
            path.chmod(0o755)

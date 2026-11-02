"""wedge's site-layer cache: reuse only a layer whose every file matches its manifest."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

import wedge._archive as wedge_archive
import wedge._build as wedge_build
import wedge._layer_cache as layer_cache
from wedge._build import SiteLayer, build, build_from_layer, site_layer_key
from wedge._bytecode import build_bytecode_trees
from wedge._config import WedgeConfig
from wedge._layer_cache import cache_root, cached_layer, manifest

Populate = Callable[[Path], None]


def _entries(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir())


def _counting_populate(calls: list[Path]) -> Populate:
    def populate(dest: Path) -> None:
        calls.append(dest)
        (dest / "pkg" / "sub").mkdir(parents=True)
        _ = (dest / "pkg" / "__init__.py").write_text("VALUE = 1\n")
        _ = (dest / "pkg" / "sub" / "mod.py").write_text("X = 2\n")
        (dest / "pkg-1.0.dist-info").mkdir()
        _ = (dest / "pkg-1.0.dist-info" / "METADATA").write_text("Name: pkg\n")

    return populate


@pytest.mark.ac("wedge-site-layer-cache:AC-1")
def test_a_valid_entry_is_reused_without_populating() -> None:
    calls: list[Path] = []
    populate = _counting_populate(calls)

    first, recorded = cached_layer("k", populate)
    second, again = cached_layer("k", populate)

    assert recorded == again == manifest(first)

    assert first == second == cache_root() / "k" / "site"
    assert len(calls) == 1
    assert _entries(cache_root()) == ["k"], "a populated entry leaves no staging directory"


def _edit(site: Path, _entry: Path) -> None:
    with (site / "pkg" / "sub" / "mod.py").open("a") as handle:
        _ = handle.write("EVIL = 1\n")


def _add(site: Path, _entry: Path) -> None:
    _ = (site / "pkg" / "extra.py").write_text("EVIL = 1\n")


def _add_dir(site: Path, _entry: Path) -> None:
    (site / "evil").mkdir()


def _remove(site: Path, _entry: Path) -> None:
    (site / "pkg" / "sub" / "mod.py").unlink()


def _symlink(site: Path, entry: Path) -> None:
    secret = entry.parent.parent / "secret.py"
    _ = secret.write_text("X = 2\n")
    (site / "pkg" / "sub" / "mod.py").unlink()
    (site / "pkg" / "sub" / "mod.py").symlink_to(secret)


def _edit_manifest(site: Path, entry: Path) -> None:
    """An inconsistent manifest: it no longer matches the layer."""
    _edit(site, entry)
    recorded = cast(dict[str, str], json.loads((entry / "manifest.json").read_text()))
    recorded["pkg/sub/mod.py"] = "0" * 64
    _ = (entry / "manifest.json").write_text(json.dumps(recorded))


def _drop_manifest(_site: Path, entry: Path) -> None:
    (entry / "manifest.json").unlink()


def _corrupt_manifest(_site: Path, entry: Path) -> None:
    _ = (entry / "manifest.json").write_text("{not json")


def _site_symlink(site: Path, entry: Path) -> None:
    elsewhere = entry.parent.parent / "elsewhere"
    _ = shutil.copytree(site, elsewhere)
    shutil.rmtree(site)
    site.symlink_to(elsewhere)


@pytest.mark.ac("wedge-site-layer-cache:AC-2")
@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(tamper, id=tamper.__name__.lstrip("_"))
        for tamper in (_edit, _add, _add_dir, _remove, _symlink, _edit_manifest, _drop_manifest,
                       _corrupt_manifest, _site_symlink)
    ],
)
def test_a_tampered_entry_is_discarded_and_populated_again(tamper: Callable[[Path, Path], None]) -> None:
    calls: list[Path] = []
    populate = _counting_populate(calls)
    site, fresh = cached_layer("k", populate)
    tamper(site, site.parent)

    again, recorded = cached_layer("k", populate)

    assert recorded == fresh

    assert len(calls) == 2
    assert again == site and not again.is_symlink()
    assert manifest(again) == fresh
    assert _entries(cache_root()) == ["k"], "the discarded entry and the staging directory are gone"


@pytest.mark.ac("wedge-site-layer-cache:AC-3")
def test_a_failed_populate_leaves_no_entry_and_a_retry_works() -> None:
    def broken(dest: Path) -> None:
        _ = (dest / "half.py").write_text("partial")
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError, match="network down"):
        _ = cached_layer("k", broken)

    assert _entries(cache_root()) == []
    calls: list[Path] = []
    assert (cached_layer("k", _counting_populate(calls))[0] / "pkg" / "__init__.py").is_file()
    assert len(calls) == 1


@pytest.mark.ac("wedge-site-layer-cache:AC-3")
def test_builders_that_populate_one_key_at_once_share_one_entry() -> None:
    builders = 4
    barrier = threading.Barrier(builders)
    calls: list[Path] = []
    inner = _counting_populate(calls)

    def populate(dest: Path) -> None:
        inner(dest)
        _ = barrier.wait(timeout=30)

    def build_one(_index: int) -> Path:
        return cached_layer("k", populate)[0]

    with ThreadPoolExecutor(max_workers=builders) as pool:
        paths = list(pool.map(build_one, range(builders)))

    assert len(calls) == builders, "every builder populated before any entry existed"
    assert set(paths) == {cache_root() / "k" / "site"}
    assert _entries(cache_root()) == ["k"]
    assert json.loads((cache_root() / "k" / "manifest.json").read_text()) == manifest(paths[0])


@pytest.mark.ac("wedge-site-layer-cache:AC-3")
def test_a_new_entry_prunes_entries_and_leftovers_unused_for_thirty_days() -> None:
    calls: list[Path] = []
    populate = _counting_populate(calls)
    _ = cached_layer("old", populate)
    _ = cached_layer("recent", populate)
    leftover = cache_root() / ".old.crashed"
    leftover.mkdir()
    stale = time.time() - 31 * 24 * 60 * 60
    os.utime(cache_root() / "old" / "manifest.json", (stale, stale))
    os.utime(leftover, (stale, stale))

    _ = cached_layer("new", populate)

    assert _entries(cache_root()) == ["new", "recent"]


def test_the_cache_lives_under_wedge_cache(private_layer_cache: Path) -> None:
    assert cache_root() == private_layer_cache / "site-layers"


# ------------------------------------------------------------- the real build path


@pytest.mark.ac("wedge-site-layer-cache:AC-1")
@pytest.mark.ac("wedge-site-layer-cache:AC-2")
def test_build_reuses_the_cached_layer_and_rebuilds_a_tampered_one(
    tmp_path: Path, copy_repo_subset: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    installs: list[Path] = []
    original = wedge_build._install_third_party  # pyright: ignore[reportPrivateUsage]

    def counting(requirements: str, site_dir: Path) -> None:
        installs.append(site_dir)
        original(requirements, site_dir)

    monkeypatch.setattr(wedge_build, "_install_third_party", counting)
    clean = build(skill, tmp_path / "out-clean")
    reused = build(skill, tmp_path / "out-reused")
    assert len(installs) == 1, "the second build reuses the cached layer"
    assert reused.content_sha256 == clean.content_sha256

    (entry,) = [p for p in cache_root().iterdir() if not p.name.startswith(".")]
    victim = next(p for p in sorted((entry / "site").rglob("*.py")) if p.stat().st_size)
    original_text = victim.read_text()
    with victim.open("a") as handle:
        _ = handle.write("\n# edited in the wedge cache\n")

    # Without the check, the edit reaches the archive: the check is what keeps the output clean.
    verified = layer_cache._verified  # pyright: ignore[reportPrivateUsage]

    def unchecked(entry: Path) -> dict[str, str]:
        return manifest(entry / "site")

    monkeypatch.setattr(layer_cache, "_verified", unchecked)
    leaked = build(skill, tmp_path / "out-leaked")
    assert leaked.content_sha256 != clean.content_sha256
    assert len(installs) == 1
    monkeypatch.setattr(layer_cache, "_verified", verified)

    after = build(skill, tmp_path / "out-after")

    assert len(installs) == 2, "the tampered layer is installed again"
    assert after.content_sha256 == clean.content_sha256
    assert victim.read_text() == original_text


def _edit_existing(site: Path) -> None:
    victim = next(p for p in sorted(site.rglob("*.py")) if p.stat().st_size)
    with victim.open("a") as handle:
        _ = handle.write("\n# edited after the cache check\n")


def _add_top_level(site: Path) -> None:
    _ = (site / "zzz_evil.pth").write_text("import os\n")
    (site / "evilpkg").mkdir()
    _ = (site / "evilpkg" / "__init__.py").write_text("EVIL = 1\n")


@pytest.mark.ac("wedge-site-layer-cache:AC-5")
@pytest.mark.parametrize("change", [_edit_existing, _add_top_level], ids=["edit-existing", "add-top-level"])
def test_a_layer_change_after_the_cache_check_fails_the_build_and_writes_nothing(
    tmp_path: Path,
    copy_repo_subset: Callable[[Path], Path],
    monkeypatch: pytest.MonkeyPatch,
    change: Callable[[Path], None],
) -> None:
    skill = copy_repo_subset(tmp_path / "checkout")
    _ = build(skill, tmp_path / "out-clean")
    view = wedge_build._view_layer  # pyright: ignore[reportPrivateUsage]

    def change_then_view(layer: Path, site_dir: Path) -> frozenset[str]:
        change(layer)
        return view(layer, site_dir)

    monkeypatch.setattr(wedge_build, "_view_layer", change_then_view)

    with pytest.raises(ValueError, match="cached site layer changed during the build"):
        _ = build(skill, tmp_path / "out-changed")

    assert not list((tmp_path / "out-changed").iterdir())


def test_bytecode_uses_verified_snapshot_when_layer_changes_during_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")
    site = tmp_path / "layer"
    package = site / "third"
    package.mkdir(parents=True)
    source = package / "__init__.py"
    _ = source.write_text("VALUE = 1\n")
    sources = tmp_path / "sources"
    sources.mkdir()
    _ = (sources / "app.py").write_text("import third\ndef main():\n    print(third.VALUE)\n")
    layer = SiteLayer(site.resolve(), site_layer_key(project, ()), (), project)
    minor = ".".join(str(part) for part in sys.version_info[:2])
    target = WedgeConfig(name="snapshot", entry="app:main", source="app.py", repo="o/n", bytecode=(minor,))
    expected = manifest(site)
    compile_trees = build_bytecode_trees

    def mutate_then_compile(
        snapshot: Path, scratch: Path, entry: str, config: WedgeConfig, first_party: frozenset[str]
    ) -> dict[str, Path]:
        _ = source.write_text("VALUE = 2\n")
        return compile_trees(snapshot, scratch, entry, config, first_party)

    monkeypatch.setattr(wedge_build, "build_bytecode_trees", mutate_then_compile)
    _, pyz = wedge_build._build_from_layer(  # pyright: ignore[reportPrivateUsage]
        layer, sources, target, tmp_path / "out", expected,
    )
    result = subprocess.run([sys.executable, "-I", str(pyz)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1\n"
    assert source.read_text() == "VALUE = 2\n"


def test_the_layer_cache_key_covers_wedges_populate_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    key = wedge_build._layer_cache_key  # pyright: ignore[reportPrivateUsage]
    base = key("pkg==1.0 \\\n    --hash=sha256:00\n")
    assert key("# a header naming /some/checkout\npkg==1.0 \\\n    --hash=sha256:00\n    # via app\n") == base
    for name, value in (("_LAYER_RECIPE", 999), ("_INSTALL_FLAGS", ("--no-cache",)), ("_VOLATILE_INSTALL_FILES", {"RECORD"})):
        with monkeypatch.context() as patch:
            patch.setattr(wedge_build, name, value)
            assert key("pkg==1.0 \\\n    --hash=sha256:00\n") != base, name


# ------------------------------------------------------------- the layer view


def _hand_layer(root: Path) -> Path:
    (root / "clean" / "deep").mkdir(parents=True)
    _ = (root / "clean" / "__init__.py").write_text("A = 1\n")
    _ = (root / "clean" / "deep" / "data.txt").write_text("data\n")
    (root / "dirty" / "__pycache__").mkdir(parents=True)
    _ = (root / "dirty" / "__init__.py").write_text("B = 1\n")
    _ = (root / "dirty" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (root / "dirty-1.0.dist-info").mkdir()
    _ = (root / "dirty-1.0.dist-info" / "METADATA").write_text("Name: dirty\n")
    _ = (root / "dirty-1.0.dist-info" / "RECORD").write_text("volatile\n")
    (root / "bin").mkdir()
    _ = (root / "bin" / "tool").write_text("#!/host/python\n")
    _ = (root / "single.py").write_text("C = 1\n")
    _ = (root / "stale.pyc").write_bytes(b"\0")
    return root


@pytest.mark.ac("wedge-site-layer-cache:AC-4")
def test_a_linked_layer_view_builds_the_same_bytes_as_a_copy_and_never_writes_the_layer(tmp_path: Path) -> None:
    layer_dir = _hand_layer(tmp_path / "layer")
    project = tmp_path / "project"
    project.mkdir()
    _ = (project / "uv.lock").write_text("lock\n")
    layer = SiteLayer(layer_dir.resolve(), site_layer_key(project, ()), (), project)
    sources = tmp_path / "sources"
    sources.mkdir()
    _ = (sources / "app.py").write_text("def main() -> None: ...\n")
    target = WedgeConfig(name="app", entry="app:main", source="app.py", repo="o/n", bytecode=())
    before = manifest(layer_dir)

    linked = wedge_build._view_layer(layer_dir, tmp_path / "view")  # pyright: ignore[reportPrivateUsage]
    assert linked == {"clean", "single.py"}, "only clean entries are links; the rest are copied and stripped"

    reference = tmp_path / "copy"
    _ = shutil.copytree(layer_dir, reference)
    wedge_build._strip_volatile(reference)  # pyright: ignore[reportPrivateUsage]
    _ = shutil.copy2(sources / "app.py", reference / "app.py")
    out = tmp_path / "out"
    out.mkdir()
    expected, _ = wedge_archive.archive_into(reference, "app", "app:main", out)

    built = build_from_layer(layer, sources, target, tmp_path / "built")

    assert built.read_bytes() == (out / built.name).read_bytes()
    assert expected[:12] in built.name
    assert manifest(layer_dir) == before, "a build never writes to the layer"

def test_archive_rejects_changed_member_without_output(tmp_path: Path) -> None:
    site = tmp_path / "site"
    package = site / "third"
    package.mkdir(parents=True)
    _ = (site / "app.py").write_text("def main(): pass\n")
    _ = (package / "__init__.py").write_text("VALUE = 1\n")
    expected = manifest(site)
    _ = (package / "__init__.py").write_text("VALUE = 2\n")
    out = tmp_path / "out"
    out.mkdir()

    with pytest.raises(ValueError, match="cached site layer changed during the build"):
        _ = wedge_archive.archive_into(site, "app", "app:main", out, (expected, frozenset({"app.py"})))

    assert not list(out.iterdir())


def test_archive_removes_temporary_file_when_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = tmp_path / "site"
    site.mkdir()
    _ = (site / "app.py").write_text("def main(): pass\n")
    out = tmp_path / "out"
    out.mkdir()

    def fail_replace(_source: str, _dest: str | Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("wedge._archive.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        _ = wedge_archive.archive_into(site, "app", "app:main", out)

    assert not list(out.iterdir())

"""Build a reproducible .pyz for one skill's CLI.

The flow has three steps. ``populate_site_layer`` resolves and guards the
project's third-party closure and installs it into a layer directory; this
is the only step that needs the network. ``stage_sources`` copies the
configured local ``include`` trees and the skill's CLI source into a staged
tree. ``build_from_layer`` lays the layer and the staged tree out in a fresh
site directory, without volatile install metadata, and shivs the result with a
fixed shebang and ``SOURCE_DATE_EPOCH``. The same key always gives the same
uncompressed contents, so the content digest (``wedge._digest``) matches on
every host even when two zlib builds compress those contents differently.
Third-party wheels never come from the uv cache (``--no-cache``), so a
modified uv cache cannot change the contents. ``build`` and ``build_many``
reuse a layer from wedge's own cache (``wedge._layer_cache``) only after each
file matches the manifest that the fresh install recorded. The digest names
the asset.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from wedge._archive import canonicalize_archive
from wedge._config import ConfigError, WedgeConfig, load_config, load_targets, validate_name
from wedge._digest import content_sha256
from wedge._fanout import Outcome, fan_out
from wedge._guard import guard_closure
from wedge._key import (
    FORMAT_VERSION,
    TARGET_PYTHON,
    BuildPaths,
    compute_key,
    resolve_paths,
    selected_files,
)
from wedge._layer_cache import cached_layer
from wedge._resolve import export_requirements, resolve_closure

_VOLATILE_INSTALL_FILES = {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}
# --no-cache: see _install_third_party. These flags join the layer cache key.
_INSTALL_FLAGS = ("--no-cache", "--require-hashes", "--no-deps")
# Bump when _install_third_party or _strip_volatile changes what a layer holds, so no cached layer from older rules is reused.
_LAYER_RECIPE = 1
# Installed console scripts live in a top-level `bin`. Keep that name free of first-party sources.
_RESERVED_TOP_LEVEL = {"bin"}
_IMPORTABLE_SUFFIXES = {".py", ".so", ".pyd"}
# 1980-01-01T00:00:00Z: the earliest timestamp a zip member can hold. shiv's
# reproducible mode floors SOURCE_DATE_EPOCH=0 to this same value, but set it
# explicitly so the build does not depend on that floor.
_SOURCE_DATE_EPOCH = "315532800"
_NO_NAMES: frozenset[str] = frozenset()

_LayerId = tuple[Path, tuple[str, ...]]


@dataclass(frozen=True)
class BuildResult:
    """The built ``.pyz``: its key, content digest, and where it landed on disk."""

    name: str
    key: str
    content_sha256: str
    path: Path


@dataclass(frozen=True)
class _Prepared:
    """One target, resolved and keyed, ready to build from a site layer."""

    skill_dir: Path
    config: WedgeConfig
    key: str
    paths: BuildPaths

    @property
    def layer_id(self) -> _LayerId:
        """Targets with one project and one group set share one site layer."""
        return (self.paths.project, _sorted_groups(self.config.groups))


def _sorted_groups(groups: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(groups)))


def resolve_target(skill_dir: Path, config: WedgeConfig, *, missing_ok: bool = False) -> BuildPaths:
    """Resolve and validate ``config``'s project, source, and includes against the real checkout.

    With ``missing_ok``, the source and include paths may not exist. Such a target builds only from an overlay.

    Call this once per target. ``stage_sources`` then takes the result, so a
    caller that stages many candidate trees does not resolve the checkout again.
    """
    return resolve_paths(skill_dir, config, missing_ok=missing_ok)


def _prepare(skill_dir: Path, config: WedgeConfig | None = None) -> _Prepared:
    skill_dir = Path(skill_dir).resolve()
    config = config if config is not None else load_config(skill_dir)
    return _Prepared(skill_dir, config, compute_key(skill_dir, config), resolve_target(skill_dir, config))


def _prepare_all(skill_dir: Path) -> list[_Prepared]:
    """One prepared build per target of the skill's ``wedge.toml``."""
    skill_dir = Path(skill_dir).resolve()
    return [_prepare(skill_dir, config) for config in load_targets(skill_dir)]


def _closure_requirements(project: Path, groups: Sequence[str]) -> tuple[str, bool]:
    """Export and guard the closure; return the requirements and whether any package installs."""
    requirements = export_requirements(project, groups)
    closure = resolve_closure(project, requirements)
    guard_closure(closure)
    return requirements, bool(closure)


def _cached_site_layer(project: Path, groups: Sequence[str]) -> tuple[SiteLayer, Mapping[str, str]]:
    """Return the layer of ``project`` and ``groups`` from wedge's layer cache, and its manifest; install on a miss.

    The guard runs on every call. The cache key holds every locked requirement
    line with its hashes, and the cache hashes each file of a reused layer
    against the manifest of its fresh install, so a reused layer holds the
    same files as a fresh install. The build checks its archive against the
    manifest too, because the layer can change after the cache checked it.
    """
    project = Path(project).resolve()
    requirements, installs = _closure_requirements(project, groups)

    def populate(dest: Path) -> None:
        if installs:
            _install_third_party(requirements, dest)
        _strip_volatile(dest)

    path, expected = cached_layer(_layer_cache_key(requirements), populate)
    return SiteLayer(path, site_layer_key(project, groups), _sorted_groups(groups), project), expected


def _layer_cache_key(requirements: str) -> str:
    """A sha256 over what a layer holds: the requirement lines and hashes, the target, uv, and wedge's populate rules."""
    lines = [line.strip() for line in requirements.splitlines()]
    document = {
        "format_version": FORMAT_VERSION,
        "populate": [_LAYER_RECIPE, list(_INSTALL_FLAGS), sorted(_VOLATILE_INSTALL_FILES)],
        "target_python": TARGET_PYTHON,
        "platform": sysconfig.get_platform(),
        "uv": subprocess.run(["uv", "--version"], capture_output=True, text=True, check=True).stdout.strip(),
        "requirements": [line for line in lines if line and not line.startswith("#")],
    }
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class SiteLayer:
    """A third-party install dir, populated once and shared by later builds.

    ``key`` is a sha256 over the wedge format version, the ``uv.lock`` digest of
    ``project``, the sorted groups, and the target Python. No first-party source
    enters ``path``, and ``build_from_layer`` never writes to it. A key file
    beside ``path`` lets ``open_site_layer`` reopen the layer later.
    """

    path: Path
    key: str
    groups: tuple[str, ...]
    project: Path


def site_layer_key(project: Path, groups: Sequence[str]) -> str:
    document = {
        "format_version": FORMAT_VERSION,
        "target_python": TARGET_PYTHON,
        "uv_lock": hashlib.sha256((Path(project) / "uv.lock").read_bytes()).hexdigest(),
        "groups": sorted(set(groups)),
    }
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _key_file(dest: Path) -> Path:
    """The sidecar that records a layer's key. It sits beside the layer, never inside it."""
    return dest.with_name(f"{dest.name}.key")


def populate_site_layer(project: Path, groups: Sequence[str], dest: Path) -> SiteLayer:
    """Install ``project``'s frozen closure plus ``groups`` into the new directory ``dest``.

    This is the only step that needs the network. The key and the destination
    are checked first, then the pure-wheel guard runs before any install.
    The install runs in a sibling temporary directory that is renamed to ``dest``
    on success, so a failure leaves no ``dest`` behind and a retry works.
    The key goes to ``<dest>.key`` after the rename, through a temporary file
    and an atomic replace. The call refuses a ``dest`` whose key file exists or
    is a symlink. A caller removes both ``dest`` and ``<dest>.key``.
    """
    project = Path(project).resolve()
    dest = Path(dest)
    key = site_layer_key(project, groups)
    key_file = _key_file(dest)
    if dest.exists() or dest.is_symlink():
        raise FileExistsError(dest)
    if key_file.exists() or key_file.is_symlink():
        raise FileExistsError(key_file)
    requirements, installs = _closure_requirements(project, groups)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}.", dir=dest.parent))
    try:
        if installs:
            _install_third_party(requirements, staging)
        _strip_volatile(staging)
        os.rename(staging, dest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        _write_key(key_file, key)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return SiteLayer(dest.resolve(), key, _sorted_groups(groups), project)


def _write_key(key_file: Path, key: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{key_file.name}.", dir=key_file.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            _ = handle.write(key + "\n")
        os.chmod(temporary, 0o644)
        os.replace(temporary, key_file)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def open_site_layer(project: Path, groups: Sequence[str], dest: Path) -> SiteLayer:
    """Reopen the layer that ``populate_site_layer`` wrote to ``dest``, for a resumed run.

    Raises ``ValueError`` when ``dest`` has no key file or when ``uv.lock`` or
    ``groups`` changed since the layer was populated.
    """
    project = Path(project).resolve()
    dest = Path(dest)
    key = site_layer_key(project, groups)
    if dest.is_symlink() or not dest.is_dir():
        raise FileNotFoundError(dest)
    try:
        recorded = _key_file(dest).read_text().strip()
    except OSError as exc:
        raise ValueError(f"site layer {dest} has no key file: {exc}") from exc
    if recorded != key:
        raise ValueError(f"site layer {dest} was populated from other inputs; uv.lock or groups changed")
    return SiteLayer(dest.resolve(), key, _sorted_groups(groups), project)


def _under(root: Path, real: Path) -> bool:
    return real == root or root in real.parents


def _is_target_path(paths: BuildPaths, real: Path) -> bool:
    """Whether ``real`` lies under an include root, or under the source and its selectors."""
    if any(_under(root, real) for root in paths.includes):
        return True
    if not paths.source.exists():
        return _under(paths.source, real)
    if not paths.source.is_dir():
        return real == paths.source
    if not paths.source_paths:
        return _under(paths.source, real)
    return any(_under(paths.source / selector, real) for selector in paths.source_paths)


def _overlay_replacements(paths: BuildPaths, overlay: Path | None) -> dict[Path, Path]:
    """Map each real path to its file under ``overlay``; reject any path outside the target's roots."""
    if overlay is None:
        return {}
    overlay = Path(overlay)
    if overlay.is_symlink() or not overlay.is_dir():
        raise ValueError(f"overlay must be a directory and not a symlink: {overlay}")
    replacements: dict[Path, Path] = {}
    for directory, directories, files in os.walk(overlay):
        for name in directories:
            if (Path(directory) / name).is_symlink():
                raise ValueError(f"overlay must not contain symlink: {Path(directory) / name}")
        for name in files:
            file = Path(directory) / name
            relative = file.relative_to(overlay)
            if file.is_symlink() or not file.is_file():
                raise ValueError(f"overlay entry must be a regular file: {relative.as_posix()}")
            real = paths.project / relative
            if not _is_target_path(paths, real):
                raise ConfigError(f"overlay file is not under the target's sources: {relative.as_posix()}")
            replacements[real] = file
    return replacements


def _staged_paths(paths: BuildPaths, dest: Path, real: Path) -> list[Path]:
    """Every staged location of ``real``: one per include or source root that contains it."""
    staged = [
        dest / root.name if real == root else dest / root.name / real.relative_to(root)
        for root in (*paths.includes, paths.source)
        if _under(root, real)
    ]
    if not staged:
        raise ConfigError(f"not a source of the target: {real}")
    return staged


def stage_sources(paths: BuildPaths, dest: Path, overlay: Path | None = None, *, overlay_only: bool = False) -> Path:
    """Copy the target's includes and source into the new directory ``dest``; return it.

    ``paths`` comes from ``resolve_target``. With ``overlay``, a file under it
    replaces or adds the file at the same path relative to ``paths.project``,
    in every staged root that contains that path. An overlay file must lie
    under an include root, or under the source and inside its ``source_paths``
    selectors; any other overlay file is an error.
    With ``overlay_only``, nothing is read from the live checkout: ``dest`` holds
    only the overlay files, laid out under the target's roots.
    A failure removes ``dest``, except when ``dest`` already exists.
    """
    replacements = _overlay_replacements(paths, overlay)
    dest = Path(dest)
    dest.mkdir(parents=True)
    try:
        if not overlay_only:
            for local in paths.includes:
                _copy_source(local, dest)
            _copy_source(paths.source, dest, paths.source_paths)
        for real, replacement in replacements.items():
            for staged in _staged_paths(paths, dest, real):
                staged.parent.mkdir(parents=True, exist_ok=True)
                _ = shutil.copy2(replacement, staged)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


def build_from_layer(layer: SiteLayer, sources_tree: Path, target: WedgeConfig, out_dir: Path) -> Path:
    """Shiv ``target`` from ``layer`` plus ``sources_tree``, with no network.

    The layer key must match the current ``uv.lock`` and the target's groups.
    The build never writes to the layer. A first-party path that collides
    with an installed one is an error. The output is byte-identical to
    ``build`` for the same inputs. Returns
    ``<out_dir>/<name>-<content_sha256[:12]>.pyz``.
    """
    return _build_from_layer(layer, sources_tree, target, out_dir)[1]


def _build_from_layer(
    layer: SiteLayer,
    sources_tree: Path,
    target: WedgeConfig,
    out_dir: Path,
    expected: Mapping[str, str] | None = None,
) -> tuple[str, Path]:
    if layer.key != site_layer_key(layer.project, target.groups):
        raise ValueError(
            f"uv.lock or groups {target.groups} of target {target.name!r} do not match the layer {layer.groups}"
        )
    out_dir = _ensure_out_dir(out_dir)
    installed = frozenset(child.name for child in layer.path.iterdir())
    first_party: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="wedge-site-") as tmp:
        site_dir = Path(tmp) / "site"
        linked = _view_layer(layer.path, site_dir)
        for entry in sorted(Path(sources_tree).iterdir()):
            if entry.is_symlink():
                raise ValueError(f"build source must not be a symlink: {entry}")
            _check_free(site_dir, entry, installed)
            first_party.add(entry.name)
            destination = site_dir / entry.name
            if entry.is_dir():
                _copy_tree(entry, destination)
            else:
                _ = shutil.copy2(entry, destination, follow_symlinks=False)
        _reject_links(site_dir, linked)
        check = None if expected is None else (expected, frozenset(first_party))
        return _shiv_into(site_dir, target.name, target.entry, out_dir, check)


def _view_layer(layer: Path, site_dir: Path) -> frozenset[str]:
    """Fill the new ``site_dir`` with the layer's entries; return the names that are links into the layer.

    A top-level entry that holds only regular files and directories, and no
    volatile file, becomes a symlink to the layer entry. shiv follows such a
    link, so the archive gets the same paths and bytes as from a copy, and a
    build does not create and delete one file per installed file. Any other
    entry is copied and stripped as before, so ``_reject_links`` still sees
    a symlink or a special file that sits in the layer.
    """
    layer = layer.absolute()
    site_dir.mkdir()
    linked: set[str] = set()
    for child in sorted(layer.iterdir()):
        destination = site_dir / child.name
        if _readable_in_place(child):
            destination.symlink_to(child, target_is_directory=child.is_dir())
            linked.add(child.name)
        elif child.is_dir() and not child.is_symlink():
            _ = shutil.copytree(child, destination, symlinks=True)
        else:
            _ = shutil.copy2(child, destination, follow_symlinks=False)
    _strip_volatile(site_dir)
    return frozenset(linked)


def _readable_in_place(entry: Path) -> bool:
    """Whether the build can read the top-level layer ``entry`` through a link, with nothing to strip or reject."""
    mode = entry.lstat().st_mode
    if entry.name == "bin" or _is_volatile(entry, stat.S_ISDIR(mode), top_level=True):
        return False
    if stat.S_ISREG(mode):
        return True
    if not stat.S_ISDIR(mode):
        return False
    for directory, directories, files in os.walk(entry):
        for name in (*directories, *files):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)) or _is_volatile(path, stat.S_ISDIR(mode)):
                return False
    return True


def _build_prepared(
    prepared: _Prepared,
    layer: SiteLayer,
    stage_dir: Path,
    out_dir: Path,
    expected: Mapping[str, str] | None = None,
) -> BuildResult:
    """Stage one target's sources and shiv them over ``layer`` into ``out_dir``."""
    if layer.project != prepared.paths.project:
        raise ValueError(f"site layer project {layer.project} is not the target's project {prepared.paths.project}")
    sources = stage_sources(prepared.paths, stage_dir)
    digest, out_path = _build_from_layer(layer, sources, prepared.config, out_dir, expected)
    return BuildResult(prepared.config.name, prepared.key, digest, out_path)


def _shiv_into(
    site_dir: Path,
    name: str,
    entry: str,
    out_dir: Path,
    check: tuple[Mapping[str, str], frozenset[str]] | None = None,
) -> tuple[str, Path]:
    """Shiv ``site_dir`` and place the archive in ``out_dir``.

    With ``check``, a layer manifest and the first-party top-level names, every
    other ``site-packages`` member must match the manifest, file for file.
    Otherwise nothing lands in ``out_dir``.
    """
    validate_name(name)
    with tempfile.TemporaryDirectory(prefix="wedge-build-") as tmp:
        built = Path(tmp) / f"{name}.pyz"
        _shiv(site_dir, entry, built)
        canonicalize_archive(built)
        if check is not None:
            _check_layer_members(built, *check)
        digest = content_sha256(built)
        out_path = out_dir / f"{name}-{digest[:12]}.pyz"
        # A temp file in out_dir plus an atomic replace never writes through a
        # symlink that already sits at out_path.
        descriptor, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=out_dir)
        os.close(descriptor)
        try:
            _ = shutil.copyfile(built, temporary)
            os.chmod(temporary, stat.S_IMODE(built.stat().st_mode))
            os.replace(temporary, out_path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    return digest, out_path


def _check_layer_members(archive: Path, expected: Mapping[str, str], first_party: frozenset[str]) -> None:
    """Raise ``ValueError`` when the archive's third-party files differ from the layer manifest ``expected``.

    Every ``site-packages`` member outside the ``first_party`` top-level names
    is third-party; ``_check_free`` keeps the two name sets apart.
    """
    files = {path: digest for path, digest in expected.items() if digest != "dir"}
    found: dict[str, str] = {}
    with ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.removeprefix("site-packages/")
            if name != info.filename and not info.is_dir() and name.split("/", 1)[0] not in first_party:
                found[name] = hashlib.sha256(zf.read(info)).hexdigest()
    if found != files:
        changed = sorted(set(found) ^ set(files) | {p for p in found.keys() & files.keys() if found[p] != files[p]})
        raise ValueError(f"the cached site layer changed during the build ({', '.join(changed[:3])}); build again")


def build(skill_dir: Path, out_dir: Path) -> BuildResult:
    """Build ``<out_dir>/<name>-<content_sha256[:12]>.pyz``; returns its key and digest."""
    out_dir = _ensure_out_dir(out_dir)
    prepared = _prepare(skill_dir)
    layer, expected = _cached_site_layer(prepared.paths.project, prepared.config.groups)
    with tempfile.TemporaryDirectory(prefix="wedge-stage-") as tmp:
        return _build_prepared(prepared, layer, Path(tmp) / "stage", out_dir, expected)


def build_many(
    skill_dirs: Sequence[Path], out_dir: Path, *, jobs: int | None = None
) -> list[Outcome[Path, BuildResult]]:
    """Build every target of every skill into ``out_dir``, ``jobs`` at a time.

    Targets with the same project and groups share one site layer, from
    wedge's layer cache, so a repository of many skills over one package
    downloads its closure at most once. Each target then stages its own sources
    and builds from the layer. Returns one outcome per target in input order
    (a skill with ``[[target]]`` tables yields several); a failure while
    populating a layer fails every target that shares it.
    """
    out_dir = _ensure_out_dir(out_dir)
    prepared = fan_out(list(skill_dirs), _prepare_all, jobs=jobs)
    # Two targets with one name collapse into one entry of the name-keyed
    # result. Both fail instead; the rest still build.
    every = [target for p in prepared if p.value is not None for target in p.value]
    names = [target.config.name for target in every]
    duplicates = {name for name in names if names.count(name) > 1}
    valid = [target for target in every if target.config.name not in duplicates]
    layer_ids = list(dict.fromkeys(p.layer_id for p in valid))

    populated = fan_out(layer_ids, lambda layer_id: _cached_site_layer(layer_id[0], layer_id[1]), jobs=jobs)
    layers = {o.item: o.value for o in populated if o.value is not None}
    site_errors = {o.item: o.error for o in populated if o.error is not None}
    ready = [p for p in valid if p.layer_id in layers]
    with tempfile.TemporaryDirectory(prefix="wedge-stage-") as tmp:
        root = Path(tmp)
        stage_dirs = {id(p): root / f"stage-{index}" for index, p in enumerate(ready)}
        shivved = fan_out(
            ready,
            lambda p: _build_prepared(p, layers[p.layer_id][0], stage_dirs[id(p)], out_dir, layers[p.layer_id][1]),
            jobs=jobs,
        )
        built = {(p.skill_dir, p.config.name): o for p, o in zip(ready, shivved)}

    outcomes: list[Outcome[Path, BuildResult]] = []
    for skill_dir, o in zip(skill_dirs, prepared):
        path = Path(skill_dir)
        if o.error is not None:
            outcomes.append(Outcome(path, error=o.error))
            continue
        assert o.value is not None
        for target in o.value:
            if target.config.name in duplicates:
                outcomes.append(Outcome(path, error=f"duplicate target name {target.config.name!r}"))
            elif target.layer_id in site_errors:
                outcomes.append(Outcome(path, error=site_errors[target.layer_id]))
            else:
                result = built[(target.skill_dir, target.config.name)]
                outcomes.append(Outcome(path, value=result.value, error=result.error))
    return outcomes


def _install_third_party(requirements: str, site_dir: Path) -> None:
    # --no-cache: uv checks a wheel's hash only when it downloads it, and it
    # hardlinks cached unpacked wheels into every install. An edit to any
    # hardlinked copy changes the cache, and every later build would inherit
    # the change. A fresh download keeps the contents a function of the
    # locked wheel hashes alone.
    with tempfile.TemporaryDirectory(prefix="wedge-requirements-") as tmp:
        requirements_path = Path(tmp) / "requirements.txt"
        _ = requirements_path.write_text(requirements)
        _ = subprocess.run(
            [
                "uv",
                "pip",
                "install",
                *_INSTALL_FLAGS,
                "--target",
                str(site_dir),
                "--python-version",
                TARGET_PYTHON,
                "-r",
                str(requirements_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )


def _copy_tree(src: Path, dest: Path) -> None:
    if src.is_symlink():
        raise ValueError(f"build source must not be a symlink: {src}")
    for path in src.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"build source must not contain symlink: {path}")
    _ = shutil.copytree(src, dest, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def _import_name(path: Path) -> str:
    """The name Python imports ``path`` as: ``foo.py`` and ``foo.cpython-311.so`` both import as ``foo``."""
    if not path.is_dir() and path.suffix in _IMPORTABLE_SUFFIXES:
        return path.name.split(".")[0]
    return path.name


def layer_import_names(layer: SiteLayer) -> frozenset[str]:
    """The top-level names that ``import`` finds in the populated ``layer``. List only; import nothing."""
    return frozenset(
        _import_name(entry)
        for entry in layer.path.iterdir()
        if entry.name != "__pycache__"
        and not entry.name.endswith((".dist-info", ".data"))
        and (entry.is_dir() or entry.suffix in _IMPORTABLE_SUFFIXES)
    )


def _check_free(site_dir: Path, entry: Path, installed: frozenset[str] = _NO_NAMES) -> None:
    """Reject a top-level ``entry`` that collides with a child of ``site_dir`` or with a stripped name.

    A child named in ``installed`` comes from the site layer; any other child is another source.
    """
    if entry.name in _RESERVED_TOP_LEVEL:
        raise ConfigError(f"build source uses the reserved top-level name {entry.name!r}")
    names = {entry.name, _import_name(entry)}
    for child in sorted(site_dir.iterdir()):
        if names & {child.name, _import_name(child)}:
            side = "shadows installed" if child.name in installed else "collides with another source"
            raise ConfigError(f"build source {entry.name!r} {side} {child.name!r}")


def _copy_source(source: Path, site_dir: Path, selectors: tuple[str, ...] = ()) -> None:
    if source.is_symlink():
        raise ValueError(f"build source must not be a symlink: {source}")
    _check_free(site_dir, source)
    destination = site_dir / source.name
    if not selectors:
        if source.is_dir():
            _copy_tree(source, destination)
        else:
            _ = shutil.copy2(source, destination, follow_symlinks=False)
        return
    destination.mkdir()
    for selected in selected_files(source, selectors):
        relative = selected.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copy2(selected, target, follow_symlinks=False)


def _reject_links(root: Path, linked: frozenset[str] = _NO_NAMES) -> None:
    """Reject any symlink or special file in the build tree, just before shiv reads it.

    A top-level name in ``linked`` is a link that ``_view_layer`` made and checked; it is allowed.
    """
    for directory, directories, files in os.walk(root):
        for name in (*directories, *files):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) and not (name in linked and Path(directory) == root):
                raise ValueError(f"build tree must not contain symlink: {path}")
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                raise ValueError(f"build tree must hold only regular files and directories: {path}")


def _ensure_out_dir(out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    if out_dir.is_symlink():
        raise ValueError(f"output directory must not be a symlink: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir.resolve()


def _is_volatile(path: Path, is_dir: bool, *, top_level: bool = False) -> bool:
    """Whether ``_strip_volatile`` removes ``path``: bytecode, or install metadata inside a ``.dist-info`` directory."""
    if is_dir:
        return path.name == "__pycache__"
    return path.suffix == ".pyc" or (
        not top_level and path.name in _VOLATILE_INSTALL_FILES and path.parent.name.endswith(".dist-info")
    )


def _strip_volatile(site_dir: Path) -> None:
    # Installed console scripts embed the builder's absolute Python path.
    shutil.rmtree(site_dir / "bin", ignore_errors=True)
    # os.walk does not follow a link, so a strip never deletes through a link into the layer.
    for directory, directories, files in os.walk(site_dir):
        here = Path(directory)
        for name in [name for name in directories if _is_volatile(here / name, True)]:
            directories.remove(name)
            shutil.rmtree(here / name, ignore_errors=True)
        for name in files:
            if _is_volatile(here / name, False):
                (here / name).unlink()


def _shiv(site_dir: Path, entry: str, out_path: Path) -> None:
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = _SOURCE_DATE_EPOCH
    # Inside a wedge.pyz, sys.executable is the host interpreter, which cannot
    # import the archive's shiv. Point the child at the directory that holds it.
    shiv_spec = importlib.util.find_spec("shiv")
    if shiv_spec is not None and shiv_spec.origin is not None:
        shiv_root = str(Path(shiv_spec.origin).parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [shiv_root, env.get("PYTHONPATH")]))
    _ = subprocess.run(
        [
            sys.executable,
            "-m",
            "shiv",
            "--reproducible",
            "--uncompressed",
            "--site-packages",
            str(site_dir),
            "-p",
            "/usr/bin/env python3",
            "-e",
            entry,
            "-o",
            str(out_path),
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

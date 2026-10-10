"""The wedge targets that a skill owns: built bytes, editable sources, and the host rebuild.

A skill owns the targets in its own `wedge.toml`. Each own `.pyz` joins the candidate as frozen bytes. Under
`prose+cli`, the sources of some targets also join it as text under `WEDGE_PREFIX`, and a changed source
builds again on the host. The build only copies and zips source. It never imports or runs candidate code.

A project outside the repository root cannot give repo-relative names, so its targets stay frozen.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import cast, final

from wedge._build import SiteLayer, build_from_layer, open_site_layer, populate_site_layer, resolve_target, stage_sources
from wedge._bundle import bundle_path
from wedge._config import ConfigError, WedgeConfig, defaults_path, load_targets
from wedge._key import BuildPaths, first_party_files

from skillz_experiments._candidate import (FROZEN_FILE_LIMIT, PACKAGE_LIMIT, WEDGE_PREFIX, Candidate, as_text,
                                           ignored_files)
from skillz_experiments._cases import CodedError, digest, mapping, relative
from skillz_experiments._contract import Contract
from skillz_experiments._facts import repo_root

# A target never offers these as editable sources.
NEVER_EDITABLE = frozenset({"wedge.toml", "uv.lock"})
# The editable wedge sources of a run together stay within this many characters. A reflection echoes every one.
WEDGE_EDIT_LIMIT = 100_000
LayerId = tuple[Path, tuple[str, ...]]
_DETAIL_LIMIT = 600
# The rebuilder keeps this many built results.
_CACHE_ENTRIES = 4


def never_editable(name: str) -> bool:
    """Return whether the file `name` defines or holds a build: `wedge.toml`, `uv.lock`, or a `.pyz`."""
    base = PurePosixPath(name)
    return base.name in NEVER_EDITABLE or base.suffix == ".pyz"


def _detail(error: subprocess.CalledProcessError) -> str:
    """Return the text that a failed subprocess printed, or the error itself."""
    for stream in (cast(object, error.stderr), cast(object, error.stdout)):
        if isinstance(stream, str) and stream.strip():
            return stream
        if isinstance(stream, bytes) and stream.strip():
            return stream.decode("utf-8", errors="replace")
    return str(error)


class BuildFailed(CodedError):
    """The host build of an own target failed. The candidate is rejected."""

    def __init__(self, target: str, detail: str) -> None:
        super().__init__("build-failed", f"target {target}: {detail[-_DETAIL_LIMIT:].strip()}")


def layer_order(targets: Sequence[Site]) -> list[LayerId]:
    """Return the distinct layer ids of `targets` in one fixed order, so a resumed run finds each layer again."""
    return sorted({target.layer_id for target in targets}, key=lambda layer: (str(layer[0]), layer[1]))


@dataclass(frozen=True)
class Site:
    """One target of the skill's `wedge.toml`, resolved for a build. It holds no bytes."""

    config: WedgeConfig
    paths: BuildPaths
    pyz: str
    repo: Path | None

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def layer_id(self) -> LayerId:
        return (self.paths.project, tuple(sorted(set(self.config.groups))))

    def project_relative(self, name: str) -> PurePosixPath:
        """Return the path of the `WEDGE_PREFIX` file `name` relative to the target's project."""
        assert self.repo is not None
        return PurePosixPath(name.removeprefix(WEDGE_PREFIX)).relative_to(self.paths.project.relative_to(self.repo).as_posix())


@dataclass(frozen=True)
class OwnTarget(Site):
    """A `Site` with its built `.pyz` bytes and its first-party files.

    `sources` maps `WEDGE_PREFIX` names to text. It is None when the sources cannot be edited: the project is
    outside the repository root, a source file is binary, is not UTF-8, is over the text limit, or has a name
    that `relative` refuses, or git ignores a first-party file.
    `fixed` holds the first-party files that no proposal edits, such as a data `.pyz`. A rebuild stages them
    from the seed. `names` holds every first-party file of the target.
    """

    built: bytes
    sources: dict[str, str] | None
    fixed: dict[str, bytes]
    names: frozenset[str]


def _repo_names(paths: BuildPaths, repo: Path | None) -> frozenset[str]:
    """Return the `WEDGE_PREFIX` names of every first-party file of a target. Return an empty set when unknown."""
    if repo is None:
        return frozenset()
    try:
        return frozenset(WEDGE_PREFIX + file.relative_to(repo).as_posix() for file in first_party_files(paths))
    except (ValueError, OSError):
        return frozenset()


def _scan(paths: BuildPaths, repo: Path | None) -> tuple[frozenset[str], dict[str, str] | None, dict[str, bytes]]:
    """Return the first-party names, the editable sources (None when they cannot be edited), and the fixed files."""
    if repo is None or not (paths.project == repo or repo in paths.project.parents):
        return frozenset(), None, {}
    try:
        files = first_party_files(paths)
        names = frozenset(WEDGE_PREFIX + file.relative_to(repo).as_posix() for file in files)
    except (ValueError, OSError):
        return frozenset(), None, {}
    ignored = ignored_files(paths.project)
    found: dict[str, str] = {}
    fixed: dict[str, bytes] = {}
    for file in files:
        name = WEDGE_PREFIX + file.relative_to(repo).as_posix()
        if file.is_relative_to(paths.project) and file.relative_to(paths.project).as_posix() in ignored:
            return names, None, {}
        data = file.read_bytes()
        if never_editable(file.name):
            if len(data) > FROZEN_FILE_LIMIT:
                return names, None, {}
            fixed[name] = data
            continue
        text = as_text(data)
        if text is None:
            return names, None, {}
        try:
            _ = relative(name)
        except ValueError:
            return names, None, {}
        found[name] = text
    return names, found, fixed


def config_hash(skill_root: Path) -> str:
    """Return a digest of the `wedge.toml` files that define the skill's targets."""
    shared = defaults_path(skill_root)
    parts = [(skill_root / "wedge.toml").read_bytes()] + ([shared.read_bytes()] if shared is not None else [])
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


def load_sites(skill_root: Path) -> list[Site]:
    """Return the targets of `<skill_root>/wedge.toml`, resolved. Return [] without a `wedge.toml`. Read no `.pyz`."""
    root = skill_root.resolve()
    if not (root / "wedge.toml").is_file():
        return []
    try:
        configs = load_targets(root)
        resolved = [(config, resolve_target(root, config)) for config in configs]
    except (ConfigError, ValueError, OSError) as error:
        raise CodedError("wedge-config-invalid", f"{root / 'wedge.toml'} is not a valid wedge config: {error}") from None
    repo = repo_root(root)
    return [Site(config, paths, bundle_path(Path(), config.name).as_posix(), repo) for config, paths in resolved]


def own_targets(skill_root: Path) -> tuple[OwnTarget, ...]:
    """Return the targets of `<skill_root>/wedge.toml`, each with its built `.pyz` bytes. Return () without a `wedge.toml`."""
    root = skill_root.resolve()
    targets: list[OwnTarget] = []
    for site in load_sites(root):
        file = bundle_path(root, site.name)
        if file.is_symlink() or not file.is_file():
            raise CodedError("target-not-built", f"{site.pyz} is missing; run `wedge bundle` for the skill, then run again")
        names, sources, fixed = _scan(site.paths, site.repo)
        targets.append(OwnTarget(site.config, site.paths, site.pyz, site.repo, file.read_bytes(), sources, fixed, names))
    return tuple(targets)


def _nested_names(root: Path, repo: Path | None) -> frozenset[str]:
    """Return the first-party names of every target in a `wedge.toml` below `root`. Another skill builds from them."""
    found: set[str] = set()
    for config_file in sorted(root.rglob("wedge.toml")):
        directory = config_file.parent
        if directory == root:
            continue
        try:
            for config in load_targets(directory):
                found |= _repo_names(resolve_target(directory, config), repo)
        except (ConfigError, ValueError, OSError):
            continue
    return frozenset(found)


@dataclass(frozen=True)
class TargetPlan:
    """The own targets of a skill, the ones whose sources the search edits in order, and why each other one is frozen."""

    targets: tuple[OwnTarget, ...] = ()
    editable: tuple[OwnTarget, ...] = ()
    reasons: Mapping[str, str] = field(default_factory=dict[str, str])

    @property
    def frozen(self) -> dict[str, bytes]:
        found = {target.pyz: target.built for target in self.targets}
        for target in self.editable:
            found |= target.fixed
        return found

    @property
    def sources(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for target in self.editable:
            found |= target.sources or {}
        return found

    def helper_target(self, contract: Contract) -> OwnTarget | None:
        """Return the target whose `.pyz` the contract helper names."""
        if contract.helper is None:
            return None
        return next((target for target in self.targets if target.pyz == contract.helper.path), None)

    def layer_ids(self) -> list[LayerId]:
        return layer_order(self.editable)

    def record(self) -> dict[str, object]:
        """Return the run record of the plan: the source names and the fixed file names of each editable target.

        It also holds the reason that each other own target is frozen.
        """
        return {"editable": {target.name: sorted(target.sources or {}) for target in self.editable},
                "fixed": {target.name: sorted(target.fixed) for target in self.editable},
                "frozen": dict(self.reasons)}


def _frozen_reason(target: OwnTarget, offered: set[str]) -> str:
    """Return why `target` stays frozen. `offered` holds the targets that the helper or SKILL.md names."""
    if target.sources is None:
        return ("its sources cannot be edited: the project is outside the repository root, or a first-party file is "
                + "binary, not UTF-8, over the text limit, or git-ignored")
    if target.name not in offered:
        return f"neither the contract helper nor SKILL.md names scripts/{target.name}.pyz"
    return "its wedge sources are shared with a frozen or nested target, or they exceed the size limits"


def _fit(stage: Sequence[OwnTarget], blocked: frozenset[str], text: int) -> list[OwnTarget]:
    """Return the targets of `stage` that fit, with their sources less the `blocked` names.

    A target left with no source is frozen. The first target that does not fit, and every later one, stays frozen.
    A blocked source stays in the build as a fixed file.
    """
    chosen: list[OwnTarget] = []
    taken: dict[str, str] = {}
    wedge = 0
    for target in stage:
        sources = target.sources or {}
        mine = {name: source for name, source in sources.items() if name not in blocked}
        if not mine:
            continue
        size = sum(len(source) for name, source in mine.items() if name not in taken)
        if text + wedge + size > PACKAGE_LIMIT or wedge + size > WEDGE_EDIT_LIMIT:
            break
        wedge += size
        taken |= mine
        fixed = target.fixed | {name: source.encode("utf-8") for name, source in sources.items() if name in blocked}
        chosen.append(replace(target, sources=mine, fixed=fixed))
    return chosen


def plan_targets(skill_root: Path, files: Mapping[str, str], contract: Contract, edit: str) -> TargetPlan:
    """Choose the editable targets: the helper's target, then the targets that SKILL.md names.

    Only `prose+cli` edits sources. Targets join in order while the text total stays within `PACKAGE_LIMIT`
    and the wedge source total stays within `WEDGE_EDIT_LIMIT`. The first target that does not fit, and every
    later one, stays frozen built bytes. A source that a frozen target or a nested skill target also builds
    from is never editable, and a target left with no source is frozen. The plan records why each target is frozen.
    """
    targets = own_targets(skill_root)
    if edit != "prose+cli":
        return TargetPlan(targets, (), {target.name: f"--edit {edit} keeps every own target frozen" for target in targets})
    if not targets:
        return TargetPlan()
    guide = files.get("SKILL.md", "")
    helper = TargetPlan(targets).helper_target(contract)
    named = [target for target in targets if target is not helper
             and re.search(rf"(?<![\w.-]){re.escape(target.name)}\.pyz(?![\w-]|\.\w)", guide)]
    offered = {target.name for target in ([helper] if helper is not None else []) + named}
    stage = [target for target in ([helper] if helper is not None else []) + named if target.sources is not None]
    nested = _nested_names(skill_root.resolve(), targets[0].repo)
    text = sum(len(source) for source in files.values())
    full = stage
    dropped: set[str] = set()
    while True:
        staged = {target.name for target in full}
        blocked = nested.union(*(target.names for target in targets if target.name not in staged or target.name in dropped))
        chosen = _fit(full, blocked, text)
        live = [target.name for target in full if target.name not in dropped]
        kept = {target.name for target in chosen}
        if [target.name for target in chosen] == live:
            reasons = {target.name: _frozen_reason(target, offered) for target in targets if target.name not in kept}
            return TargetPlan(targets, tuple(chosen), reasons)
        dropped |= set(live) - kept


def populate_layers(plan: TargetPlan, directory: Path) -> dict[LayerId, SiteLayer]:
    """Populate one site layer per project and group set of the editable targets.

    This is the only step that needs the network. `directory` holds the layers; a stale one is replaced.
    """
    if plan.editable and shutil.which("uv") is None:
        raise CodedError("site-layer-failed", "uv is not on PATH, and the site layer needs it; "
                         + "install uv (https://docs.astral.sh/uv/), then run again, or use --edit prose")
    shutil.rmtree(directory, ignore_errors=True)
    layers: dict[LayerId, SiteLayer] = {}
    for index, layer_id in enumerate(plan.layer_ids()):
        project, groups = layer_id
        try:
            layers[layer_id] = populate_site_layer(project, groups, directory / f"layer-{index}")
        except subprocess.CalledProcessError as error:
            raise CodedError("site-layer-failed", f"cannot populate the site layer for {project}: "
                             + f"{_detail(error)[-_DETAIL_LIMIT:].strip()}") from None
        except (ConfigError, ValueError, OSError) as error:
            raise CodedError("site-layer-failed", f"cannot populate the site layer for {project}: {error}") from None
    return layers


def open_layers(targets: Sequence[Site], directory: Path, keys: Sequence[str]) -> dict[LayerId, SiteLayer]:
    """Reopen the layers of a prepared run. Raise `site-layer-drift` when `uv.lock` or the groups changed."""
    ids = layer_order(targets)
    if len(ids) != len(keys):
        raise CodedError("site-layer-drift", "the site layers differ from the frozen record")
    layers: dict[LayerId, SiteLayer] = {}
    for index, (layer_id, key) in enumerate(zip(ids, keys)):
        try:
            layer = open_site_layer(layer_id[0], layer_id[1], directory / f"layer-{index}")
        except (ValueError, OSError) as error:
            raise CodedError("site-layer-drift", f"the site layer for {layer_id[0]} cannot be reused: {error}") from None
        if layer.key != key:
            raise CodedError("site-layer-drift", f"the site layer for {layer_id[0]} differs from the frozen record")
        layers[layer_id] = layer
    return layers


def seal(plan: TargetPlan, seed: Candidate, skill_root: Path, directory: Path) -> tuple[Candidate, dict[str, object]]:
    """Populate the layers, build the editable targets from the seed sources, and return the sealed seed and its record.

    The sealed seed holds the built bytes of each editable target in place of the `.pyz` on disk, which can be
    stale. The record is what `reopen` reads.
    """
    layers = populate_layers(plan, directory)
    sealed = seed
    if plan.editable:
        rebuilder = Rebuilder(seed, plan.editable, {t.name: sorted(t.sources or {}) for t in plan.editable},
                              {t.name: sorted(t.fixed) for t in plan.editable}, layers)
        sealed = seed.with_frozen(rebuilder.seed_build())
    keys = [layers[layer].key for layer in plan.layer_ids()]
    return sealed, plan.record() | {"layers": keys, "config_hash": config_hash(skill_root) if plan.targets else ""}


def reopen(skill_root: Path, owned: Mapping[str, object], seed: Candidate, directory: Path) -> Rebuilder | None:
    """Reopen the rebuilder of a sealed run from its record. Return None when no target has editable sources.

    Read the config and paths only: no built `.pyz` and no source file. Raise `site-layer-drift` when `uv.lock`,
    the groups, or `wedge.toml` changed, and `run-record-tampered` when the record is malformed.
    """
    editable = mapping(owned.get("editable", {}))
    if not editable:
        return None
    fixed = mapping(owned.get("fixed", {}))
    keys = owned.get("layers")
    if not isinstance(keys, list) or not all(isinstance(key, str) for key in cast(list[object], keys)):
        raise CodedError("run-record-tampered", "the recorded site layers are malformed; create a new run")
    names = {name: cast(list[str], sources) for name, sources in editable.items()}
    if any(not isinstance(sources, list) or not all(isinstance(file, str) and file in seed.files for file in cast(list[object], sources))
           for sources in editable.values()):
        raise CodedError("run-record-tampered", "a recorded editable file is not a seed file; create a new run")
    fixed_names = {name: cast(list[str], found) for name, found in fixed.items()}
    if any(name not in seed.frozen for found in fixed_names.values() for name in found):
        raise CodedError("run-record-tampered", "a recorded fixed wedge file is missing from the frozen files; create a new run")
    try:
        if config_hash(skill_root) != owned.get("config_hash"):
            raise CodedError("site-layer-drift", "the wedge.toml of the skill changed since the run began")
        sites = [site for site in load_sites(skill_root) if site.name in names]
        if len(sites) != len(names):
            raise CodedError("site-layer-drift", "the wedge targets of the skill changed since the run began")
        layers = open_layers(sites, directory, cast(list[str], keys))
    except OSError as error:
        raise CodedError("site-layer-drift", f"the wedge files of the skill cannot be read: {error}") from None
    return Rebuilder(seed, sites, names, fixed_names, layers)


@final
class Rebuilder:
    """Build the `.pyz` of each editable target whose sources a candidate changed, on the host.

    `names` maps each editable target to the `WEDGE_PREFIX` names of its sources in the seed. `fixed` maps it to
    the names of its other first-party files, whose bytes the seed holds as frozen files. The build stages only
    those files, never the live checkout, and shivs them over the site layer. The last few results stay cached by
    content. One lock per content key makes concurrent builds of the same content run once.
    """

    def __init__(self, seed: Candidate, targets: Sequence[Site], names: Mapping[str, Sequence[str]],
                 fixed: Mapping[str, Sequence[str]], layers: Mapping[LayerId, SiteLayer]) -> None:
        self.seed: Candidate = seed
        self.targets: list[Site] = [target for target in targets if target.name in names]
        self.names: dict[str, tuple[str, ...]] = {name: tuple(sources) for name, sources in names.items()}
        self.fixed: dict[str, tuple[str, ...]] = {name: tuple(found) for name, found in fixed.items()}
        self.layers: Mapping[LayerId, SiteLayer] = layers
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._failures: dict[str, BuildFailed] = {}
        self._building: dict[str, threading.Lock] = {}
        self._lock: threading.Lock = threading.Lock()

    def seed_build(self) -> dict[str, bytes]:
        """Return the built bytes of every editable target from the seed sources. Raise `BuildFailed` on failure."""
        return {target.pyz: self._build(target, self.seed) for target in self.targets}

    def apply(self, candidate: Candidate) -> Candidate:
        """Return `candidate` with the built bytes of every target that it changed. Raise `BuildFailed` on failure."""
        built: dict[str, bytes] = {}
        for target in self.targets:
            if all(candidate.files[name] == self.seed.files[name] for name in self.names[target.name]):
                continue
            built[target.pyz] = self._build(target, candidate)
        return candidate.with_frozen(built) if built else candidate

    def _cached(self, key: str) -> bytes | None:
        with self._lock:
            data = self._cache.get(key)
            if data is not None:
                self._cache.move_to_end(key)
            return data

    def _build(self, target: Site, candidate: Candidate) -> bytes:
        sources = {name: candidate.files[name] for name in self.names[target.name]}
        key = digest({"target": target.name, "sources": sources})
        done = self._finished(key)
        if done is not None:
            return done
        with self._lock:
            building = self._building.setdefault(key, threading.Lock())
        with building:
            done = self._finished(key)
            if done is not None:
                return done
            try:
                data = self._run(target, candidate, sources)
            except BuildFailed as error:
                with self._lock:
                    self._failures[key] = error
                    _ = self._building.pop(key, None)
                raise
            except BaseException:
                with self._lock:
                    _ = self._building.pop(key, None)
                raise
            with self._lock:
                self._cache[key] = data
                while len(self._cache) > _CACHE_ENTRIES:
                    _ = self._cache.popitem(last=False)
                _ = self._building.pop(key, None)
            return data

    def _finished(self, key: str) -> bytes | None:
        """Return the cached bytes of `key`, or raise the `BuildFailed` that an earlier build of it raised."""
        with self._lock:
            failure = self._failures.get(key)
            if failure is not None:
                raise failure
        return self._cached(key)

    def _run(self, target: Site, candidate: Candidate, sources: Mapping[str, str]) -> bytes:
        with tempfile.TemporaryDirectory(prefix="skillz-wedge-build-") as directory:
            root = Path(directory)
            try:
                for name, text in sources.items():
                    self._stage(target, root, name, text.encode("utf-8"))
                for name in self.fixed.get(target.name, ()):
                    self._stage(target, root, name, candidate.frozen[name])
                (root / "overlay").mkdir(exist_ok=True)
                staged = stage_sources(target.paths, root / "stage", root / "overlay", overlay_only=True)
                pyz = build_from_layer(self.layers[target.layer_id], staged, target.config, root / "out")
                return pyz.read_bytes()
            except subprocess.CalledProcessError as error:
                raise BuildFailed(target.name, _detail(error)) from None
            except (ConfigError, ValueError, OSError) as error:
                raise BuildFailed(target.name, str(error)) from None

    @staticmethod
    def _stage(target: Site, root: Path, name: str, data: bytes) -> None:
        path = root / "overlay" / target.project_relative(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(data)
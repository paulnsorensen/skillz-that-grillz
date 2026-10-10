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
import tomllib
from collections import OrderedDict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import cast, final

from wedge._build import (SiteLayer, build_from_layer, layer_import_names, open_site_layer, populate_site_layer,
                          resolve_target, stage_sources)
from wedge._bundle import bundle_path
from wedge._config import ConfigError, WedgeConfig, defaults_path, load_targets, parse_targets
from wedge._key import BuildPaths, first_party_files

from skillz_experiments._candidate import (FROZEN_FILE_LIMIT, NEW_CLI, PACKAGE_LIMIT, WEDGE_PREFIX, Candidate, as_text,
                                           ignored_files, is_skill_file)
from skillz_experiments._cases import CodedError, digest, mapping, relative, repo_root
from skillz_experiments._contract import Contract
from skillz_experiments._new_cli import expand_files, fromargs_include, inherits_source_paths

# A target never offers these as editable sources.
NEVER_EDITABLE = frozenset({"wedge.toml", "uv.lock"})
# The editable wedge sources of a run together stay within this many characters. A reflection echoes every one.
WEDGE_EDIT_LIMIT = 100_000
LayerId = tuple[Path, tuple[str, ...]]
_DETAIL_LIMIT = 600
# The rebuilder keeps this many built results.
_CACHE_ENTRIES = 4
# A generated `@new-cli` target builds on a layer that holds these top-level packages. The fromargs package itself
# comes from the first-party include, so it must not sit in the layer (the build rejects a shadowed name).
_NEW_CLI_LAYER_NAMES = frozenset({"cyclopts"})
_UNEDITABLE = ("its sources cannot be edited: the project is outside the repository root, or a first-party file is "
               + "binary, not UTF-8, over the text limit, or git-ignored")


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


def layer_order(layer_ids: Iterable[LayerId]) -> list[LayerId]:
    """Return the distinct `layer_ids` in one fixed order, so a resumed run finds each layer again."""
    return sorted(set(layer_ids), key=lambda layer: (str(layer[0]), layer[1]))


def new_cli_layer(site: Site) -> LayerId:
    """Return the layer of a generated `@new-cli` target: the project of `site` with no extra dependency group."""
    return (site.paths.project, ())


@dataclass(frozen=True)
class Site:
    """One target of a `wedge.toml`, resolved for a build. It holds no bytes.

    The config can come from any `wedge.toml` text. The source path of a generated target need not exist.
    """

    config: WedgeConfig
    paths: BuildPaths
    pyz: str
    repo: Path | None

    @property
    def skill_root(self) -> Path:
        return self.paths.config_file.parent

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def layer_id(self) -> LayerId:
        return (self.paths.project, tuple(sorted(set(self.config.groups))))

    def project_relative(self, name: str) -> PurePosixPath:
        """Return the path of the source `name` relative to the target's project.

        `name` is either a `WEDGE_PREFIX` name, relative to the repository root, or a skill file name.
        """
        if not name.startswith(WEDGE_PREFIX):
            return PurePosixPath((self.skill_root / name).relative_to(self.paths.project).as_posix())
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


def specs_from_text(skill_root: Path, text: str | None) -> list[Site]:
    """Return the targets that the `wedge.toml` content `text` defines for the skill, resolved. Read no `.pyz`.

    None reads `<skill_root>/wedge.toml`. The disk supplies the project, the includes, and the shared defaults.
    A source or include that does not exist is fine when `text` is given.
    """
    root = skill_root.resolve()
    try:
        configs = load_targets(root) if text is None else parse_targets(root, text)
        resolved = [(config, resolve_target(root, config, missing_ok=text is not None)) for config in configs]
    except (ConfigError, ValueError, OSError) as error:
        raise CodedError("wedge-config-invalid", f"{root / 'wedge.toml'} is not a valid wedge config: {error}") from None
    repo = repo_root(root)
    return [Site(config, paths, bundle_path(Path(), config.name).as_posix(), repo) for config, paths in resolved]


def load_sites(skill_root: Path) -> list[Site]:
    """Return the targets of `<skill_root>/wedge.toml`, resolved. Return [] without a `wedge.toml`. Read no `.pyz`."""
    root = skill_root.resolve()
    if not (root / "wedge.toml").is_file():
        return []
    return specs_from_text(root, None)


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


@dataclass(frozen=True)
class TargetAudit:
    """One own target for `audit-facts`: its name, and why a `prose+cli` run keeps it frozen ("" when it is editable)."""

    name: str
    frozen: str


@dataclass(frozen=True)
class WedgeAudit:
    """The wedge facts of a skill: its own targets, its foreign `.pyz` files, and why the checks do not apply ("" if they do)."""

    targets: tuple[TargetAudit, ...]
    foreign: tuple[str, ...]
    inapplicable: str = ""


def _project_gap(root: Path) -> str:
    """Return why the project of `<root>/wedge.toml` is missing, or "". Raise `CodedError` for an invalid `wedge.toml`."""
    try:
        configs = load_targets(root)
    except (ConfigError, ValueError, OSError) as error:
        raise CodedError("wedge-config-invalid", f"{root / 'wedge.toml'} is not a valid wedge config: {error}") from None
    for config in configs:
        project = (root / config.project).resolve()
        for required in ("pyproject.toml", "uv.lock"):
            if not (project / required).is_file():
                return f"the wedge project {config.project!r} has no {required}, such as in an installed copy of the skill"
    return ""


def _lower_bundles(root: Path) -> set[Path]:
    """Return the `.pyz` paths that a `wedge.toml` below `root` builds, when its directory has no `SKILL.md`."""
    found: set[Path] = set()
    for config_file in sorted(root.rglob("wedge.toml")):
        directory = config_file.parent
        if directory == root or (directory / "SKILL.md").exists():
            continue
        try:
            found |= {bundle_path(directory, config.name) for config in load_targets(directory)}
        except (ConfigError, ValueError, OSError):
            continue
    return found


def audit_targets(skill_root: Path) -> WedgeAudit:
    """Return the wedge facts of a skill. Read the config and the first-party files only. Build nothing.

    The edit limit follows the run planner: `_scan` for the sources, and `_choose` for each target alone, as if a run offers only it. A foreign `.pyz` is a
    path relative to the skill root that no own config builds. A `.pyz` that a lower `wedge.toml` builds is own
    when that directory has no `SKILL.md`. Raise `CodedError` for an invalid `wedge.toml` or an unreadable source.
    """
    root = skill_root.resolve()
    if (root / "wedge.toml").is_file():
        gap = _project_gap(root)
        if gap:
            return WedgeAudit((), (), gap)
    scanned: list[OwnTarget] = []
    sites = load_sites(root)
    for site in sites:
        try:
            _ = first_party_files(site.paths)
            names, sources, fixed = _scan(site.paths, site.repo)
        except (ValueError, OSError) as error:
            raise CodedError("wedge-source-unreadable", f"target {site.name}: cannot read its sources: {error}") from None
        scanned.append(OwnTarget(site.config, site.paths, site.pyz, site.repo, b"", sources, fixed, names))
    nested = _nested_names(root, sites[0].repo) if sites else frozenset[str]()
    offered = {target.name for target in scanned}
    chosen = {target.name for target in scanned if target.sources is not None and _choose(scanned, [target], nested, 0)}
    blocked = nested.union(*(target.names for target in scanned if target.sources is None))
    audits: list[TargetAudit] = []
    for target in scanned:
        size = sum(len(text) for name, text in (target.sources or {}).items() if name not in blocked)
        if target.name in chosen:
            audits.append(TargetAudit(target.name, ""))
        elif target.sources is not None and size > WEDGE_EDIT_LIMIT:
            audits.append(TargetAudit(target.name, f"{size} characters of sources do not fit the wedge edit limit of {WEDGE_EDIT_LIMIT}"))
        else:
            audits.append(TargetAudit(target.name, _frozen_reason(target, offered)))
    built = {bundle_path(root, site.name) for site in sites} | _lower_bundles(root)
    foreign = sorted(file.relative_to(root).as_posix() for file in root.rglob("*.pyz") if file.is_file() and file not in built)
    return WedgeAudit(tuple(audits), tuple(foreign))


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
class FromargsCapability:
    """The skill can take a `@new-cli` target: `site` is the target that includes the fromargs package.

    `fixed` holds the first-party files of that include, by `WEDGE_PREFIX` name. A generated target stages them.
    """

    site: Site
    fixed: dict[str, bytes]


@dataclass(frozen=True)
class TargetPlan:
    """The own targets of a skill, the ones whose sources the search edits in order, and why each other one is frozen.

    `capable` is set when the skill can take a `@new-cli` target.
    """

    targets: tuple[OwnTarget, ...] = ()
    editable: tuple[OwnTarget, ...] = ()
    reasons: Mapping[str, str] = field(default_factory=dict[str, str])
    capable: FromargsCapability | None = None
    dropped: str = ""

    @property
    def frozen(self) -> dict[str, bytes]:
        found = {target.pyz: target.built for target in self.targets}
        for target in self.editable:
            found |= target.fixed
        if self.capable is not None:
            found |= {name: data for name, data in self.capable.fixed.items() if name not in self.sources}
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
        return layer_order([*(target.layer_id for target in self.editable),
                            *([new_cli_layer(self.capable.site)] if self.capable is not None else [])])

    def record(self) -> dict[str, object]:
        """Return the run record of the plan: the source names and the fixed file names of each editable target.

        It also holds the reason that each other own target is frozen, and the include files of a `@new-cli` target.
        """
        found: dict[str, object] = {"editable": {target.name: sorted(target.sources or {}) for target in self.editable},
                                    "fixed": {target.name: sorted(target.fixed) for target in self.editable},
                                    "frozen": dict(self.reasons)}
        if self.capable is not None:
            found["new_cli"] = {"fixed": sorted(self.capable.fixed)}
        if self.dropped:
            found["new_cli_dropped"] = self.dropped
        return found


def _provides_cyclopts(lock: Path) -> bool:
    """Return whether the `uv.lock` file `lock` lists the cyclopts package."""
    try:
        data = cast(dict[str, object], tomllib.loads(lock.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    packages = data.get("package")
    if not isinstance(packages, list):
        return False
    names = {str(cast(dict[str, object], item).get("name", "")).lower().replace("_", "-")
             for item in cast(list[object], packages) if isinstance(item, dict)}
    return "cyclopts" in names


def _capability(skill_root: Path, targets: Sequence[Site], files: Mapping[str, str]) -> FromargsCapability | None:
    """Return the `@new-cli` capability, or None.

    The skill needs a `wedge.toml` whose lock provides cyclopts, and a target that includes the fromargs package.
    The skill directory lies inside that target's project, because a new source lives under `src/` in the skill.
    The project lies inside the repository root, so the include files have repository-relative names.
    The new table must not inherit a shared `source_paths`. The lock check is a cheap pre-check; `settle`
    then requires fromargs and cyclopts in the populated layer.
    """
    found = fromargs_include([target.config for target in targets])
    if found is None or "wedge.toml" not in files:
        return None
    site = next(target for target in targets if target.config is found[0])
    paths, root = site.paths, skill_root.resolve()
    if site.repo is None or not (root == paths.project or paths.project in root.parents):
        return None
    if not (paths.project == site.repo or site.repo in paths.project.parents) or not _provides_cyclopts(paths.uv_lock):
        return None
    if inherits_source_paths(root, files["wedge.toml"]):
        return None
    include = paths.includes[site.config.include.index(found[1])]
    fixed: dict[str, bytes] = {}
    try:
        for file in first_party_files(BuildPaths(paths.config_file, paths.project, include, (include,))):
            name = relative(WEDGE_PREFIX + file.relative_to(site.repo).as_posix())
            data = file.read_bytes()
            if len(data) > FROZEN_FILE_LIMIT:
                return None
            fixed[name] = data
    except (ValueError, OSError):
        return None
    return FromargsCapability(site, fixed)


def _frozen_reason(target: OwnTarget, offered: set[str]) -> str:
    """Return why `target` stays frozen. `offered` holds the targets that the helper or SKILL.md names."""
    if target.sources is None:
        return _UNEDITABLE
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
    named = [target for target in targets if target is not helper and _named(target, guide)]
    offered = {target.name for target in ([helper] if helper is not None else []) + named}
    stage = [target for target in ([helper] if helper is not None else []) + named if target.sources is not None]
    nested = _nested_names(skill_root.resolve(), targets[0].repo)
    chosen = _choose(targets, stage, nested, sum(len(source) for source in files.values()))
    kept = {target.name for target in chosen}
    reasons = {target.name: _frozen_reason(target, offered) for target in targets if target.name not in kept}
    return TargetPlan(targets, tuple(chosen), reasons, _capability(skill_root, targets, files))


def _named(target: OwnTarget, guide: str) -> bool:
    """Return whether the SKILL.md text `guide` names `scripts/<target>.pyz`."""
    return re.search(rf"(?<![\w.-]){re.escape(target.name)}\.pyz(?![\w-]|\.\w)", guide) is not None


def _choose(targets: Sequence[OwnTarget], stage: Sequence[OwnTarget], nested: frozenset[str], text: int) -> list[OwnTarget]:
    """Return the staged targets that stay editable. Drop each target that does not fit, then fit the rest again."""
    dropped: set[str] = set()
    while True:
        staged = {target.name for target in stage}
        blocked = nested.union(*(target.names for target in targets if target.name not in staged or target.name in dropped))
        chosen = _fit(stage, blocked, text)
        live = [target.name for target in stage if target.name not in dropped]
        if [target.name for target in chosen] == live:
            return chosen
        dropped |= set(live) - {target.name for target in chosen}


def populate_layers(plan: TargetPlan, directory: Path) -> dict[LayerId, SiteLayer]:
    """Populate one site layer per project and group set of the editable targets.

    This is the only step that needs the network. `directory` holds the layers; a stale one is replaced.
    """
    if (plan.editable or plan.capable is not None) and shutil.which("uv") is None:
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


def open_layers(layer_ids: Iterable[LayerId], directory: Path, keys: Sequence[str]) -> dict[LayerId, SiteLayer]:
    """Reopen the layers of a prepared run. Raise `site-layer-drift` when `uv.lock` or the groups changed."""
    ids = layer_order(layer_ids)
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


def settle(plan: TargetPlan, directory: Path) -> tuple[TargetPlan, dict[LayerId, SiteLayer]]:
    """Populate the layers of `plan`. Drop the `@new-cli` capability when its layer fails or lacks cyclopts.

    A layer that an editable target needs fails hard. A layer that only the capability needs is optional.
    """
    capable = plan.capable
    optional = capable is not None and new_cli_layer(capable.site) not in {target.layer_id for target in plan.editable}
    try:
        layers = populate_layers(plan, directory)
    except CodedError as error:
        if not optional:
            raise
        plan = replace(plan, capable=None, dropped=f"the fromargs site layer failed: {error}")
        return plan, populate_layers(plan, directory)
    if capable is None:
        return plan, layers
    try:
        complete = _NEW_CLI_LAYER_NAMES <= layer_import_names(layers[new_cli_layer(capable.site)])
    except OSError:
        complete = False
    if complete:
        return plan, layers
    plan = replace(plan, capable=None, dropped="the fromargs site layer lacks cyclopts")
    return plan, populate_layers(plan, directory) if optional else layers


def seal(plan: TargetPlan, seed: Candidate, skill_root: Path,
         layers: Mapping[LayerId, SiteLayer]) -> tuple[Candidate, dict[str, object]]:
    """Build the editable targets from the seed sources on `layers`, and return the sealed seed and its record.

    The sealed seed holds the built bytes of each editable target in place of the `.pyz` on disk, which can be
    stale. The record is what `reopen` reads.
    """
    sealed = seed
    if plan.editable:
        rebuilder = Rebuilder(seed, plan.editable, {t.name: sorted(t.sources or {}) for t in plan.editable},
                              {t.name: sorted(t.fixed) for t in plan.editable}, layers)
        sealed = seed.with_frozen(rebuilder.seed_build())
    keys = [layers[layer].key for layer in plan.layer_ids()]
    return sealed, plan.record() | {"layers": keys, "config_hash": config_hash(skill_root) if plan.targets else ""}


def reopen(skill_root: Path, owned: Mapping[str, object], seed: Candidate, directory: Path) -> Rebuilder | None:
    """Reopen the rebuilder of a sealed run from its record.

    Return None when no target has editable sources and the skill offers no `@new-cli` target.
    Read the config and paths only: no built `.pyz` and no source file. Raise `site-layer-drift` when `uv.lock`,
    the groups, or `wedge.toml` changed, and `run-record-tampered` when the record is malformed.
    """
    editable = mapping(owned.get("editable", {}))
    new_cli = owned.get("new_cli")
    if not editable and new_cli is None:
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
    raw: object = mapping(new_cli).get("fixed", []) if new_cli is not None else []
    if not isinstance(raw, list) or any(not isinstance(name, str) or (name not in seed.frozen and name not in seed.files)
                                        for name in cast(list[object], raw)):
        raise CodedError("run-record-tampered", "a recorded include file of the new target is missing from the seed; create a new run")
    generated = cast(list[str], raw)
    try:
        if config_hash(skill_root) != owned.get("config_hash"):
            raise CodedError("site-layer-drift", "the wedge.toml of the skill changed since the run began")
        found = load_sites(skill_root)
        sites = [site for site in found if site.name in names]
        if len(sites) != len(names):
            raise CodedError("site-layer-drift", "the wedge targets of the skill changed since the run began")
        capable: Site | None = None
        if new_cli is not None:
            base = fromargs_include([site.config for site in found])
            if base is None:
                raise CodedError("site-layer-drift", "the wedge targets of the skill changed since the run began")
            capable = next(site for site in found if site.config is base[0])
        layers = open_layers([*(site.layer_id for site in sites), *([new_cli_layer(capable)] if capable is not None else [])],
                             directory, cast(list[str], keys))
        layer_names = layer_import_names(layers[new_cli_layer(capable)]) if capable is not None else frozenset[str]()
    except OSError as error:
        raise CodedError("site-layer-drift", f"the wedge files of the skill cannot be read: {error}") from None
    return Rebuilder(seed, sites, names, fixed_names, layers, skill_root=skill_root, capable=capable, generated=generated,
                     layer_names=layer_names)


@final
class Rebuilder:
    """Build the `.pyz` of each editable target whose sources a candidate changed, on the host.

    `names` maps each editable target to the `WEDGE_PREFIX` names of its sources in the seed. `fixed` maps it to
    the names of its other first-party files, whose bytes the seed holds as frozen files. The build stages only
    those files, never the live checkout, and shivs them over the site layer. The last few results stay cached by
    content. One lock per content key makes concurrent builds of the same content run once.
    """

    def __init__(self, seed: Candidate, targets: Sequence[Site], names: Mapping[str, Sequence[str]],
                 fixed: Mapping[str, Sequence[str]], layers: Mapping[LayerId, SiteLayer], *,
                 skill_root: Path | None = None, capable: Site | None = None, generated: Sequence[str] = (),
                 layer_names: Collection[str] = ()) -> None:
        self.seed: Candidate = seed
        self.targets: list[Site] = [target for target in targets if target.name in names]
        self.names: dict[str, tuple[str, ...]] = {name: tuple(sources) for name, sources in names.items()}
        self.fixed: dict[str, tuple[str, ...]] = {name: tuple(found) for name, found in fixed.items()}
        self.layers: Mapping[LayerId, SiteLayer] = layers
        self.root: Path | None = skill_root.resolve() if skill_root is not None else None
        self.capable: Site | None = capable
        self.generated: tuple[str, ...] = tuple(generated)
        self.layer_names: frozenset[str] = frozenset(layer_names)
        self._specs: dict[str, list[Site]] = {}
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._failures: dict[str, BuildFailed] = {}
        self._building: dict[str, threading.Lock] = {}
        self._lock: threading.Lock = threading.Lock()

    def seed_build(self) -> dict[str, bytes]:
        """Return the built bytes of every editable target from the seed sources. Raise `BuildFailed` on failure."""
        return {target.pyz: self._build(target, self.seed, self.names[target.name], self.fixed.get(target.name, ()))
                for target in self.targets}

    def apply(self, candidate: Candidate) -> Candidate:
        """Return `candidate` with the built bytes of every target that it changed or adds.

        Expand `@new-cli` first. Raise `BuildFailed` or another `CodedError` on failure.
        """
        candidate = self._expand(candidate)
        built: dict[str, bytes] = {}
        for target in self.targets:
            if all(candidate.files[name] == self.seed.files.get(name) for name in self.names[target.name]):
                continue
            built[target.pyz] = self._build(target, candidate, self.names[target.name], self.fixed.get(target.name, ()))
        for spec in self._generated(candidate):
            root = self._root()
            names = sorted(name for name in candidate.files
                           if is_skill_file(name) and (root / name).is_relative_to(spec.paths.source))
            built[spec.pyz] = self._build(spec, candidate, names, self.generated)
        return candidate.with_frozen(built) if built else candidate

    def _root(self) -> Path:
        if self.root is None:
            raise CodedError("new-cli-unavailable", "the rebuilder has no skill root")
        return self.root

    def _expand(self, candidate: Candidate) -> Candidate:
        """Return `candidate` with the files that its `@new-cli` value adds. Raise a `CodedError` for a rejected value."""
        value = candidate.files.get(NEW_CLI, "")
        if not value.strip():
            return candidate
        if self.capable is None:
            raise CodedError("new-cli-unavailable", "this skill cannot take a new target")
        added = expand_files(self._root(), self.seed.files, value, self.layer_names)
        child = replace(candidate, files=candidate.files | added)
        child.__dict__["frozen_digests"] = candidate.frozen_digests
        return child

    def _specs_of(self, text: str) -> list[Site]:
        with self._lock:
            found = self._specs.get(text)
        if found is None:
            found = specs_from_text(self._root(), text)
            with self._lock:
                self._specs[text] = found
        return found

    def _generated(self, candidate: Candidate) -> list[Site]:
        """Return the targets that the `wedge.toml` of `candidate` adds to the seed's."""
        text, before = candidate.files.get("wedge.toml"), self.seed.files.get("wedge.toml")
        if self.root is None or text is None or before is None or text == before:
            return []
        old = {site.name for site in self._specs_of(before)}
        return [site for site in self._specs_of(text) if site.name not in old]

    def _cached(self, key: str) -> bytes | None:
        with self._lock:
            data = self._cache.get(key)
            if data is not None:
                self._cache.move_to_end(key)
            return data

    def _build(self, target: Site, candidate: Candidate, names: Sequence[str], fixed: Sequence[str]) -> bytes:
        sources = {name: candidate.files[name] for name in names}
        pinned: dict[str, bytes] = {}
        for name in fixed:
            data = candidate.frozen.get(name)
            if data is None and name in candidate.files:
                data = candidate.files[name].encode("utf-8")
            if data is None:
                raise BuildFailed(target.name, f"the fixed file {name} is in neither the frozen nor the text files")
            pinned[name] = data
        config = target.config
        key = digest({"target": target.name, "sources": sources,
                      "fixed": {name: hashlib.sha256(data).hexdigest() for name, data in pinned.items()},
                      "config": [config.entry, config.source, list(config.include), list(config.groups)]})
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
                data = self._run(target, sources, pinned)
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

    def _run(self, target: Site, sources: Mapping[str, str], fixed: Mapping[str, bytes]) -> bytes:
        layer = self.layers.get(target.layer_id)
        if layer is None:
            raise BuildFailed(target.name, "no site layer for the target")
        with tempfile.TemporaryDirectory(prefix="skillz-wedge-build-") as directory:
            root = Path(directory)
            try:
                for name, text in sources.items():
                    self._stage(target, root, name, text.encode("utf-8"))
                for name, data in fixed.items():
                    self._stage(target, root, name, data)
                (root / "overlay").mkdir(exist_ok=True)
                staged = stage_sources(target.paths, root / "stage", root / "overlay", overlay_only=True)
                pyz = build_from_layer(layer, staged, target.config, root / "out")
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
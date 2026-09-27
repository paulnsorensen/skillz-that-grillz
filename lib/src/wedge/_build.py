"""Build a reproducible .pyz for one skill's CLI.

Resolve and guard the project's third-party closure, install it plus the
configured local ``include`` trees and the skill's CLI source into a fresh
site directory, strip volatile install metadata, and shiv the result with a
fixed shebang and ``SOURCE_DATE_EPOCH``. The same key always gives the same
uncompressed contents, so the content digest (``wedge._digest``) matches on
every host even when two zlib builds compress those contents differently.
Third-party wheels download fresh on every build (``--no-cache``), so a
modified uv cache cannot change the contents. The digest names the asset.
"""

from __future__ import annotations

import os
from io import BytesIO
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from wedge._config import ConfigError, WedgeConfig, load_config
from wedge._digest import content_sha256
from wedge._fanout import Outcome, fan_out
from wedge._guard import guard_closure
from wedge._key import TARGET_PYTHON, compute_key, resolve_paths
from wedge._resolve import export_requirements, resolve_closure

_VOLATILE_INSTALL_FILES = {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}
# 1980-01-01T00:00:00Z: the earliest timestamp a zip member can hold. shiv's
# reproducible mode floors SOURCE_DATE_EPOCH=0 to this same value, but set it
# explicitly so the build does not depend on that floor.
_SOURCE_DATE_EPOCH = "315532800"


@dataclass(frozen=True)
class BuildResult:
    """The built ``.pyz``: its key, content digest, and where it landed on disk."""

    name: str
    key: str
    content_sha256: str
    path: Path


@dataclass(frozen=True)
class SiteInputs:
    """Everything that goes into a site directory; skills that share it share the site."""

    project: Path
    source: Path
    includes: tuple[Path, ...]
    groups: tuple[str, ...]


@dataclass(frozen=True)
class _Prepared:
    """One skill, resolved and keyed, ready to be shivved from a site directory."""

    skill_dir: Path
    config: WedgeConfig
    key: str
    site: SiteInputs


def _prepare(skill_dir: Path) -> _Prepared:
    skill_dir = Path(skill_dir).resolve()
    config = load_config(skill_dir)
    paths = resolve_paths(skill_dir, config)
    key = compute_key(skill_dir, config)
    site = SiteInputs(paths.project, paths.source, paths.includes, config.groups)
    return _Prepared(skill_dir, config, key, site)


def _populate_site(site: SiteInputs, site_dir: Path) -> None:
    """Install the closure and copy the local trees into an empty ``site_dir``."""
    requirements = export_requirements(site.project, site.groups)
    closure = resolve_closure(site.project, requirements)
    guard_closure(closure)
    site_dir.mkdir()
    if closure:
        _install_third_party(requirements, site_dir)
    for local in (*site.includes, site.source):
        _copy_source(local, site_dir)
    _strip_volatile(site_dir)


def _shiv_skill(prepared: _Prepared, site_dir: Path, out_dir: Path) -> BuildResult:
    """Shiv one skill from a populated site directory into ``out_dir``."""
    config = prepared.config
    with tempfile.TemporaryDirectory(prefix="wedge-build-") as tmp:
        built = Path(tmp) / f"{config.name}.pyz"
        _shiv(site_dir, config.entry, built)
        _canonicalize_archive(built)
        digest = content_sha256(built)
        out_path = out_dir / f"{config.name}-{digest[:12]}.pyz"
        _ = shutil.move(built, out_path)
    return BuildResult(name=config.name, key=prepared.key, content_sha256=digest, path=out_path)


def build(skill_dir: Path, out_dir: Path) -> BuildResult:
    """Build ``<out_dir>/<name>-<content_sha256[:12]>.pyz``; returns its key and digest."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    prepared = _prepare(skill_dir)
    with tempfile.TemporaryDirectory(prefix="wedge-site-") as tmp:
        site_dir = Path(tmp) / "site"
        _populate_site(prepared.site, site_dir)
        return _shiv_skill(prepared, site_dir, out_dir)


def build_many(
    skill_dirs: Sequence[Path], out_dir: Path, *, jobs: int | None = None
) -> list[Outcome[Path, BuildResult]]:
    """Build every skill into ``out_dir``, ``jobs`` at a time.

    Skills with the same project, trees, and groups share one site directory,
    so a repository of many skills over one package downloads and copies its
    closure once. Returns one outcome per skill in input order; a failure
    while populating a site fails every skill that shares it.
    """
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    prepared = fan_out(list(skill_dirs), _prepare, jobs=jobs)
    # Two skills with one name collapse into one entry of the name-keyed
    # result. Both fail instead; the rest still build.
    names = [p.value.config.name for p in prepared if p.value is not None]
    duplicates = {name for name in names if names.count(name) > 1}
    valid = [p.value for p in prepared if p.value is not None and p.value.config.name not in duplicates]
    sites = list(dict.fromkeys(p.site for p in valid))

    with tempfile.TemporaryDirectory(prefix="wedge-site-") as tmp:
        site_dirs = {site: Path(tmp) / f"site-{index}" for index, site in enumerate(sites)}
        populated = fan_out(sites, lambda site: _populate_site(site, site_dirs[site]), jobs=jobs)
        site_errors = {o.item: o.error for o in populated if o.error is not None}
        ready = [p for p in valid if p.site not in site_errors]
        shivved = fan_out(ready, lambda p: _shiv_skill(p, site_dirs[p.site], out_dir), jobs=jobs)
        built = {p.skill_dir: o for p, o in zip(ready, shivved)}

    outcomes: list[Outcome[Path, BuildResult]] = []
    for skill_dir, o in zip(skill_dirs, prepared):
        path = Path(skill_dir)
        if o.error is not None:
            outcomes.append(Outcome(path, error=o.error))
            continue
        assert o.value is not None
        if o.value.config.name in duplicates:
            outcomes.append(Outcome(path, error=f"duplicate skill name {o.value.config.name!r}"))
        elif o.value.site in site_errors:
            outcomes.append(Outcome(path, error=site_errors[o.value.site]))
        else:
            result = built[o.value.skill_dir]
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
                "--no-cache",
                "--require-hashes",
                "--no-deps",
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
    _ = shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def _copy_source(source: Path, site_dir: Path) -> None:
    if source.is_symlink():
        raise ValueError(f"build source must not be a symlink: {source}")
    destination = site_dir / source.name
    if destination.exists() or destination.is_symlink():
        raise ConfigError(f"build source would overwrite installed path: {destination}")
    if source.is_dir():
        _copy_tree(source, destination)
    else:
        _ = shutil.copy2(source, destination)


def _strip_volatile(site_dir: Path) -> None:
    # Installed console scripts embed the builder's absolute Python path.
    shutil.rmtree(site_dir / "bin", ignore_errors=True)
    for path in sorted(site_dir.rglob("*"), reverse=True):
        if path.is_dir():
            if path.name == "__pycache__":
                shutil.rmtree(path, ignore_errors=True)
        elif path.name in _VOLATILE_INSTALL_FILES or path.suffix == ".pyc":
            path.unlink()


def _shiv(site_dir: Path, entry: str, out_path: Path) -> None:
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = _SOURCE_DATE_EPOCH
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


def _canonicalize_archive(path: Path) -> None:
    """Sort, normalize, and deflate ZIP members.

    shiv does not sort its bundled bootstrap files. The deflate bytes can vary
    with the zlib build; the content digest ignores them.
    """
    original = path.read_bytes()
    shebang, separator, _ = original.partition(b"\n")
    if not separator or not shebang.startswith(b"#!"):
        raise ValueError(f"shiv output lacks a shebang: {path}")
    with ZipFile(BytesIO(original)) as source, path.open("wb") as output:
        _ = output.write(shebang + separator)
        with ZipFile(output, "w") as target:
            for info in sorted(source.infolist(), key=lambda item: item.filename):
                data = source.read(info)
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.create_system = 3
                is_dir = info.filename.endswith("/")
                mode = 0o40755 if is_dir else 0o100644
                info.external_attr = (mode << 16) | (0x10 if is_dir else 0)
                info.compress_type = ZIP_STORED if is_dir else ZIP_DEFLATED
                target.writestr(info, data)

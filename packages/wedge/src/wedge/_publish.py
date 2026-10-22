"""Publish each wedged skill's .pyz to the rolling ``wedge`` GitHub release.

Idempotent and race-safe: two runs (or two racing processes) that build the
same key converge on exactly one uploaded asset. An existing asset counts as
published only when this run's own build and the asset's downloaded content
digest both match the lock; the asset's compressed bytes may come from
another zlib build. Everything goes through the
``gh`` CLI so ``GH_TOKEN`` and auth stay in one place.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from wedge._build import BuildResult, build_many
from wedge._config import ConfigError, WedgeConfig, load_targets, multi_target_message
from wedge._digest import content_sha256
from wedge._fanout import fan_out
from wedge._lock import LockData, load_lock, lock_path
from wedge._release_store import RELEASE, GhReleaseStore, ReleaseStore, ReleaseStoreError, run_gh

__all__ = ["RELEASE", "publish"]


def _ensure_release(repo: str, target: str, store: ReleaseStore) -> None:
    """Make sure the rolling ``wedge`` prerelease exists, tolerating a race."""
    if store.view():
        return
    try:
        store.create(target)
    except ReleaseStoreError as exc:
        created_error = str(exc)
    else:
        return
    if store.view():
        return
    raise RuntimeError(f"cannot create or find release {RELEASE!r} in {repo}: {created_error}")


def _resolve_commit(skill_dir: Path, ref: str) -> str:
    """Resolve ``ref`` to a commit sha inside ``skill_dir``'s git checkout."""
    result = subprocess.run(
        ["git", "-C", str(skill_dir), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"cannot resolve {ref!r} in {skill_dir}: {result.stderr.strip()}")
    return result.stdout.strip()


def _require_on_branch(repo: str, branch: str, target: str, skill_dirs: list[Path]) -> None:
    """Refuse a target commit that ``branch`` does not contain.

    ``target`` must resolve to the same commit as every prepared skill's own
    ``HEAD``, so a publish never builds a tree the target does not describe.
    The compare API then answers without any local history, so a shallow
    checkout is enough: ``identical`` is the branch tip, ``behind`` an
    ancestor of it.
    """
    target_sha = _resolve_commit(skill_dirs[0], target)
    for skill_dir in skill_dirs:
        head_sha = _resolve_commit(skill_dir, "HEAD")
        if head_sha != target_sha:
            raise ValueError(
                f"target {target!r} resolves to {target_sha}, but {skill_dir} HEAD is {head_sha}"
            )
    result = run_gh(["api", f"repos/{repo}/compare/refs/heads/{branch}...{target_sha}", "--jq", ".status"])
    if result.returncode != 0:
        raise RuntimeError(f"cannot compare {target_sha} with {branch} in {repo}: {result.stderr}")
    status = result.stdout.strip()
    if status not in {"identical", "behind"}:
        raise ValueError(f"{target_sha} is not on {branch} in {repo} (compare status {status!r})")


def _published_digest(store: ReleaseStore, asset: str) -> str:
    """Download the asset and return its content digest; ``"invalid"`` if it is no archive."""
    with tempfile.TemporaryDirectory(prefix="wedge-digest-") as tmp:
        store.download(asset, Path(tmp))
        try:
            return content_sha256(Path(tmp) / asset)
        except Exception:  # any unreadable archive (bad zip, zlib, CRC) is a mismatch
            return "invalid"


def _verdict(lock_data: LockData, store: ReleaseStore, found: str) -> tuple[str, str]:
    published = _published_digest(store, lock_data.asset)
    if published == lock_data.content_sha256:
        return "skipped", f"asset {lock_data.asset} {found}"
    return (
        "failed",
        f"asset {lock_data.asset} has content digest {published}, lock wants {lock_data.content_sha256}",
    )


def _publish_built(
    lock_data: LockData, result: BuildResult, store: ReleaseStore, known_assets: set[str]
) -> tuple[str, str]:
    """Publish one freshly built skill; returns ``(status, reason)``.

    The caller builds every skill; this function compares that build with
    the lock before it trusts an asset. An asset uploaded from another host
    counts as published only after this host reproduces the same content.
    """
    if result.key != lock_data.key:
        return "failed", f"lock is stale: key {lock_data.key} != current {result.key}"
    if result.content_sha256 != lock_data.content_sha256:
        return (
            "failed",
            f"built content digest {result.content_sha256} != lock {lock_data.content_sha256}",
        )
    if lock_data.asset in known_assets:
        return _verdict(lock_data, store, "already published")
    try:
        store.upload(result.path)
    except ReleaseStoreError as exc:
        if lock_data.asset in store.list_assets():
            return _verdict(lock_data, store, "published by a racing run")
        return "failed", f"upload failed: {exc}"
    return "published", f"uploaded {lock_data.asset}"


def _load_publishable(
    skill_dirs: list[Path], repo: str
) -> tuple[dict[str, dict[str, str]], list[tuple[Path, WedgeConfig, LockData]]]:
    """Load each skill's targets and locks; returns failed results plus what is ready to build."""
    configs: list[tuple[Path, tuple[WedgeConfig, ...] | None, str | None]] = []
    for raw_skill in skill_dirs:
        skill_dir = Path(raw_skill)
        try:
            targets = load_targets(skill_dir)
        except (ConfigError, OSError) as exc:
            configs.append((skill_dir, None, str(exc)))
        else:
            configs.append((skill_dir, targets, None))
    valid = [(path, config) for path, targets, _error in configs if targets is not None for config in targets]
    names = [config.name for _path, config in valid]
    if len(names) != len(set(names)):
        raise ValueError("duplicate skill names are not publishable")
    if any(config.repo != repo for _path, config in valid):
        raise ValueError("publish repository differs from skill configuration")

    results: dict[str, dict[str, str]] = {}
    used_names = set(names)
    for index, (skill_dir, targets, error) in enumerate(configs):
        if targets is None:
            name = skill_dir.name
            while name in used_names:
                name = f"{skill_dir.name}-{index}"
                index += 1
            used_names.add(name)
            results[name] = {"status": "failed", "reason": error or "invalid configuration"}

    prepared: list[tuple[Path, WedgeConfig, LockData]] = []
    for skill_dir, config in valid:
        scripts = skill_dir / "scripts"
        if not lock_path(skill_dir, config.name).exists() and (scripts / f"{config.name}.pyz").is_file():
            # ADR-010 direct bundle: no release asset; `wedge bundle --check` verifies it.
            # This holds for each target of a `[[target]]` skill too.
            results[config.name] = {"status": "skipped", "reason": "direct bundle; no release asset"}
            continue
        if config.multi:
            # Release mode needs one lock per skill directory; tables have no lock shape yet.
            results[config.name] = {"status": "failed", "reason": multi_target_message(skill_dir)}
            continue
        try:
            lock_data = load_lock(skill_dir, config.name)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            results[config.name] = {"status": "failed", "reason": str(exc)}
            continue
        if lock_data.repo != config.repo:
            raise ValueError("lock repository differs from skill configuration")
        prepared.append((skill_dir, config, lock_data))
    return results, prepared


@dataclass(frozen=True)
class _Ready:
    """One skill whose build matched its key, ready to compare with its lock."""

    name: str
    lock_data: LockData
    build_result: BuildResult


def publish(
    skill_dirs: list[Path],
    *,
    repo: str,
    target: str,
    jobs: int | None = None,
    branch: str | None = None,
    store: ReleaseStore | None = None,
) -> dict[str, dict[str, str]]:
    """Publish every skill, ``jobs`` at a time; returns ``{name: {status, reason}}``.

    With ``branch``, refuse before any side effect unless ``target`` is on
    that branch. Skills that share a project and group set share one site
    layer, so a repository of many skills over one package downloads its closure once.
    """
    if not skill_dirs:
        return {}
    results, prepared = _load_publishable(skill_dirs, repo)
    if not prepared:
        return results
    if branch is not None:
        _require_on_branch(repo, branch, target, [skill_dir for skill_dir, _config, _lock in prepared])
    store = store or GhReleaseStore(repo)
    _ensure_release(repo, target, store)
    known_assets = store.list_assets()
    with tempfile.TemporaryDirectory(prefix="wedge-publish-") as tmp:
        built = build_many([skill_dir for skill_dir, _config, _lock in prepared], Path(tmp), jobs=jobs)
        ready: list[_Ready] = []
        for (_skill_dir, config, lock_data), outcome in zip(prepared, built):
            if outcome.error is not None:
                results[config.name] = {"status": "failed", "reason": outcome.error}
            else:
                assert outcome.value is not None
                ready.append(_Ready(config.name, lock_data, outcome.value))

        def _publish_one(item: _Ready) -> tuple[str, str]:
            return _publish_built(item.lock_data, item.build_result, store, known_assets)

        uploads = fan_out(ready, _publish_one, jobs=jobs)
    for item, outcome in zip(ready, uploads):
        if outcome.error is not None:
            results[item.name] = {"status": "failed", "reason": outcome.error}
        else:
            assert outcome.value is not None
            status, reason = outcome.value
            results[item.name] = {"status": status, "reason": reason}
    return results

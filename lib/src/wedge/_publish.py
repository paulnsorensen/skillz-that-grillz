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
from pathlib import Path
from typing import cast

from wedge._build import BuildResult, build_many
from wedge._config import ConfigError, WedgeConfig, load_config
from wedge._digest import content_sha256
from wedge._fanout import fan_out
from wedge._lock import LockData, load_lock

RELEASE = "wedge"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def _ensure_release(repo: str, target: str) -> None:
    """Make sure the rolling ``wedge`` prerelease exists, tolerating a race."""
    if _run(["release", "view", RELEASE, "--repo", repo]).returncode == 0:
        return
    created = _run(
        [
            "release",
            "create",
            RELEASE,
            "--repo",
            repo,
            "--title",
            RELEASE,
            "--notes",
            "Rolling release: content-addressed wedge .pyz assets.",
            "--prerelease",
            "--latest=false",
            "--target",
            target,
        ]
    )
    if created.returncode == 0:
        return
    if _run(["release", "view", RELEASE, "--repo", repo]).returncode == 0:
        return
    raise RuntimeError(f"cannot create or find release {RELEASE!r} in {repo}: {created.stderr}")


def _require_on_branch(repo: str, branch: str, target: str) -> None:
    """Refuse a target commit that ``branch`` does not contain.

    The compare API answers without any local history, so a shallow checkout
    is enough: ``identical`` is the branch tip, ``behind`` an ancestor of it.
    """
    result = _run(["api", f"repos/{repo}/compare/{branch}...{target}", "--jq", ".status"])
    if result.returncode != 0:
        raise RuntimeError(f"cannot compare {target} with {branch} in {repo}: {result.stderr}")
    status = result.stdout.strip()
    if status not in {"identical", "behind"}:
        raise ValueError(f"{target} is not on {branch} in {repo} (compare status {status!r})")


def _asset_exists(repo: str, asset: str) -> bool:
    result = _run(["release", "view", RELEASE, "--repo", repo, "--json", "assets"])
    if result.returncode != 0:
        raise RuntimeError(f"cannot view release {RELEASE!r} in {repo}: {result.stderr}")
    payload_raw: object = cast(object, json.loads(result.stdout))
    if not isinstance(payload_raw, dict):
        raise ValueError("release assets response is invalid")
    payload = cast(dict[str, object], payload_raw)
    assets_raw: object = payload.get("assets")
    if not isinstance(assets_raw, list):
        raise ValueError("release assets response is invalid")
    assets = cast(list[object], assets_raw)
    typed_assets = [cast(dict[str, object], entry) for entry in assets if isinstance(entry, dict)]
    return any(entry.get("name") == asset for entry in typed_assets)


def _published_digest(repo: str, asset: str) -> str:
    """Download the asset and return its content digest; ``"invalid"`` if it is no archive."""
    with tempfile.TemporaryDirectory(prefix="wedge-digest-") as tmp:
        download = _run(
            ["release", "download", RELEASE, "--repo", repo, "--pattern", asset, "--dir", tmp, "--clobber"]
        )
        if download.returncode != 0:
            raise RuntimeError(f"cannot download asset {asset!r}: {download.stderr}")
        try:
            return content_sha256(Path(tmp) / asset)
        except Exception:  # any unreadable archive (bad zip, zlib, CRC) is a mismatch
            return "invalid"


def _verdict(lock_data: LockData, repo: str, found: str) -> tuple[str, str]:
    published = _published_digest(repo, lock_data.asset)
    if published == lock_data.content_sha256:
        return "skipped", f"asset {lock_data.asset} {found}"
    return (
        "failed",
        f"asset {lock_data.asset} has content digest {published}, lock wants {lock_data.content_sha256}",
    )


def _publish_built(lock_data: LockData, result: BuildResult, repo: str) -> tuple[str, str]:
    """Publish one freshly built skill; returns ``(status, reason)``.

    Every run builds and compares the digest to the lock before it trusts a
    published asset. An asset uploaded from another host counts as published
    only after this host reproduces the same content.
    """
    if result.key != lock_data.key:
        return "failed", f"lock is stale: key {lock_data.key} != current {result.key}"
    if result.content_sha256 != lock_data.content_sha256:
        return (
            "failed",
            f"built content digest {result.content_sha256} != lock {lock_data.content_sha256}",
        )
    if _asset_exists(repo, lock_data.asset):
        return _verdict(lock_data, repo, "already published")
    upload = _run(["release", "upload", RELEASE, str(result.path), "--repo", repo])
    if upload.returncode == 0:
        return "published", f"uploaded {lock_data.asset}"
    if _asset_exists(repo, lock_data.asset):
        return _verdict(lock_data, repo, "published by a racing run")
    return "failed", f"upload failed: {upload.stderr}"


def publish(
    skill_dirs: list[Path],
    *,
    repo: str,
    target: str,
    jobs: int | None = None,
    branch: str | None = None,
) -> dict[str, dict[str, str]]:
    """Publish every skill, ``jobs`` at a time; returns ``{name: {status, reason}}``.

    With ``branch``, refuse before any side effect unless ``target`` is on
    that branch. Skills that share build inputs share one site directory, so
    a repository of many skills over one package downloads its closure once.
    """
    if not skill_dirs:
        return {}
    configs: list[tuple[Path, WedgeConfig | None, str | None]] = []
    for raw_skill in skill_dirs:
        skill_dir = Path(raw_skill)
        try:
            config = load_config(skill_dir)
        except (ConfigError, OSError) as exc:
            configs.append((skill_dir, None, str(exc)))
        else:
            configs.append((skill_dir, config, None))
    valid = [(path, config) for path, config, _error in configs if config is not None]
    names = [config.name for _path, config in valid]
    if len(names) != len(set(names)):
        raise ValueError("duplicate skill names are not publishable")
    if any(config.repo != repo for _path, config in valid):
        raise ValueError("publish repository differs from skill configuration")

    results: dict[str, dict[str, str]] = {}
    used_names = set(names)
    for index, (skill_dir, config, error) in enumerate(configs):
        if config is None:
            name = skill_dir.name
            while name in used_names:
                name = f"{skill_dir.name}-{index}"
                index += 1
            used_names.add(name)
            results[name] = {"status": "failed", "reason": error or "invalid configuration"}

    prepared: list[tuple[Path, WedgeConfig, LockData]] = []
    for skill_dir, config in valid:
        try:
            lock_data = load_lock(skill_dir, config.name)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            results[config.name] = {"status": "failed", "reason": str(exc)}
            continue
        if lock_data.repo != config.repo:
            raise ValueError("lock repository differs from skill configuration")
        prepared.append((skill_dir, config, lock_data))
    if not prepared:
        return results
    if branch is not None:
        _require_on_branch(repo, branch, target)
    _ensure_release(repo, target)
    with tempfile.TemporaryDirectory(prefix="wedge-publish-") as tmp:
        built = build_many([skill_dir for skill_dir, _config, _lock in prepared], Path(tmp), jobs=jobs)
        ready: list[tuple[str, LockData, BuildResult]] = []
        for (_skill_dir, config, lock_data), outcome in zip(prepared, built):
            if outcome.value is None:
                results[config.name] = {"status": "failed", "reason": outcome.error or ""}
            else:
                ready.append((config.name, lock_data, outcome.value))
        uploads = fan_out(ready, lambda item: _publish_built(item[1], item[2], repo), jobs=jobs)
    for (name, _lock, _result), outcome in zip(ready, uploads):
        status, reason = outcome.value if outcome.value is not None else ("failed", outcome.error or "")
        results[name] = {"status": status, "reason": reason}
    return results

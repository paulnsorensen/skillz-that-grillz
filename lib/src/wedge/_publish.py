"""Publish each wedged skill's .pyz to the rolling ``wedge`` GitHub release.

Idempotent and race-safe: two runs (or two racing processes) that build the
same key converge on exactly one uploaded asset. An existing asset counts as
published only when its content digest matches the lock; its compressed
bytes may come from another zlib build. Everything goes through the
``gh`` CLI so ``GH_TOKEN`` and auth stay in one place.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import cast

from wedge._build import build
from wedge._config import ConfigError, WedgeConfig, load_config
from wedge._digest import content_sha256
from wedge._key import compute_key
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


def _publish_one(skill_dir: Path, repo: str) -> tuple[str, str]:
    """Publish one skill; returns ``(status, reason)``."""
    skill_dir = Path(skill_dir)
    config = load_config(skill_dir)
    lock_data = load_lock(skill_dir, config.name)
    if lock_data.repo != repo:
        return "failed", f"lock targets repo {lock_data.repo}, not the publish repo {repo}"
    key = compute_key(skill_dir, config)
    if key != lock_data.key:
        return "failed", f"lock is stale: key {lock_data.key} != current {key}"

    if _asset_exists(repo, lock_data.asset):
        return _verdict(lock_data, repo, "already published")

    with tempfile.TemporaryDirectory(prefix="wedge-publish-") as tmp:
        result = build(skill_dir, Path(tmp))
        if result.content_sha256 != lock_data.content_sha256:
            return (
                "failed",
                f"built content digest {result.content_sha256} != lock {lock_data.content_sha256}",
            )
        upload = _run(["release", "upload", RELEASE, str(result.path), "--repo", repo])
        if upload.returncode == 0:
            return "published", f"uploaded {lock_data.asset}"

    if _asset_exists(repo, lock_data.asset):
        return _verdict(lock_data, repo, "published by a racing run")
    return "failed", f"upload failed: {upload.stderr}"


def publish(skill_dirs: list[Path], *, repo: str, target: str) -> dict[str, dict[str, str]]:
    """Publish every skill; returns ``{name: {status, reason}}``."""
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

    prepared: list[tuple[Path, WedgeConfig]] = []
    for skill_dir, config in valid:
        try:
            lock_data = load_lock(skill_dir, config.name)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            results[config.name] = {"status": "failed", "reason": str(exc)}
            continue
        if lock_data.repo != config.repo:
            raise ValueError("lock repository differs from skill configuration")
        prepared.append((skill_dir, config))
    if not prepared:
        return results
    _ensure_release(repo, target)
    for skill_dir, config in prepared:
        try:
            status, reason = _publish_one(skill_dir, repo)
        except (ConfigError, OSError, ValueError) as exc:
            status, reason = "failed", str(exc)
        results[config.name] = {"status": status, "reason": reason}
    return results

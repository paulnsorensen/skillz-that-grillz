"""Publish each wedged skill's .pyz to the rolling ``wedge`` GitHub release.

Idempotent and race-safe: two runs (or two racing processes) that build the
same key converge on exactly one uploaded asset. Everything goes through the
``gh`` CLI so ``GH_TOKEN`` and auth stay in one place.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import cast

from wedge._build import build
from wedge._config import ConfigError, WedgeConfig, load_config
from wedge._key import compute_key
from wedge._lock import load_lock

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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_digest(repo: str, asset: str) -> str | None:
    """The asset's sha256, from the API digest or a downloaded copy."""
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
    match = next((entry for entry in typed_assets if entry.get("name") == asset), None)
    if not isinstance(match, dict):
        return None
    digest = match.get("digest")
    if isinstance(digest, str) and digest:
        return digest.removeprefix("sha256:")
    with tempfile.TemporaryDirectory(prefix="wedge-digest-") as tmp:
        downloaded = Path(tmp) / asset
        download = _run(
            [
                "release",
                "download",
                RELEASE,
                "--repo",
                repo,
                "--pattern",
                asset,
                "--dir",
                tmp,
                "--clobber",
            ]
        )
        if download.returncode != 0:
            raise RuntimeError(f"cannot download asset {asset!r}: {download.stderr}")
        return _sha256(downloaded)


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

    existing = _asset_digest(repo, lock_data.asset)
    if existing is not None:
        if existing == lock_data.sha256:
            return "skipped", f"asset {lock_data.asset} already published"
        return (
            "failed",
            f"asset {lock_data.asset} exists with digest {existing}, lock wants {lock_data.sha256}",
        )

    with tempfile.TemporaryDirectory(prefix="wedge-publish-") as tmp:
        result = build(skill_dir, Path(tmp))
        if result.sha256 != lock_data.sha256:
            return "failed", f"built sha256 {result.sha256} != lock {lock_data.sha256}"
        upload = _run(["release", "upload", RELEASE, str(result.path), "--repo", repo])
        if upload.returncode == 0:
            return "published", f"uploaded {lock_data.asset}"

    raced = _asset_digest(repo, lock_data.asset)
    if raced == lock_data.sha256:
        return "skipped", f"asset {lock_data.asset} published by a racing run"
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

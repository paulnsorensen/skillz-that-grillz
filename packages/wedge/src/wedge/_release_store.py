"""The release operations publish needs, and the ``gh`` CLI that performs them.

The publish policy talks to a ``ReleaseStore``; production passes a
``GhReleaseStore`` so ``GH_TOKEN`` and auth stay in the ``gh`` CLI.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Protocol, cast, final

RELEASE = "wedge"


class ReleaseStoreError(RuntimeError):
    """A release operation failed.

    ``create`` and ``upload`` carry the raw failure text; the policy words its
    own message around it. ``list_assets`` and ``download`` carry the full message.
    """


class ReleaseStore(Protocol):
    """One repository's rolling ``wedge`` release."""

    def view(self) -> bool:
        """Whether the release exists."""
        ...

    def create(self, target: str) -> None:
        """Create the release at ``target``; raise ``ReleaseStoreError`` on failure."""
        ...

    def list_assets(self) -> set[str]:
        """Every asset name on the release."""
        ...

    def upload(self, path: Path) -> None:
        """Upload ``path`` as an asset; raise ``ReleaseStoreError`` on failure."""
        ...

    def download(self, asset: str, directory: Path) -> None:
        """Write ``asset`` to ``directory / asset``."""
        ...


GhRun = Callable[[list[str]], subprocess.CompletedProcess[str]]


def run_gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


@final
class GhReleaseStore:
    """``ReleaseStore`` backed by the ``gh`` CLI."""

    def __init__(self, repo: str, run: GhRun = run_gh) -> None:
        self._repo: str = repo
        self._run: GhRun = run

    def view(self) -> bool:
        return self._run(["release", "view", RELEASE, "--repo", self._repo]).returncode == 0

    def create(self, target: str) -> None:
        created = self._run(
            [
                "release",
                "create",
                RELEASE,
                "--repo",
                self._repo,
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
        if created.returncode != 0:
            raise ReleaseStoreError(created.stderr)

    def list_assets(self) -> set[str]:
        result = self._run(["release", "view", RELEASE, "--repo", self._repo, "--json", "assets"])
        if result.returncode != 0:
            raise ReleaseStoreError(f"cannot view release {RELEASE!r} in {self._repo}: {result.stderr}")
        payload_raw: object = cast(object, json.loads(result.stdout))
        if not isinstance(payload_raw, dict):
            raise ValueError("release assets response is invalid")
        payload = cast(dict[str, object], payload_raw)
        assets_raw: object = payload.get("assets")
        if not isinstance(assets_raw, list):
            raise ValueError("release assets response is invalid")
        assets = cast(list[object], assets_raw)
        typed_assets = [cast(dict[str, object], entry) for entry in assets if isinstance(entry, dict)]
        return {cast(str, entry["name"]) for entry in typed_assets if isinstance(entry.get("name"), str)}

    def upload(self, path: Path) -> None:
        upload = self._run(["release", "upload", RELEASE, str(path), "--repo", self._repo])
        if upload.returncode != 0:
            raise ReleaseStoreError(upload.stderr)

    def download(self, asset: str, directory: Path) -> None:
        download = self._run(
            [
                "release",
                "download",
                RELEASE,
                "--repo",
                self._repo,
                "--pattern",
                asset,
                "--dir",
                str(directory),
                "--clobber",
            ]
        )
        if download.returncode != 0:
            raise ReleaseStoreError(f"cannot download asset {asset!r}: {download.stderr}")

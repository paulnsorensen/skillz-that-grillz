"""Build direct executable skill archives without locks or launchers."""

from __future__ import annotations

import os
import shutil
import tempfile
import zlib
from pathlib import Path
from zipfile import BadZipFile

from wedge._build import BuildResult, build_many
from wedge._fanout import Outcome
from wedge._digest import content_sha256


def bundle_many(
    skill_dirs: list[Path], *, jobs: int | None = None, check: bool = False
) -> list[Outcome[Path, BuildResult]]:
    """Build direct name.pyz files for each skill, or check committed bundles."""
    with tempfile.TemporaryDirectory(prefix="wedge-bundle-") as tmp:
        outcomes = build_many(skill_dirs, Path(tmp), jobs=jobs)
        checked: list[Outcome[Path, BuildResult]] = []
        for outcome in outcomes:
            if outcome.error is not None or outcome.value is None:
                checked.append(outcome)
                continue
            skill = Path(outcome.item).resolve()
            scripts = skill / "scripts"
            target = scripts / f"{outcome.value.name}.pyz"
            if scripts.is_symlink():
                checked.append(Outcome(outcome.item, error=f"scripts directory must not be symlink: {scripts}"))
                continue
            if target.is_symlink():
                checked.append(Outcome(outcome.item, error=f"bundle target must not be symlink: {target}"))
                continue
            if check:
                error: str | None = None
                if not target.is_file():
                    error = f"missing bundle {target}"
                elif not os.access(target, os.X_OK):
                    error = f"bundle is not executable {target}"
                else:
                    try:
                        digest = content_sha256(target)
                        with target.open("rb") as archive:
                            first_line = archive.readline()
                        if first_line != b"#!/usr/bin/env python3\n":
                            error = f"bundle has non-canonical shebang {target}"
                        elif digest != outcome.value.content_sha256:
                            error = f"stale bundle {target}"
                    except (BadZipFile, OSError, ValueError, zlib.error) as exc:
                        error = f"invalid bundle {target}: {exc}"
                checked.append(Outcome(
                    outcome.item,
                    value=BuildResult(
                        outcome.value.name, outcome.value.key, outcome.value.content_sha256, target
                    ),
                    error=error,
                ))
                continue
            temporary_path: Path | None = None
            try:
                scripts.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    dir=scripts, prefix=f".{target.name}.", delete=False
                ) as temporary:
                    temporary_path = Path(temporary.name)
                _ = shutil.copyfile(outcome.value.path, temporary_path)
                temporary_path.chmod(0o755)
                os.replace(temporary_path, target)
            except OSError as exc:
                checked.append(Outcome(outcome.item, error=str(exc)))
                continue
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            value = BuildResult(outcome.value.name, outcome.value.key, outcome.value.content_sha256, target)
            checked.append(Outcome(outcome.item, value=value))
        return checked

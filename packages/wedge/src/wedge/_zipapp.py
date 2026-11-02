"""Write a canonical, directly runnable wedge ZIP archive."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from wedge._key import FORMAT_VERSION
from wedge._main import MAIN_SOURCE

_SHEBANG = b"#!/usr/bin/env python3\n"
_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def write_archive(site_dir: Path, entry: str, out_path: Path) -> None:
    environment = json.dumps(
        {"entry": entry, "format": FORMAT_VERSION}, sort_keys=True, separators=(",", ":")
    ).encode() + b"\n"
    members: dict[str, bytes | Path | None] = {
        "__main__.py": MAIN_SOURCE.encode(),
        "environment.json": environment,
        "site-packages/": None,
    }
    for directory, dirs, files in os.walk(site_dir, followlinks=True):
        dirs.sort()
        relative = Path(directory).relative_to(site_dir)
        if relative != Path("."):
            members["site-packages/" + relative.as_posix() + "/"] = None
        for name in files:
            path = Path(directory) / name
            if path.suffix in {".so", ".pyd"}:
                raise ValueError(f"native extension modules are not supported in ZIP archives: {path.relative_to(site_dir)}")
            members["site-packages/" + path.relative_to(site_dir).as_posix()] = path
    with out_path.open("wb") as output:
        _ = output.write(_SHEBANG)
        with ZipFile(output, "w") as archive:
            for name, source in sorted(members.items()):
                info = ZipInfo(name, date_time=_DATE_TIME)
                info.create_system = 3
                info.external_attr = ((0o40755 << 16) | 0x10) if source is None else (0o100644 << 16)
                info.compress_type = ZIP_DEFLATED if source is not None else ZIP_STORED
                if source is None:
                    archive.writestr(info, b"")
                else:
                    with archive.open(info, "w") as member:
                        if isinstance(source, Path):
                            with source.open("rb") as data:
                                _ = shutil.copyfileobj(data, member)
                        else:
                            _ = member.write(source)
    out_path.chmod(0o755)

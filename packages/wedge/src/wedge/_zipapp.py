"""Write a canonical, directly runnable wedge ZIP archive."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from wedge._config import BYTECODE_PYTHONS
from wedge._key import FORMAT_VERSION
from wedge._main import MAIN_SOURCE

_SHEBANG = b"#!/usr/bin/env python3\n"
_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def write_archive(site_dir: Path, entry: str, out_path: Path, trees: dict[str, Path] | None = None) -> None:
    environment = json.dumps(
        {"entry": entry, "format": FORMAT_VERSION, "python_minors": [minor for minor, _patch in BYTECODE_PYTHONS]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode() + b"\n"
    members: dict[str, bytes | Path | None] = {
        "__main__.py": MAIN_SOURCE.encode(),
        "environment.json": environment,
        "site-packages/": None,
    }
    for prefix, root in (("site-packages", site_dir), *((trees or {}).items())):
        members[prefix + "/"] = None
        for directory, dirs, files in os.walk(root, followlinks=True):
            dirs.sort()
            relative = Path(directory).relative_to(root)
            if relative != Path("."):
                members[prefix + "/" + relative.as_posix() + "/"] = None
            for name in files:
                path = Path(directory) / name
                members[prefix + "/" + path.relative_to(root).as_posix()] = path
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

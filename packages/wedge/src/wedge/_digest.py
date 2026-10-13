"""The content digest: a sha256 over a .pyz's uncompressed members.

Deflate output depends on the zlib build, so two hosts can compress the same
build into different bytes. The digest covers only what Python reads back out
of the archive: each member's name and uncompressed bytes, in archive order.
Every host computes the same digest for the same build, so a local
``wedge lock`` can pin it and a compressed asset from any runner verifies.

The launcher template carries a byte-for-byte copy of this algorithm; a test
keeps the two in step.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

# Refuse archives that claim more than this many uncompressed bytes, so a
# crafted asset cannot exhaust memory or disk before verification fails.
MAX_UNCOMPRESSED = 512 * 1024 * 1024
# zipimport reads only these two methods; an archive that hashes right but
# cannot run is refused up front instead of failing inside Python.
_RUNNABLE_METHODS = (ZIP_STORED, ZIP_DEFLATED)
_DOMAIN = b"wedge-content-v1\0"


def content_sha256(path: Path) -> str:
    """The sha256 over ``path``'s member names and uncompressed bytes."""
    digest = hashlib.sha256(_DOMAIN)
    total = 0
    with ZipFile(path) as archive:
        for info in archive.infolist():
            if info.compress_type not in _RUNNABLE_METHODS:
                raise ValueError(f"{path} member {info.filename} uses a ZIP method zipimport cannot read")
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise ValueError(f"{path} expands past {MAX_UNCOMPRESSED} bytes")
            name = info.filename.encode("utf-8")
            digest.update(len(name).to_bytes(8, "big") + name)
            digest.update(info.file_size.to_bytes(8, "big"))
            with archive.open(info) as member:
                for chunk in iter(lambda: member.read(1 << 20), b""):
                    digest.update(chunk)
    return digest.hexdigest()

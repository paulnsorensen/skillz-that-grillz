"""Read proof trees without following links, then stage real case inputs."""
from __future__ import annotations

import base64
import os
import stat
from collections.abc import Mapping
from pathlib import Path

from skillz_experiments._cases import Fixture
from skillz_experiments._records import read_bytes

FILE_COUNT = 200
PAYLOAD_LIMIT = 4 * 1024 * 1024
TRACE = "trace/events.jsonl"


def _directory(target: Path, reference: str) -> Path:
    here = target
    for part in reference.split("/"):
        here = here / part
        if not stat.S_ISDIR(here.lstat().st_mode):
            raise ValueError(f"{reference} must be a plain directory")
    return here


def fixture_files(target: Path, reference: str, journey: bool) -> dict[str, bytes]:
    """Return only output text files and a required journey trace from a bounded proof tree."""
    root = _directory(target, reference)
    found: dict[str, bytes] = {}
    total = 0
    def fail_traversal(error: OSError) -> None:
        raise error
    for parent, directories, files in os.walk(root, onerror=fail_traversal, followlinks=False):
        for name in (*directories, *files):
            path = Path(parent) / name
            rel = path.relative_to(root).as_posix()
            root_entry = len(Path(rel).parts) == 1
            if name.startswith(".") or (root_entry and name not in (("output", "trace") if journey else ("output",))):
                raise ValueError(f"proof fixture has an unsafe entry {rel}")
            if rel.startswith("trace/") and rel != TRACE:
                raise ValueError(f"proof fixture has an unexpected entry {rel}")
            if root_entry and name in files:
                raise ValueError(f"proof fixture root entry {rel} must be a directory")
            mode = path.lstat().st_mode
            if name in directories and not stat.S_ISDIR(mode) or name in files and not stat.S_ISREG(mode):
                raise ValueError(f"proof fixture has a nonregular entry {rel}")
            if name in files:
                if not (rel.startswith("output/") or journey and rel == TRACE):
                    raise ValueError(f"proof fixture has an unexpected file {rel}")
                if len(found) >= FILE_COUNT:
                    raise ValueError("proof fixture exceeds 200 files")
                data = read_bytes(target, f"{reference}/{rel}", PAYLOAD_LIMIT - total)
                try:
                    _ = data.decode("utf-8")
                except UnicodeDecodeError:
                    raise ValueError(f"proof fixture {rel} must be UTF-8 text") from None
                found[rel] = data
                total += len(data)
    if journey and TRACE not in found:
        raise ValueError("journey proof requires trace/events.jsonl")
    return found


def stage(workspace: Path, files: Mapping[str, Fixture], proof: Mapping[str, bytes], journey: bool) -> None:
    (workspace / "home").mkdir()
    (workspace / "tmp").mkdir()
    for name, content in files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(content.encode("utf-8") if isinstance(content, str)
                             else base64.b64decode(content["base64"]))
    for name, content in proof.items():
        if name in files and not (journey and name == TRACE):
            raise ValueError(f"proof cannot replace case input {name}")
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(content)

from __future__ import annotations

import contextlib
import json
import os
import select
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import IO, cast

from skillz_experiments._cases import loads_untrusted, mapping


def valid_listing(value: object, workspace: Path, skill: str) -> bool:
    result = mapping(value)
    data = result.get("data")
    if not isinstance(data, list) or len(cast(list[object], data)) != 1:
        return False
    listing = mapping(cast(list[object], data)[0])
    if listing.get("cwd") != str(workspace) or listing.get("errors") != []:
        return False
    skills = listing.get("skills")
    if not isinstance(skills, list) or len(cast(list[object], skills)) != 1:
        return False
    listed = mapping(cast(list[object], skills)[0])
    return (listed.get("name") == skill and listed.get("enabled") is True
            and listed.get("scope") == "repo" and listed.get("pluginId") is None
            and listed.get("path") == str(workspace / ".agents/skills" / skill / "SKILL.md"))


def _send(stream: IO[bytes], messages: list[dict[str, object]]) -> None:
    _ = stream.write("".join(json.dumps(item) + "\n" for item in messages).encode())
    stream.flush()


def _reply(message: dict[str, object], stream: IO[bytes], workspace: Path, skill: str) -> bool | None:
    """Return the discovery verdict, or None while the server has more to say."""
    if message.get("id") == 0:
        if "error" in message:
            return False
        _send(stream, [{"method": "initialized", "params": {}},
                       {"id": 1, "method": "skills/list", "params": {"cwds": [str(workspace)], "forceReload": True}}])
    elif message.get("id") == 1:
        return "error" not in message and valid_listing(message.get("result"), workspace, skill)
    return None


def _verdict(child: subprocess.Popen[bytes], stream: IO[bytes], deadline: float, workspace: Path,
             skill: str) -> bool | None:
    assert child.stdout is not None
    buffer = bytearray()
    while time.monotonic() < deadline:
        ready, _, _ = select.select([child.stdout], [], [], max(0, deadline - time.monotonic()))
        if not ready:
            break
        chunk = os.read(child.stdout.fileno(), 65536)
        if not chunk:
            break
        buffer.extend(chunk)
        while b"\n" in buffer:
            end = buffer.index(b"\n")
            line = bytes(buffer[:end])
            del buffer[:end + 1]
            verdict = _reply(mapping(loads_untrusted(line.decode())), stream, workspace, skill)
            if verdict is not None:
                return verdict
    return None


def discover(command: list[str], workspace: Path, environment: dict[str, str], timeout: float,
             skill: str) -> bool:
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as errors, subprocess.Popen(command, cwd=workspace, env=environment, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=errors, start_new_session=True) as child:
        assert child.stdin is not None
        _send(child.stdin, [{"id": 0, "method": "initialize", "params": {
            "clientInfo": {"name": "skillz-experiment", "version": "1"},
            "capabilities": {"experimentalApi": True}}}])
        try:
            verdict = _verdict(child, child.stdin, deadline, workspace, skill)
            if verdict is None:
                _ = errors.seek(0)
                detail = errors.read(4096).decode("utf-8", errors="replace")
                raise RuntimeError("native Codex skill discovery gave no result: " + detail)
            return verdict
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            _ = child.wait()
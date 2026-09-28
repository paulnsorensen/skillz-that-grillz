from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import cast

from skillz_experiments._cases import mapping


def valid_listing(value: object, workspace: Path) -> bool:
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
    skill = mapping(cast(list[object], skills)[0])
    return (skill.get("name") == "skillz" and skill.get("enabled") is True
            and skill.get("scope") == "repo" and skill.get("pluginId") is None
            and skill.get("path") == str(workspace / ".agents/skills/skillz/SKILL.md"))


def discover(command: list[str], workspace: Path, environment: dict[str, str], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as errors, subprocess.Popen(command, cwd=workspace, env=environment, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=errors, start_new_session=True) as child:
        assert child.stdin is not None and child.stdout is not None
        initialize = {"id": 0, "method": "initialize", "params": {
            "clientInfo": {"name": "skillz-experiment", "version": "1"},
            "capabilities": {"experimentalApi": True}}}
        _ = child.stdin.write((json.dumps(initialize) + "\n").encode())
        child.stdin.flush()
        buffer = bytearray()
        try:
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
                    message = mapping(cast(object, json.loads(line)))
                    if message.get("id") == 0:
                        if "error" in message:
                            return False
                        notifications: list[dict[str, object]] = [{"method": "initialized", "params": {}},
                            {"id": 1, "method": "skills/list", "params": {"cwds": [str(workspace)], "forceReload": True}}]
                        _ = child.stdin.write("".join(json.dumps(item) + "\n" for item in notifications).encode())
                        child.stdin.flush()
                    elif message.get("id") == 1:
                        return "error" not in message and valid_listing(message.get("result"), workspace)
            _ = errors.seek(0)
            detail = errors.read(4096).decode("utf-8", errors="replace")
            raise RuntimeError("native Codex skill discovery timed out or closed: " + detail)
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _ = child.wait()

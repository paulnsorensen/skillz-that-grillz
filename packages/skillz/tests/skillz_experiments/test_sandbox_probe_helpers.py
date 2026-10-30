from __future__ import annotations

import shlex
import signal
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

from skillz_experiments.harness._claude._sandbox import unix_command, unix_failure
from skillz_experiments.wrapper._gemini import _interrupted  # pyright: ignore[reportPrivateUsage]


ADDRESS = "/tmp/skillz-probe.sock"
TOKEN = "probe-token"


def _events(command: str, output: str | None) -> list[dict[str, object]]:
    use: dict[str, object] = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "probe", "name": "Bash", "input": {"command": command}}]}}
    if output is None:
        return [use]
    result: dict[str, object] = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "probe", "content": output}]}}
    return [use, result]


def test_unix_probe_accepts_only_its_denied_result() -> None:
    command = unix_command(ADDRESS, TOKEN)
    assert "socket.AF_UNIX" in command
    assert ADDRESS in command
    assert TOKEN in command
    assert unix_failure(_events(command, f"denied-{TOKEN}"), ADDRESS, TOKEN) is None


def test_unix_probe_command_connects_to_owned_socket() -> None:
    with tempfile.TemporaryDirectory(prefix="sp-", dir="/tmp") as temporary:
        address = Path(temporary) / "probe.sock"
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(address))
            server.listen(1)
            server.settimeout(2)
            connected = subprocess.run(
                shlex.split(unix_command(str(address), TOKEN)),
                capture_output=True, text=True, check=True, timeout=8,
            )
            with server.accept()[0] as peer:
                assert peer.recv(1024) == TOKEN.encode()
            assert connected.stdout == f"open-{TOKEN}\n"

        denied = subprocess.run(
            shlex.split(unix_command(str(Path(temporary) / "absent.sock"), TOKEN)),
            capture_output=True, text=True, check=True, timeout=8,
        )
        assert denied.stdout == f"denied-{TOKEN}\n"


@pytest.mark.parametrize(
    ("command", "output"),
    [
        (None, None),
        (None, "open-probe-token"),
        (None, "denied-other-token"),
        ("other-command", "denied-probe-token"),
    ],
)
def test_unix_probe_rejects_missing_or_unrelated_evidence(command: str | None, output: str | None) -> None:
    evidence = [] if command is None and output is None else _events(command or unix_command(ADDRESS, TOKEN), output)
    failure = unix_failure(evidence, ADDRESS, TOKEN)
    assert failure is not None
    assert "no evidence" in failure


@pytest.mark.parametrize(("signum", "status"), [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_gemini_signal_handler_exits_with_signal_status(signum: int, status: int) -> None:
    with pytest.raises(SystemExit) as caught:
        _interrupted(signum, None)
    assert caught.value.code == status

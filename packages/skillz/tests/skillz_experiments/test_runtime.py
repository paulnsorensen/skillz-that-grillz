from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._budget import BudgetExhausted
from skillz_experiments._runtime import process


def test_deadline_kills_process_group(tmp_path: Path) -> None:
    script = ("import subprocess,sys,time,pathlib; "
              + "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
              + "pathlib.Path('descendant.pid').write_text(str(child.pid));time.sleep(30)")
    with pytest.raises(BudgetExhausted, match="terminated"):
        _ = process([sys.executable, "-c", script], cwd=tmp_path,
                    timeout=0.5, environment={"PATH": os.defpath})
    pid = int((tmp_path / "descendant.pid").read_text())
    status = Path(f"/proc/{pid}/stat")
    deadline = time.monotonic() + 2
    while True:
        try:
            state = status.read_text().split()[2]
        except (FileNotFoundError, ProcessLookupError):
            return
        if state == "Z" or time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    assert state == "Z"

def test_deadline_sends_sigterm_before_sigkill_so_a_wrapper_runs_its_stopper(tmp_path: Path) -> None:
    script = ("import signal,sys,pathlib,time\n"
              "def stop(*_):\n"
              " pathlib.Path('stopper.ran').write_text('yes'); sys.exit(0)\n"
              "signal.signal(signal.SIGTERM, stop)\n"
              "pathlib.Path('ready').write_text('1')\n"
              "time.sleep(30)\n")
    with pytest.raises(BudgetExhausted, match="terminated"):
        _ = process([sys.executable, "-c", script], cwd=tmp_path, timeout=5, environment={"PATH": os.defpath})
    assert (tmp_path / "stopper.ran").read_text() == "yes"


def test_interrupt_sends_sigterm_before_sigkill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    script = ("import signal,sys,pathlib,time\n"
              "def stop(*_):\n"
              " pathlib.Path('stopper.ran').write_text('yes'); sys.exit(0)\n"
              "signal.signal(signal.SIGTERM, stop)\n"
              "pathlib.Path('ready').write_text('1')\n"
              "time.sleep(30)\n")
    real = cast(Callable[..., tuple[str, str]], subprocess.Popen.communicate)
    calls = {"n": 0}

    def interrupted(self: subprocess.Popen[str], *args: object, **kwargs: object) -> tuple[str, str]:
        calls["n"] += 1
        if calls["n"] == 1:
            deadline = time.monotonic() + 5
            while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            raise KeyboardInterrupt
        return real(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    assert threading.current_thread() is threading.main_thread()
    with pytest.raises(KeyboardInterrupt):
        _ = process([sys.executable, "-c", script], cwd=tmp_path, timeout=30, environment={"PATH": os.defpath})
    assert (tmp_path / "stopper.ran").read_text() == "yes"


def test_a_second_interrupt_during_the_grace_wait_still_kills_a_child_that_ignores_sigterm(tmp_path: Path) -> None:
    import _thread
    import threading

    from skillz_experiments import _runtime

    script = ("import signal,pathlib,time\n"
              "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
              "pathlib.Path('ready').write_text('1')\n"
              "time.sleep(60)\n")
    with subprocess.Popen([sys.executable, "-c", script], cwd=tmp_path, text=True, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as child:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        timer = threading.Timer(0.5, _thread.interrupt_main)
        timer.start()
        try:
            with pytest.raises(KeyboardInterrupt):
                _runtime._end(child)  # pyright: ignore[reportPrivateUsage]
        finally:
            timer.cancel()
        assert child.poll() is not None


def test_candidate_forbidden_change(tmp_path: Path) -> None:
    from skillz_experiments._candidate import Candidate

    _ = (tmp_path / "SKILL.md").write_text("seed")
    _ = (tmp_path / "oracle.json").write_text("sealed")
    candidate = Candidate.capture(tmp_path, ["SKILL.md"])
    with pytest.raises(ValueError, match="components"):
        _ = candidate.changed({"SKILL.md": "new", "oracle.json": "tampered"})
    assert candidate.changed({"SKILL.md": "new"}).files["oracle.json"] == "sealed"

def test_profile_denies_host_and_cleans_tool_environment(tmp_path: Path) -> None:
    from skillz_experiments.harness._codex import configuration

    arguments = configuration(tmp_path, Path("/usr/bin/codex"), [])
    joined = " ".join(arguments)
    assert '":root"="deny"' in joined
    assert 'network={enabled=false}' in joined
    assert 'shell_environment_policy.inherit="none"' in joined
    assert 'shell_environment_policy.experimental_use_profile=false' in joined
    assert 'skills.bundled.enabled=false' in joined
    assert "--sandbox" not in arguments

def test_startup_failure_evidence_keeps_stderr_only_in_the_private_excerpt() -> None:
    from skillz_experiments.harness._codex import failure_details

    evidence = failure_details(2, "error: unexpected argument '-P' found PRIVATE_PROMPT_SENTINEL", [])
    assert evidence["returncode"] == 2
    assert evidence["reason"] == "unsupported-cli-argument"
    assert "PRIVATE_PROMPT_SENTINEL" in str(evidence["stderr_excerpt"])
    assert "PRIVATE_PROMPT_SENTINEL" not in str({key: value for key, value in evidence.items() if key != "stderr_excerpt"})
    assert evidence["usage"] == {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None}


def _run_probe(tmp_path: Path, port: int, prefix: str = "") -> subprocess.CompletedProcess[str]:
    from skillz_experiments.harness._isolation import probe

    script = probe(tmp_path, tmp_path / "absent-sealed", tmp_path / "absent-engine", port)
    return subprocess.run([sys.executable, "-c", prefix + script], cwd=tmp_path, capture_output=True, text=True,
                          env={"PATH": os.defpath}, timeout=10, check=False)


def test_network_probe_fails_when_the_host_loopback_listener_is_reachable(tmp_path: Path) -> None:
    from skillz_experiments.harness._isolation import listening

    with listening() as port:
        result = _run_probe(tmp_path, port)
    assert result.returncode != 0
    assert "network isolation failed" in result.stderr


def _client(port: int, payload: bytes | None, *, reset: bool = False) -> None:
    import socket
    import struct

    with socket.create_connection(("127.0.0.1", port)) as client:
        if reset:
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        if payload is not None:
            client.sendall(payload)


def test_watched_listener_is_true_when_a_client_sends_the_token() -> None:
    from skillz_experiments.harness._isolation import watched

    with watched() as (port, connected):
        _client(port, b"GET /tok HTTP/1.1\r\n\r\n")
        assert connected("tok") is True


def test_watched_listener_ignores_a_foreign_request() -> None:
    from skillz_experiments.harness._isolation import watched

    with watched() as (port, connected):
        _client(port, b"GET / HTTP/1.1\r\nUser-Agent: Go-http-client/1.1\r\n\r\n")
        assert connected("tok") is False


def test_watched_listener_ignores_a_silent_client() -> None:
    import socket

    from skillz_experiments.harness._isolation import watched

    with watched() as (port, connected), socket.create_connection(("127.0.0.1", port)):
        assert connected("tok") is False


def test_watched_listener_is_true_when_a_client_resets_the_connection() -> None:
    from skillz_experiments.harness._isolation import watched

    with watched() as (port, connected):
        _client(port, None, reset=True)
        assert connected("tok") is True


def _routed(outcome: str) -> str:
    """Prefix that fakes the routed connect. The loopback connect stays real."""
    return (
        "import socket,errno\n"
        "real=socket.socket.connect\n"
        "def connect(self,address):\n"
        " if address[0]!='192.0.2.1': return real(self,address)\n"
        f" outcome={outcome!r}\n"
        " if outcome=='unreachable': raise OSError(errno.ENETUNREACH,'unreachable')\n"
        " if outcome=='timeout': raise socket.timeout('timed out')\n"
        " return None\n"
        "socket.socket.connect=connect\n")


def test_network_probe_passes_when_the_connection_or_the_socket_is_refused(tmp_path: Path) -> None:
    from skillz_experiments.harness._isolation import listening

    with listening() as port:
        pass
    refused = _run_probe(tmp_path, port, _routed("unreachable"))
    assert (refused.returncode, refused.stdout.strip()) == (0, "isolation-ok")
    with listening() as open_port:
        no_socket = _run_probe(tmp_path, open_port, "import socket\ndef deny(*a,**k): raise PermissionError()\nsocket.socket=deny\n")
    assert (no_socket.returncode, no_socket.stdout.strip()) == (0, "isolation-ok")


@pytest.mark.parametrize("outcome", ["connected", "timeout"])
def test_network_probe_fails_when_a_namespace_has_its_own_loopback_but_routed_egress(tmp_path: Path, outcome: str) -> None:
    from skillz_experiments.harness._isolation import listening

    with listening() as port:
        pass
    result = _run_probe(tmp_path, port, _routed(outcome))
    assert result.returncode != 0
    assert "network isolation failed" in result.stderr


def test_network_probe_checks_the_route_with_a_udp_connect_and_never_a_tcp_syn(tmp_path: Path) -> None:
    from skillz_experiments.harness._isolation import listening

    with listening() as port:
        pass
    prefix = (
        "import socket,errno\n"
        "real=socket.socket.connect\n"
        "def connect(self,address):\n"
        " if address[0]!='192.0.2.1': return real(self,address)\n"
        " if self.type!=socket.SOCK_DGRAM: raise SystemExit('tcp connect to the routed address')\n"
        " raise OSError(errno.ENETUNREACH,'unreachable')\n"
        "socket.socket.connect=connect\n")
    result = _run_probe(tmp_path, port, prefix)
    assert (result.returncode, result.stdout.strip()) == (0, "isolation-ok")

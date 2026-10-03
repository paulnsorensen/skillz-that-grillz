from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from skillz_experiments._runtime import BudgetExhausted, process


def test_deadline_kills_process_group(tmp_path: Path) -> None:
    script = ("import subprocess,sys,time,pathlib; "
              + "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
              + "pathlib.Path('descendant.pid').write_text(str(child.pid));time.sleep(30)")
    with pytest.raises(BudgetExhausted, match="terminated"):
        _ = process([sys.executable, "-c", script], cwd=tmp_path,
                    timeout=0.5, environment={"PATH": os.defpath})
    pid = int((tmp_path / "descendant.pid").read_text())
    status = Path(f"/proc/{pid}/stat")
    try:
        state = status.read_text().split()[2]
    except (FileNotFoundError, ProcessLookupError):
        return
    assert state == "Z"


def test_candidate_forbidden_change(tmp_path: Path) -> None:
    from skillz_experiments._candidate import Candidate

    _ = (tmp_path / "SKILL.md").write_text("seed")
    _ = (tmp_path / "oracle.json").write_text("sealed")
    candidate = Candidate.capture(tmp_path, ["SKILL.md"])
    with pytest.raises(ValueError, match="components"):
        _ = candidate.changed({"SKILL.md": "new", "oracle.json": "tampered"})
    assert candidate.changed({"SKILL.md": "new"}).files["oracle.json"] == "sealed"

def test_profile_denies_host_and_cleans_tool_environment(tmp_path: Path) -> None:
    from skillz_experiments._codex import configuration

    arguments = configuration(tmp_path, Path("/usr/bin/codex"), [])
    joined = " ".join(arguments)
    assert '":root"="deny"' in joined
    assert 'network={enabled=false}' in joined
    assert 'shell_environment_policy.inherit="none"' in joined
    assert 'shell_environment_policy.experimental_use_profile=false' in joined
    assert 'skills.bundled.enabled=false' in joined
    assert "--sandbox" not in arguments

def test_startup_failure_evidence_is_actionable_without_private_text() -> None:
    from skillz_experiments._codex import failure_details

    evidence = failure_details(2, "error: unexpected argument '-P' found PRIVATE_PROMPT_SENTINEL", [])
    assert evidence["returncode"] == 2
    assert evidence["reason"] == "unsupported-cli-argument"
    assert "PRIVATE_PROMPT_SENTINEL" not in str(evidence)
    assert evidence["usage"] == {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None}


def _run_probe(tmp_path: Path, port: int, prefix: str = "") -> subprocess.CompletedProcess[str]:
    from skillz_experiments._isolation import probe

    script = probe(tmp_path, tmp_path / "absent-sealed", tmp_path / "absent-engine", port)
    return subprocess.run([sys.executable, "-c", prefix + script], cwd=tmp_path, capture_output=True, text=True,
                          env={"PATH": os.defpath}, timeout=10, check=False)


def test_network_probe_fails_when_the_host_loopback_listener_is_reachable(tmp_path: Path) -> None:
    from skillz_experiments._isolation import listening

    with listening() as port:
        result = _run_probe(tmp_path, port)
    assert result.returncode != 0
    assert "network isolation failed" in result.stderr


def test_network_probe_passes_when_the_connection_or_the_socket_is_refused(tmp_path: Path) -> None:
    from skillz_experiments._isolation import listening

    with listening() as port:
        pass
    refused = _run_probe(tmp_path, port)
    assert (refused.returncode, refused.stdout.strip()) == (0, "isolation-ok")
    with listening() as open_port:
        no_socket = _run_probe(tmp_path, open_port, "import socket\ndef deny(*a,**k): raise PermissionError()\nsocket.socket=deny\n")
    assert (no_socket.returncode, no_socket.stdout.strip()) == (0, "isolation-ok")

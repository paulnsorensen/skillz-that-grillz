"""The nono backend for the Claude role. A pass-through fake `nono` records its argv and profile; it enforces nothing."""
from __future__ import annotations

import json
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments import _claude, _nono
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import CodedError
from skillz_experiments._claude import ClaudeCode, NetworkIsolationFailed
from skillz_experiments._doctor import doctor
from skillz_experiments._harness import Configuration
from skillz_experiments._runtime import Budget
from skillz_experiments._workflow import run

FAKE = Path(__file__).parent / "fixtures/fake_claude.py"
NONO = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
argv = sys.argv[1:]
profile = json.loads(Path(argv[argv.index("--profile") + 1]).read_text())
with Path(__file__).with_name("nono.log").open("a") as log:
    log.write(json.dumps({"argv": argv, "profile": profile, "environment": dict(os.environ),
                          "workspace": sorted(os.listdir(".")), "project": sorted(os.listdir("..")),
                          "agents_link": os.readlink(".agents"),
                          "relative_skill": Path(".agents/skills/skillz/SKILL.md").is_file(),
                          "skills": sorted(os.listdir("../.claude/skills"))}) + "\\n")
# Like nono: pass only the allowed variables, and replace the key with a proxy token.
allowed = set(profile["environment"]["allow_vars"])
child = {name: value for name, value in os.environ.items() if name in allowed}
child |= {"ANTHROPIC_API_KEY": "proxy-token", "ANTHROPIC_BASE_URL": "http://127.0.0.1:1/anthropic"}
fs = profile["filesystem"]
child["FAKE_NONO_READ"] = os.pathsep.join([os.getcwd(), *fs["allow"], *fs["read"], *fs["read_file"]])
child["FAKE_NONO_WRITE"] = os.pathsep.join([os.getcwd(), *fs["allow"]])
os.execve(argv[argv.index("--") + 1], argv[argv.index("--") + 1:], child)
'''
pytestmark = pytest.mark.usefixtures("host_login")
# Read at import, because `nono_bin` patches `sys.platform` to linux.
LINUX_HOST = sys.platform == "linux"


@pytest.fixture
def nono_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a pass-through fake `nono` on PATH and meet every nono requirement."""
    directory = tmp_path / "nono-bin"
    directory.mkdir()
    nono = directory / "nono"
    _ = nono.write_text(NONO)
    nono.chmod(0o755)
    # Keep the stub `bwrap` from `host_login` last, so the host-tool check passes on a host without bubblewrap.
    monkeypatch.setenv("PATH", f"{directory}:/usr/bin:/bin:{tmp_path / 'fallback-bin'}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-host-secret")
    monkeypatch.setattr(_nono, "landlock_abi", lambda: 8)
    monkeypatch.setattr(sys, "platform", "linux")
    return nono


def fake_claude(tmp_path: Path, mode: str = "ok") -> Path:
    executable = tmp_path / "claude-bin" / "claude"
    executable.parent.mkdir()
    _ = shutil.copyfile(FAKE, executable)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    _ = executable.with_name("claude.mode").write_text(mode)
    return executable


def logged(path: Path) -> list[dict[str, object]]:
    return [cast(dict[str, object], json.loads(line)) for line in path.read_text().splitlines()]


def test_invoke_runs_claude_inside_nono_with_read_only_skills_and_no_host_key(tmp_path: Path, nono_bin: Path) -> None:
    executable = fake_claude(tmp_path)
    transport = ClaudeCode("m", Budget(10, 120, 0), lambda: None, executable, isolation="nono")
    try:
        result = transport.invoke("hello", Candidate({"SKILL.md": "---\nname: skillz\ndescription: x\n---\n"}, ("SKILL.md",)))
    finally:
        transport.close()
    nono = logged(nono_bin.with_name("nono.log"))[-1]
    argv = cast(list[str], nono["argv"])
    assert argv[:2] == ["run", "--silent"] and "--allow-cwd" in argv
    assert argv[argv.index("--") + 1] == "/usr/bin/env" and str(executable) in argv
    profile = cast(dict[str, dict[str, object]], nono["profile"])
    assert profile["network"] == {"allow_domain": ["api.anthropic.com"], "credentials": ["anthropic"]}
    assert profile["linux"] == {"af_unix_mediation": "pathname"}
    assert "linux_temp_read" not in cast(list[str], profile["groups"]["include"])
    environment = cast(dict[str, str], nono["environment"])
    assert environment["ANTHROPIC_API_KEY"] == "sk-ant-host-secret" and "nono-state" in environment["HOME"]
    assert "--no-session-persistence" in argv
    child = logged(executable.with_name("claude.log"))[-1]
    workspace = Path(cast(str, child["cwd"]))
    child_environment = cast(dict[str, str], child["environment"])
    assert "sk-ant-host-secret" not in json.dumps(child_environment)
    assert child_environment["HOME"] == str(workspace / "home")
    assert child_environment["CLAUDE_CONFIG_DIR"] == str(workspace.parent / "claude-config")
    assert child_environment["CLAUDE_CONFIG_DIR"] in cast(list[str], profile["filesystem"]["allow"])
    assert child_environment["CLAUDE_CONFIG_DIR"] != str(transport.config_dir)
    assert ".claude" not in cast(list[str], nono["workspace"]) and nono["agents_link"] == "../.agents"
    assert nono["relative_skill"] is True
    assert {".agents", ".claude"} <= set(cast(list[str], nono["project"])) and nono["skills"] == ["skillz"]
    read = cast(list[str], profile["filesystem"]["read"])
    assert str(workspace.parent / ".claude") in read and str(workspace.parent / ".agents") in read
    assert str(workspace) not in read and str(workspace.parent) not in read
    settings = cast(dict[str, dict[str, object]], json.loads(cast(str, child["settings"])))
    assert settings["sandbox"] == {"enabled": False} and "Bash" in cast(list[str], settings["permissions"]["allow"])
    assert cast(dict[str, object], result["answer"])["load_marker"] != ""


def test_missing_requirements_stop_before_any_process_and_name_every_fix(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(_nono, "landlock_abi", lambda: 2)
    monkeypatch.setattr(sys, "platform", "linux")

    def missing(name: str) -> None:
        del name
    monkeypatch.setattr(shutil, "which", missing)
    with pytest.raises(CodedError) as caught:
        _ = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path), isolation="nono")
    assert caught.value.code == "nono-unavailable"
    message = str(caught.value)
    assert all(part in message for part in ("`nono` is not on PATH", "Landlock ABI 2", "ANTHROPIC_API_KEY is not set",
                                              "no unsafe fallback", "brew install nono"))


def test_nono_is_refused_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _nono.problems() == [("nono isolation is verified only on Linux", "use `--isolation claude` on this host")]


def test_nono_isolation_needs_the_claude_harness(tmp_path: Path, make_target: Callable[..., Path]) -> None:
    with pytest.raises(ValueError, match="only the claude harness"):
        _ = Configuration.single("codex", "m", "nono")
    with pytest.raises(ValueError, match="only the claude harness"):
        _ = run(make_target(tmp_path), tmp_path / "out", "m", adapter="codex", isolation="nono")


def test_the_environment_key_differs_between_backends(tmp_path: Path, nono_bin: Path) -> None:
    del nono_bin
    executable = fake_claude(tmp_path)
    sandbox = ClaudeCode("m", Budget(10, 120, 0), lambda: None, executable)
    nono = ClaudeCode("m", Budget(10, 120, 0), lambda: None, executable, isolation="nono")
    try:
        assert sandbox.environment_key() != nono.environment_key()
    finally:
        sandbox.close()
        nono.close()


def test_a_nono_role_needs_no_claude_login(tmp_path: Path, nono_bin: Path, host_login: Path) -> None:
    del nono_bin
    host_login.unlink()
    transport = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path), isolation="nono")
    try:
        assert transport.credential is None
        assert not list(Path(tempfile.gettempdir()).glob(f"{_claude.CONFIG_PREFIX}*"))
    finally:
        transport.close()


def test_a_resume_with_another_isolation_stops(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "out"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(CodedError, match="live model calls require --live"):
        _ = run(target, out, "local-test", approve_cases=case_hash, approve_budget=calls)
    record = cast(dict[str, object], json.loads((out / "run.json").read_text()))
    assert record["isolation"] == "claude"
    with pytest.raises(CodedError, match="--isolation differs") as differs:
        _ = run(target, out, "local-test", live=True, isolation="nono")
    assert differs.value.code == "run-config-differs"


def test_doctor_lists_every_unmet_nono_requirement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    claude = fake_claude(tmp_path)
    monkeypatch.setenv("PATH", f"{claude.parent}:/usr/bin:/bin")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(_nono, "landlock_abi", lambda: 3)
    monkeypatch.setattr(sys, "platform", "linux")
    report = doctor("claude", "nono")
    failed = [row for row in cast(list[dict[str, object]], report["checks"]) if row["status"] == "fail"]
    assert report["ok"] is False and len([row for row in failed if row["check"] == "nono"]) == 3
    assert all(row.get("fix") for row in failed)


@pytest.mark.parametrize("home", [True, False])
def test_a_wide_executable_directory_is_refused_before_the_profile_grants_it(
        nono_bin: Path, home: bool) -> None:
    del nono_bin
    directory = Path.home() if home else Path(tempfile.gettempdir())
    claude = directory / "claude"
    _ = claude.write_text("#!/bin/sh\n")
    transport = ClaudeCode("m", Budget(10, 120, 0), lambda: None, claude, isolation="nono")
    try:
        with pytest.raises(CodedError, match="cannot grant read access") as caught:
            _ = transport.invoke("hello", Candidate({"SKILL.md": "---\nname: skillz\ndescription: x\n---\n"}, ("SKILL.md",)))
    finally:
        transport.close()
    assert caught.value.code == "nono-unavailable"


def test_the_host_api_key_reaches_neither_the_profile_nor_the_argv(tmp_path: Path, nono_bin: Path) -> None:
    transport = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path), isolation="nono")
    try:
        _ = transport.invoke("hello", Candidate({"SKILL.md": "---\nname: skillz\ndescription: x\n---\n"}, ("SKILL.md",)))
    finally:
        transport.close()
    nono = logged(nono_bin.with_name("nono.log"))[-1]
    profile = cast(dict[str, dict[str, list[str]]], nono["profile"])
    assert "ANTHROPIC_API_KEY" not in profile["environment"]["allow_vars"]
    assert "sk-ant-host-secret" not in json.dumps(nono["argv"]) + json.dumps(nono["profile"])


def test_a_nono_role_is_refused_on_macos(tmp_path: Path, nono_bin: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del nono_bin
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(CodedError) as caught:
        _ = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path), isolation="nono")
    assert caught.value.code == "nono-unavailable"


@pytest.fixture
def nono_sandbox_passes(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pass the OS sandbox probe, and use a short temp root so the Unix-socket probe path fits AF_UNIX.

    The nono preflight binds an abstract socket, which exists only on Linux, so the test skips elsewhere.
    """
    if not LINUX_HOST:
        pytest.skip("abstract Unix sockets exist only on Linux")
    directory = Path(tempfile.mkdtemp(prefix="skz", dir="/tmp"))
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str]) -> tuple[int, str]:
        del self, workspace, argv
        return 0, "isolation-ok\n"
    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)
    yield
    shutil.rmtree(directory, ignore_errors=True)


def test_the_nono_preflight_probes_pathname_abstract_and_claude_writes(
        tmp_path: Path, nono_bin: Path, nono_sandbox_passes: None) -> None:
    del nono_sandbox_passes
    executable = fake_claude(tmp_path)
    transport = ClaudeCode("m", Budget(10, 120, 0), lambda: None, executable, isolation="nono")
    try:
        evidence = transport.preflight()
    finally:
        transport.close()
    prompt = cast(str, logged(executable.with_name("claude.log"))[0]["prompt"])
    assert evidence["unix_socket_filter"] == "nono"
    assert "\\0skillz-" in prompt and "/.claude/skills/" in prompt
    assert nono_bin.with_name("nono.log").exists()


def test_the_nono_preflight_passes_when_nono_denies_the_read_tool(
        tmp_path: Path, nono_bin: Path, nono_sandbox_passes: None) -> None:
    del nono_bin, nono_sandbox_passes
    transport = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path), isolation="nono")
    try:
        evidence = transport.preflight()
    finally:
        transport.close()
    assert evidence["isolation"] == "passed"


def test_the_nono_preflight_stops_when_the_read_tool_returns_the_sealed_file(
        tmp_path: Path, nono_bin: Path, nono_sandbox_passes: None) -> None:
    del nono_bin, nono_sandbox_passes
    transport = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path, "read-tool-escape"),
                           isolation="nono")
    try:
        with pytest.raises(CodedError, match="the Read tool reached a path outside the workspace") as caught:
            _ = transport.preflight()
    finally:
        transport.close()
    assert caught.value.code == "preflight-leak"


@pytest.mark.parametrize(("mode", "reason"), [
    ("abstract-open", "reached a runner-owned host Unix socket"),
    ("abstract-skipped", "Unix-socket command output is missing"),
])
def test_the_nono_preflight_stops_on_an_abstract_socket_failure(
        tmp_path: Path, nono_bin: Path, nono_sandbox_passes: None, mode: str, reason: str) -> None:
    del nono_bin, nono_sandbox_passes
    transport = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path, mode), isolation="nono")
    try:
        with pytest.raises(NetworkIsolationFailed, match=reason):
            _ = transport.preflight()
    finally:
        transport.close()


def test_the_nono_preflight_stops_on_a_spoofed_builtin_plugin(
        tmp_path: Path, nono_bin: Path, nono_sandbox_passes: None) -> None:
    del nono_bin, nono_sandbox_passes
    transport = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path, "spoofed-plugin"), isolation="nono")
    try:
        with pytest.raises(CodedError, match="foreign plugin") as caught:
            _ = transport.preflight()
    finally:
        transport.close()
    assert caught.value.code == "preflight-leak"


def test_the_nono_preflight_stops_when_the_claude_directory_is_writable(
        tmp_path: Path, nono_bin: Path, nono_sandbox_passes: None, monkeypatch: pytest.MonkeyPatch) -> None:
    del nono_bin, nono_sandbox_passes
    original = _nono.profile

    def writable(**kwargs: object) -> dict[str, object]:
        document = original(**kwargs)  # pyright: ignore[reportArgumentType]
        cast(dict[str, list[str]], document["filesystem"])["allow"].append(str(cast(Path, kwargs["project"]) / ".claude"))
        return document
    monkeypatch.setattr(_nono, "profile", writable)
    transport = ClaudeCode("m", Budget(10, 120, 0), lambda: None, fake_claude(tmp_path, "write-claude"), isolation="nono")
    try:
        with pytest.raises(CodedError, match="write isolation failed"):
            _ = transport.preflight()
    finally:
        transport.close()

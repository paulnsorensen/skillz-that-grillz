"""Codex task temp files live beside the workspace, outside Git discovery (gh-305)."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import sys
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments._candidate import make_workspace
from skillz_experiments.harness._codex import Codex, configuration
from skillz_experiments.harness._isolation import SOCKET_PATH_LIMIT, private_task_tmpdir, task_tmpdir

FAKE = '#!/bin/sh\n/usr/bin/env\nfor argument in "$@"; do echo "ARG=$argument"; done\n'


@pytest.fixture
def adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Codex]:
    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")
    host = tmp_path / "host-codex"
    host.mkdir()
    _ = (host / "auth.json").write_text('{"login": "host"}')
    fake = tmp_path / "bin/codex"
    fake.parent.mkdir()
    _ = fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(host))
    transport = Codex("m", Budget(10, 120, 0), lambda: None)
    try:
        yield transport
    finally:
        transport.close()


def _git(arguments: list[str], cwd: Path, ceiling: Path) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_CEILING_DIRECTORIES"] = str(ceiling)
    return subprocess.run(["git", *arguments], cwd=cwd, env=environment, capture_output=True, text=True, check=False)


@pytest.mark.ac("gh-719:AC-1")
def test_fixture_commit_excludes_runtime_directories(tmp_path: Path, adapter: Codex) -> None:
    workspace = make_workspace(tmp_path / "task" / "workspace")
    environment = adapter._environment(workspace)  # pyright: ignore[reportPrivateUsage]
    for path in (workspace / "f", workspace / "home/cache", workspace / "tmp/cache", Path(environment["TMPDIR"]) / "cache"):
        _ = path.write_text("fixture\n")
    for arguments in (["init", "-q", "-b", "main"], ["config", "--global", "user.email", "t@t"],
                      ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        _ = subprocess.run(["git", *arguments], cwd=workspace, env=environment, capture_output=True, text=True,
                           check=True, timeout=10)
    tracked = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=workspace, env=environment,
                             capture_output=True, text=True, check=True, timeout=10)
    assert tracked.stdout.splitlines() == ["f"]


@pytest.mark.ac("gh-719:AC-2")
def test_task_home_excludes_only_runtime_directories(tmp_path: Path, adapter: Codex) -> None:
    workspace = make_workspace(tmp_path / "task" / "workspace")
    environment = adapter._environment(workspace)  # pyright: ignore[reportPrivateUsage]
    home = Path(environment["HOME"])
    assert (home / ".config/git/ignore").read_text().splitlines() == ["/home/", "/tmp/"]
    assert not (home / ".config/git/config").exists()


@pytest.mark.ac("gh-305:AC-1")
def test_task_temp_dirs_are_outside_git_discovery(tmp_path: Path, adapter: Codex) -> None:
    workspace = make_workspace(tmp_path / "task" / "workspace")
    assert _git(["init", "-q"], workspace, tmp_path).returncode == 0
    env = adapter._environment(workspace)  # pyright: ignore[reportPrivateUsage]
    assert Path(env["TMPDIR"]) == tmp_path / "task" / "tmp"
    assert Path(env["TMPDIR"]).is_dir()
    made = Path(tempfile.mkdtemp(dir=env["TMPDIR"]))
    inside = _git(["rev-parse", "--show-toplevel"], made, tmp_path)
    assert inside.returncode == 128 and inside.stdout == ""
    control = _git(["rev-parse", "--show-toplevel"], workspace / "tmp", tmp_path)
    assert control.returncode == 0 and control.stdout.strip() == str(workspace.resolve())


@pytest.mark.ac("gh-305:AC-2")
def test_configuration_grants_only_the_task_tmpdir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    executable = Path("/usr/bin/codex")
    arguments = configuration(workspace, executable, [])
    tmpdir = tmp_path / "tmp"
    filesystem = {":root": "deny", ":minimal": "read", ":slash_tmp": "deny", str(workspace): "write",
                  str(workspace / ".agents"): "read", str(workspace / ".git"): "write", str(tmpdir): "write",
                  str(tmpdir / ".git"): "write", str(executable): "read"}
    inline = "{" + ",".join(f"{json.dumps(key)}={json.dumps(value)}" for key, value in filesystem.items()) + "}"
    assert 'permissions.skillz={extends=":workspace",filesystem=' + inline + ",network={enabled=false}}" in arguments
    policy = next(item for item in arguments if item.startswith("shell_environment_policy.set="))
    assert '"TMPDIR"=' + json.dumps(str(tmpdir)) in policy
    joined = " ".join(arguments)
    assert json.dumps(str(tmp_path)) + '="write"' not in joined
    assert json.dumps(str(workspace / "tmp")) not in joined
    assert '"/TASK/tmp"="write"' in " ".join(configuration(Path("/TASK/workspace"), executable, []))


@pytest.mark.ac("gh-305:AC-2")
def test_configuration_grants_workspace_git_write_and_keeps_agents_read_only(tmp_path: Path) -> None:
    workspace = tmp_path / "task" / "workspace"
    joined = " ".join(configuration(workspace, Path("/usr/bin/codex"), []))
    assert json.dumps(str(workspace / ".git")) + '="write"' in joined
    assert json.dumps(str(workspace / ".agents")) + '="read"' in joined
    assert json.dumps(str(workspace / ".agents")) + '="write"' not in joined


@pytest.mark.ac("gh-305:AC-3")
def test_sandbox_and_environment_use_the_task_tmpdir(tmp_path: Path, adapter: Codex) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code, stdout, _ = adapter.sandbox(workspace, ["/usr/bin/true"])
    assert code == 0
    assert f"ARG=TMPDIR={tmp_path}/tmp" in stdout
    assert f"TMPDIR={tmp_path}/tmp" in stdout.splitlines()
    assert f"{tmp_path}/workspace/tmp" not in stdout
    assert (tmp_path / "tmp").is_dir()


@pytest.mark.ac("gh-305:AC-4")
def test_task_tmpdir_fits_the_af_unix_limit() -> None:
    w = Path(tempfile.gettempdir()) / "skillz-task-abcdefgh" / "workspace"
    assert task_tmpdir(w) == w.parent / "tmp"
    assert len(str(task_tmpdir(w) / "u.sock").encode()) <= SOCKET_PATH_LIMIT
    assert len(str(task_tmpdir(w)).encode()) < len(str(w / "tmp").encode())


@pytest.mark.ac("gh-305:AC-5")
def test_task_tmpdir_refuses_a_workspace_without_a_private_parent() -> None:
    shared = Path("/tmp/skillz-contract-abcdefgh")
    with pytest.raises(ValueError):
        _ = task_tmpdir(shared)
    with pytest.raises(ValueError):
        _ = configuration(shared, Path("/usr/bin/codex"), [])


@pytest.mark.ac("gh-305:AC-5")
def test_private_task_tmpdir_refuses_an_unprivate_directory(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "tmp").mkdir(mode=0o755)
    (shared / "tmp").chmod(0o755)
    with pytest.raises(ValueError):
        _ = private_task_tmpdir(shared / "workspace")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "elsewhere").mkdir()
    (linked / "tmp").symlink_to(linked / "elsewhere")
    with pytest.raises(ValueError):
        _ = private_task_tmpdir(linked / "workspace")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    made = private_task_tmpdir(fresh / "workspace")
    assert made == fresh / "tmp" and stat.S_IMODE(made.lstat().st_mode) == 0o700


@pytest.mark.ac("gh-305:AC-2")
@pytest.mark.parametrize("root", ["workspace", "tmp"])
@pytest.mark.parametrize("unsafe", ["symlink", "dangling", "file"])
def test_launch_rejects_unsafe_git_roots(tmp_path: Path, adapter: Codex, root: str, unsafe: str) -> None:
    workspace = make_workspace(tmp_path / "task" / "workspace")
    target = workspace if root == "workspace" else tmp_path / "task" / "tmp"
    _ = private_task_tmpdir(workspace)
    git = target / ".git"
    if unsafe == "file":
        _ = git.write_text("not a directory")
    else:
        destination = workspace / ".agents" if unsafe == "symlink" else tmp_path / "absent"
        git.symlink_to(destination)
    with pytest.raises(ValueError, match=r"\.git"):
        _ = adapter._environment(workspace)  # pyright: ignore[reportPrivateUsage]
    assert git.is_symlink() if unsafe != "file" else git.is_file()


@pytest.mark.ac("gh-305:AC-2")
def test_launch_prepares_both_git_roots_without_config_side_effects(tmp_path: Path, adapter: Codex) -> None:
    workspace = make_workspace(tmp_path / "task" / "workspace")
    roots = (workspace / ".git", tmp_path / "task" / "tmp" / ".git")
    arguments = configuration(workspace, Path("/usr/bin/codex"), [])
    assert all(not root.exists() for root in roots)
    _ = configuration(Path("/TASK/workspace"), Path("/usr/bin/codex"), [])
    _ = adapter._environment(workspace)  # pyright: ignore[reportPrivateUsage]
    assert all(root.is_dir() and not root.is_symlink() for root in roots)
    assert configuration(workspace, Path("/usr/bin/codex"), []) == arguments
    for root in roots:
        root.rmdir()
    _ = adapter._environment(workspace)  # pyright: ignore[reportPrivateUsage]
    assert all(root.is_dir() and not root.is_symlink() for root in roots)


@pytest.mark.ac("gh-305:AC-2")
@pytest.mark.skipif(sys.platform != "linux", reason="the Codex Linux mount boundary is required")
def test_real_codex_sandbox_keeps_agents_read_only_with_reused_policy(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness_bin: Path) -> None:
    path = os.pathsep.join(entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                           if Path(entry).resolve() != harness_bin.resolve())
    found = shutil.which("codex", path=path)
    if found is None:
        pytest.skip("real Codex CLI is absent outside pytest harness bin")
    monkeypatch.setenv("PATH", path)
    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")
    executable = Path(found).resolve()
    assert executable != (harness_bin / "codex").resolve()
    workspace = make_workspace(tmp_path / "task with space" / "workspace")
    sentinel = workspace / ".agents" / "sentinel"
    _ = sentinel.write_text("original")
    home = tmp_path / "codex-home"
    home.mkdir()
    transport = Codex("m", Budget(10, 120, 0), lambda: None)
    try:
        environment = transport._environment(workspace)  # pyright: ignore[reportPrivateUsage]
        environment["CODEX_HOME"] = str(home)
        policy = configuration(workspace, executable, [])
        command = [str(executable), "sandbox", *policy, "-P", "skillz", "--include-managed-config",
                   "-C", str(workspace), "--", "/usr/bin/env", "-i", "PATH=/usr/bin:/bin",
                   f"HOME={workspace / 'home'}", f"TMPDIR={environment['TMPDIR']}", "LANG=C.UTF-8", "/bin/sh", "-c"]

        def run(script: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([*command, script], cwd=workspace, env=environment,
                                  capture_output=True, text=True, timeout=30, check=False)

        for root in (workspace / ".git", Path(environment["TMPDIR"]) / ".git"):
            target = shlex.quote(str(root))
            agents = shlex.quote(str(workspace / '.agents'))
            attempted = run(f"rmdir {target} && ln -s {agents} {target}")
            assert attempted.returncode != 0, attempted.stderr
            assert f"'{root}': Device or resource busy" in attempted.stderr
            assert root.is_dir() and not root.is_symlink()
            denied = run("printf tampered > .agents/sentinel")
            assert denied.returncode != 0, denied.stderr
            assert sentinel.read_text() == "original"
        initialized = run("git init -q -b main && git rev-parse --is-inside-work-tree")
        assert initialized.returncode == 0, initialized.stderr
        assert initialized.stdout.strip() == "true"
        denied = run("printf tampered > .agents/sentinel")
        assert denied.returncode != 0, denied.stderr
        assert sentinel.read_text() == "original"
    finally:
        transport.close()

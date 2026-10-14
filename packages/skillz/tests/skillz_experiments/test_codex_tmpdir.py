"""Codex task temp files live beside the workspace, so they have no sandbox `.git` ancestor (gh-305)."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments._candidate import make_workspace
from skillz_experiments.harness._codex import Codex, configuration, task_tmpdir
from skillz_experiments.harness._isolation import SOCKET_PATH_LIMIT

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


@pytest.mark.ac("gh-305:AC-1")
def test_task_temp_dirs_have_no_git_ancestor(tmp_path: Path, adapter: Codex) -> None:
    workspace = make_workspace(tmp_path / "task" / "workspace")
    assert _git(["init", "-q"], workspace, tmp_path).returncode == 0
    env = adapter._environment(workspace)  # pyright: ignore[reportPrivateUsage]
    assert Path(env["TMPDIR"]) == tmp_path / "task" / "tmp"
    assert Path(env["TMPDIR"]).is_dir()
    made = Path(tempfile.mkdtemp(dir=env["TMPDIR"]))
    for path in (made, *made.parents):
        if path == tmp_path or tmp_path not in path.parents:
            break
        assert not (path / ".git").exists()
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
                  str(workspace / ".agents"): "read", str(tmpdir): "write", str(tmpdir / ".git"): "write",
                  str(executable): "read"}
    inline = "{" + ",".join(f"{json.dumps(key)}={json.dumps(value)}" for key, value in filesystem.items()) + "}"
    assert 'permissions.skillz={extends=":workspace",filesystem=' + inline + ",network={enabled=false}}" in arguments
    policy = next(item for item in arguments if item.startswith("shell_environment_policy.set="))
    assert '"TMPDIR"=' + json.dumps(str(tmpdir)) in policy
    joined = " ".join(arguments)
    assert json.dumps(str(tmp_path)) + '="write"' not in joined
    assert json.dumps(str(workspace / "tmp")) not in joined
    assert '"/TASK/tmp"="write"' in " ".join(configuration(Path("/TASK/workspace"), executable, []))


@pytest.mark.ac("gh-305:AC-3")
def test_sandbox_and_environment_use_the_task_tmpdir(tmp_path: Path, adapter: Codex) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code, stdout, _ = adapter.sandbox(workspace, ["/usr/bin/true"])
    assert code == 0
    assert f"ARG=TMPDIR={tmp_path}/tmp" in stdout
    assert f"TMPDIR={tmp_path}/tmp" in stdout
    assert f"{tmp_path}/workspace/tmp" not in stdout
    assert (tmp_path / "tmp").is_dir()


@pytest.mark.ac("gh-305:AC-4")
def test_task_tmpdir_fits_the_af_unix_limit() -> None:
    w = Path("/tmp/skillz-task-abcdefgh/workspace")
    assert task_tmpdir(w) == Path("/tmp/skillz-task-abcdefgh/tmp")
    assert len(str(task_tmpdir(w) / "u.sock").encode()) <= SOCKET_PATH_LIMIT
    assert len(str(task_tmpdir(w)).encode()) < len(str(w / "tmp").encode())


@pytest.mark.ac("gh-305:AC-5")
def test_task_tmpdir_refuses_a_workspace_without_a_private_parent() -> None:
    shared = Path("/tmp/skillz-contract-abcdefgh")
    with pytest.raises(ValueError):
        _ = task_tmpdir(shared)
    with pytest.raises(ValueError):
        _ = configuration(shared, Path("/usr/bin/codex"), [])

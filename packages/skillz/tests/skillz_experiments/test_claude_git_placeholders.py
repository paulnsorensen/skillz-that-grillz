from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from in_process_claude import in_process_claude
from skillz_experiments._budget import Budget
from skillz_experiments._candidate import make_workspace
from skillz_experiments.harness import Configuration
from skillz_experiments.harness._claude import ClaudeCode
from skillz_experiments.harness._claude._git import PLACEHOLDERS

pytestmark = pytest.mark.usefixtures("host_login", "claude_in_process")


def _bwrap_works() -> bool:
    tool = shutil.which("bwrap")
    if tool is None or shutil.which("git") is None:
        return False
    probe = subprocess.run([tool, "--unshare-all", "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
                            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
                            "/usr/bin/python3", "-c", "pass"], capture_output=True, check=False)
    return probe.returncode == 0


needs_bwrap = pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap or git is unavailable")


def _launch(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Prepare a task workspace through the real `ClaudeCode._start` and return it with the environment Claude gets."""
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(in_process_claude(tmp_path))]}))
    session = Configuration.load(config, "claude-test-model").create("claude-test-model", Budget(10, 120, 0), lambda: None, None)
    try:
        task = cast(ClaudeCode, session.transports["task"])
        workspace = make_workspace(tmp_path / "run" / "workspace")
        _, environment = task._start(workspace, None, [])  # pyright: ignore[reportPrivateUsage]
    finally:
        session.close()
    return workspace, environment


def _sandboxed(workspace: Path, environment: dict[str, str], command: str) -> subprocess.CompletedProcess[str]:
    """Run one command under the binds that Claude Code 2.1.319 builds for a sandboxed Bash command.

    The root placeholders bind to `/dev/null`, `home/.config` binds read-only, and a `.git` that exists binds `hooks` and
    `config` read-only.
    """
    config = workspace / "home/.config"
    config.mkdir(exist_ok=True)
    argv = ["bwrap", "--die-with-parent", "--unshare-net", "--ro-bind", "/", "/", "--tmpfs", "/tmp",
            "--bind", str(workspace), str(workspace), "--ro-bind", str(config), str(config)]
    for name in PLACEHOLDERS:
        argv += ["--ro-bind", "/dev/null", str(workspace / name)]
    if (workspace / ".git").is_dir():
        argv += ["--ro-bind", str(workspace / ".git/hooks"), str(workspace / ".git/hooks"),
                 "--ro-bind", str(workspace / ".git/config"), str(workspace / ".git/config")]
    argv += ["--dev", "/dev", "--unshare-pid", "--unshare-user", "--cap-drop", "ALL", "--proc", "/proc",
             "bash", "-c", f"cd {workspace} && {command}"]
    return subprocess.run(argv, env={"PATH": os.environ["PATH"], "HOME": environment["HOME"], "TMPDIR": environment["TMPDIR"]},
                          capture_output=True, text=True, check=False, timeout=60)


@pytest.mark.ac("gh-703:AC-1")
def test_the_task_home_excludes_every_root_placeholder_and_no_runner_directory(tmp_path: Path) -> None:
    workspace, environment = _launch(tmp_path)
    ignore = Path(environment["HOME"]) / ".config/git/ignore"
    assert ignore.read_text().splitlines() == [f"/{name}" for name in PLACEHOLDERS]
    assert {".gitconfig", ".bashrc", ".bash_profile", ".vscode", ".idea"} <= set(PLACEHOLDERS)
    assert not {".agents", ".claude"} & set(PLACEHOLDERS)
    assert not (workspace / "home/.config/git/config").exists()


@needs_bwrap
@pytest.mark.ac("gh-703:AC-1")
def test_git_add_all_skips_the_sandbox_placeholders(tmp_path: Path) -> None:
    workspace, environment = _launch(tmp_path)
    ran = _sandboxed(workspace, environment, "git init -q -b main && echo x > f && git add -A && git ls-files")
    tracked = ran.stdout.split()
    assert (ran.returncode, ran.stderr, "f" in tracked) == (0, "", True)
    assert not set(PLACEHOLDERS) & set(tracked)


@needs_bwrap
@pytest.mark.ac("gh-703:AC-2")
def test_a_fixture_commits_with_the_global_identity_after_the_repository_config_is_locked(tmp_path: Path) -> None:
    workspace, environment = _launch(tmp_path)
    assert _sandboxed(workspace, environment, "git init -q -b main").returncode == 0
    local = _sandboxed(workspace, environment, "git config user.email a@b")
    assert local.returncode != 0 and "Device or resource busy" in local.stderr
    ran = _sandboxed(workspace, environment, "git config --global user.email a@b && git config --global user.name n "
                     + "&& echo x > f && git add -A && git commit -qm init && git log --format=%ae%n%an%n%s")
    assert (ran.returncode, ran.stdout.split(), ran.stderr) == (0, ["a@b", "n", "init"], "")

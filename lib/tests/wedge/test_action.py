"""AC-W7: the composite action preserves wedge output and opens the lock PR."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

_ACTION = Path(__file__).resolve().parents[3] / "actions" / "wedge" / "action.yml"


def _run_block(name: str) -> str:
    lines = _ACTION.read_text().splitlines()
    marker = f"    - name: {name}"
    start = lines.index(marker)
    run_line = next(index for index in range(start, len(lines)) if lines[index].startswith("      run: "))
    body: list[str] = []
    for line in lines[run_line + 1 :]:
        if line and not line.startswith("        "):
            break
        body.append(line[8:] if line else "")
    return "\n".join(body) + "\n"


def _write_command(directory: Path, name: str, body: str) -> None:
    path = directory / name
    _ = path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_wedge(tmp_path: Path, uv_body: str) -> subprocess.CompletedProcess[str]:
    commands = tmp_path / "commands"
    commands.mkdir()
    _write_command(commands, "uv", uv_body)
    output = tmp_path / "github-output"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env = {
        **os.environ,
        "PATH": f"{commands}:{os.environ['PATH']}",
        "GITHUB_ACTION_PATH": str(_ACTION.parents[2]),
        "GITHUB_OUTPUT": str(output),
        "WEDGE_COMMAND": "check",
        "WEDGE_ROOTS": "workspace",
        "RUNNER_TEMP": str(tmp_path / "runner-temp"),
    }
    (tmp_path / "runner-temp").mkdir()
    return subprocess.run(
        ["bash", "-c", _run_block("Run wedge")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )


@pytest.mark.ac("AC-W7")
def test_action_keeps_success_json_separate_from_uv_stderr(tmp_path: Path) -> None:
    result = _run_wedge(tmp_path, 'printf \'{"status":"ok"}\\n\'; printf \'uv startup\\n\' >&2')

    assert result.returncode == 0
    assert result.stdout == '{"status":"ok"}\n'
    assert "uv startup" in result.stderr
    assert '{"status":"ok"}' in (tmp_path / "github-output").read_text()


@pytest.mark.ac("AC-W7")
def test_action_preserves_failed_json_and_exit_status(tmp_path: Path) -> None:
    result = _run_wedge(tmp_path, 'printf \'{"error":"bad"}\\n\' >&2; exit 7')

    assert result.returncode == 7
    assert result.stdout == '{"error":"bad"}\n'
    assert '{"error":"bad"}' in result.stderr
    assert '{"error":"bad"}' in (tmp_path / "github-output").read_text()


@pytest.mark.ac("AC-W7")
def test_action_installs_gh_in_runner_temp_without_sudo(tmp_path: Path) -> None:
    commands = tmp_path / "commands"
    commands.mkdir()
    _write_command(commands, "curl", 'while (($#)); do if [[ "$1" == -o ]]; then output="$2"; fi; shift; done; printf "archive" >"$output"')
    _write_command(commands, "sha256sum", "exit 0")
    _write_command(
        commands,
        "tar",
        'mkdir -p "${RUNNER_TEMP}/gh_${GH_VERSION}_linux_amd64/bin"; printf \'#!/usr/bin/env bash\\nprintf "gh ${GH_VERSION}\\n"\\n\' >"${RUNNER_TEMP}/gh_${GH_VERSION}_linux_amd64/bin/gh"',
    )
    _write_command(commands, "sudo", "exit 99")
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    github_path = tmp_path / "github-path"
    env = {
        **os.environ,
        "PATH": f"{commands}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_PATH": str(github_path),
        "GH_VERSION": "2.92.0",
        "GH_SHA256": "unused",
    }

    result = subprocess.run(
        ["bash", "-c", _run_block("Install pinned gh CLI")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    installed = runner_temp / "bin" / "gh"
    assert installed.is_file()
    assert os.access(installed, os.X_OK)
    assert github_path.read_text().strip() == str(runner_temp / "bin")
    assert "sudo" not in result.stderr


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _run_lock_pr(tmp_path: Path, *, change_lock: bool) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """Run the lock-PR step in a real checkout against a bare file:// remote."""
    server = tmp_path / "server"
    remote = server / "owner" / "repo.git"
    remote.mkdir(parents=True)
    _ = _git(remote, "init", "--quiet", "--bare")
    checkout = tmp_path / "checkout"
    lock = checkout / "skills" / "demo" / "scripts" / "demo.wedge.json"
    lock.parent.mkdir(parents=True)
    _ = lock.write_text('{"sha256": null}\n')
    _ = _git(checkout.parent, "init", "--quiet", str(checkout))
    _ = _git(checkout, "add", ".")
    _ = _git(checkout, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--quiet", "-m", "init")
    if change_lock:
        _ = lock.write_text('{"sha256": "' + "a" * 64 + '"}\n')
    commands = tmp_path / "commands"
    commands.mkdir()
    gh_log = tmp_path / "gh.log"
    _write_command(
        commands,
        "gh",
        f'printf \'%s\\n\' "$(printf \'%s\\x1f\' "$@")" >>{gh_log}\n'
        + 'if [[ "$1 $2" == "pr create" ]]; then echo https://github.com/owner/repo/pull/7; fi',
    )
    output = tmp_path / "github-output"
    env = {
        **os.environ,
        "PATH": f"{commands}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_SERVER_URL": f"file://{server}",
        "GH_TOKEN": "token",
        "WEDGE_REPO": "owner/repo",
        "WEDGE_LOCK_BRANCH": "wedge/lock-update",
        "WEDGE_LOCK_BASE": "main",
    }
    result = subprocess.run(
        ["bash", "-c", _run_block("Open lock pull request")],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
    )
    return result, remote, gh_log


def _gh_calls(gh_log: Path) -> list[list[str]]:
    if not gh_log.exists():
        return []
    return [line.split("\x1f")[:-1] for line in gh_log.read_text().splitlines()]


@pytest.mark.ac("AC-W7")
def test_lock_pr_pushes_recorded_locks_and_enables_auto_merge(tmp_path: Path) -> None:
    result, remote, gh_log = _run_lock_pr(tmp_path, change_lock=True)

    assert result.returncode == 0, result.stderr
    pushed = _git(remote, "show", "wedge/lock-update:skills/demo/scripts/demo.wedge.json")
    assert json.loads(pushed) == {"sha256": "a" * 64}
    assert _git(remote, "log", "-1", "--format=%s", "wedge/lock-update").strip() == (
        "chore(wedge): record published .pyz digests"
    )
    calls = _gh_calls(gh_log)
    assert [call[:2] for call in calls] == [["pr", "list"], ["pr", "create"], ["pr", "merge"]]
    assert calls[2][2:] == ["https://github.com/owner/repo/pull/7", "--repo", "owner/repo", "--auto", "--squash"]
    assert "url=https://github.com/owner/repo/pull/7" in (tmp_path / "github-output").read_text()


@pytest.mark.ac("AC-W7")
def test_lock_pr_does_nothing_when_no_lock_changed(tmp_path: Path) -> None:
    result, remote, gh_log = _run_lock_pr(tmp_path, change_lock=False)

    assert result.returncode == 0, result.stderr
    assert _git(remote, "branch", "--list").strip() == ""
    assert _gh_calls(gh_log) == []
    assert (tmp_path / "github-output").read_text() == "url=\n"

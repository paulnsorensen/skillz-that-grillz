"""gh-700: no Claude role starts where Claude Code would take a `.git` above the workspace as its repository.

Claude Code takes the nearest ancestor with a `.git` entry as the repository of its working directory. Its sandbox then
creates `branches`, `remotes`, `info/.claude-sandbox-noreplace`, and `config.worktree` in that `.git` on the host. The
runner puts every workspace under the temporary directory, so a shared `/tmp/.git` grew a skeleton during each run.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from in_process_claude import in_process_claude
from skillz_experiments._budget import Budget
from skillz_experiments._cases import CodedError
from skillz_experiments.harness._claude._login import check_ancestors
from skillz_experiments.harness._claude._transport import ClaudeCode

pytestmark = pytest.mark.usefixtures("host_login", "claude_in_process")


@pytest.fixture
def sandbox_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the OS sandbox probe, which has its own tests, so the preflight reaches its Claude steps."""
    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str], seconds: int | None = None) -> tuple[int, str, str]:
        del self, workspace, argv, seconds
        return 0, "isolation-ok\n", ""
    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)


@pytest.mark.ac("gh-700:AC-1")
@pytest.mark.parametrize("kind", ["directory", "empty directory", "file"])
def test_a_git_entry_above_the_workspace_stops_the_preflight_before_any_claude_process(
        tmp_path: Path, sandbox_passes: None, kind: str) -> None:
    del sandbox_passes
    entry = Path(tempfile.gettempdir()) / ".git"
    if kind == "file":
        _ = entry.write_text("gitdir: /elsewhere\n")
    else:
        (entry / "info").mkdir(parents=True)
        if kind == "empty directory":
            (entry / "info").rmdir()
    executable = in_process_claude(tmp_path)
    budget = Budget(10, 120, 0)
    transport = ClaudeCode("m", budget, lambda: None, executable)
    try:
        with pytest.raises(CodedError, match=r"\.git") as caught:
            _ = transport.preflight()
    finally:
        transport.close()
    assert caught.value.code == "preflight-leak" and "TMPDIR" in str(caught.value)
    assert budget.calls == 0 and not executable.with_name("claude.log").exists()
    # The runner leaves the foreign entry as it found it.
    left = entry.read_text() if kind == "file" else sorted(path.name for path in entry.iterdir())
    assert left == {"file": "gitdir: /elsewhere\n", "directory": ["info"], "empty directory": []}[kind]


@pytest.mark.ac("gh-700:AC-2")
def test_a_git_entry_in_the_workspace_itself_or_none_above_it_passes(tmp_path: Path) -> None:
    workspace = tmp_path / "private" / "workspace"
    (workspace / ".git").mkdir(parents=True)
    check_ancestors(workspace)
    (tmp_path / ".git").mkdir()
    with pytest.raises(CodedError, match=str(tmp_path / ".git")) as caught:
        check_ancestors(workspace)
    assert caught.value.code == "preflight-leak"

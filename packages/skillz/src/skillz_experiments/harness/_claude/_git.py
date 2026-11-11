from __future__ import annotations

from pathlib import Path

from skillz_experiments.harness._git import write_ignore


# Root entries that Claude Code binds to `/dev/null` in each sandboxed Bash command. A fixture file is never a dotfile,
# so the exclude list hides no case file. `.agents` and `.claude` are real runner-staged directories and stay visible.
PLACEHOLDERS = (".gitconfig", ".gitmodules", ".bashrc", ".bash_profile", ".zshrc", ".zprofile", ".profile", ".ripgreprc",
                ".vscode", ".idea", ".mcp.json")


def hide_placeholders(home: Path) -> None:
    """Exclude Claude placeholders and runtime directories from `git add -A`.

    The runner sets `HOME` to `home` and leaves `XDG_CONFIG_HOME` unset, so Git reads `home/.config/git/ignore`.
    Claude Code binds `home/.config` read-only, so the file must exist before the sandbox starts.
    Only `ignore` goes there: a `home/.config/git/config` file would make `git config --global` write to a read-only path.
    """
    write_ignore(home, PLACEHOLDERS)

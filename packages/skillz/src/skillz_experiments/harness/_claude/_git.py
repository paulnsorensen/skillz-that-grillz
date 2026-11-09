from __future__ import annotations

from pathlib import Path


# Root entries that Claude Code binds to `/dev/null` in each sandboxed Bash command. A fixture file is never a dotfile,
# so the exclude list hides no case file. `.agents` and `.claude` are real runner-staged directories and stay visible.
PLACEHOLDERS = (".gitconfig", ".gitmodules", ".bashrc", ".bash_profile", ".zshrc", ".zprofile", ".profile", ".ripgreprc",
                ".vscode", ".idea", ".mcp.json")
# Runtime-owned workspace directories: the task `HOME` and `TMPDIR`. `_cases` refuses them as fixture roots, so hiding them
# hides no case file, and `git add -A` does not commit the exclude file or a `git config --global` identity.
RUNTIME = ("home", "tmp")


def ignore_lines() -> list[str]:
    """Return the global exclude patterns: each root placeholder, then each runtime-owned directory."""
    return [f"/{name}" for name in PLACEHOLDERS] + [f"/{name}/" for name in RUNTIME]


def hide_placeholders(home: Path) -> None:
    """Write the global Git exclude file that Git reads by default in `home`, so `git add -A` skips the placeholders and `RUNTIME`.

    The runner sets `HOME` to `home` and leaves `XDG_CONFIG_HOME` unset, so Git reads `home/.config/git/ignore`.
    Claude Code binds `home/.config` read-only, so the file must exist before the sandbox starts.
    Only `ignore` goes there: a `home/.config/git/config` file would make `git config --global` write to a read-only path.
    """
    directory = home / ".config/git"
    directory.mkdir(parents=True, exist_ok=True)
    _ = (directory / "ignore").write_text("".join(f"{line}\n" for line in ignore_lines()), encoding="utf-8")

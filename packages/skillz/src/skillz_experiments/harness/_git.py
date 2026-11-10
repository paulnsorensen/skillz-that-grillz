from pathlib import Path

from skillz_experiments.harness._isolation import prepare_git_root

# Fixture roots cannot use these runtime-owned workspace names.
RUNTIME = ("home", "tmp")


def write_ignore(home: Path, placeholders: tuple[str, ...] = ()) -> None:
    """Write Git's default global excludes before the task sandbox starts."""
    patterns = [f"/{name}" for name in placeholders] + [f"/{name}/" for name in RUNTIME]
    directory = home / ".config/git"
    directory.mkdir(parents=True, exist_ok=True)
    _ = (directory / "ignore").write_text("".join(f"{line}\n" for line in patterns), encoding="utf-8")


def prepare_codex_git(workspace: Path, tmpdir: Path) -> None:
    prepare_git_root(workspace / ".git")
    prepare_git_root(tmpdir / ".git")
    write_ignore(workspace / "home")

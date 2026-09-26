"""The ``wedge`` CLI: build, lock, check, and publish skill .pyz packages.

Built with ``fromargs.App`` (dogfood): every command returns data and
``run`` prints it as one JSON document on stdout.
"""

from __future__ import annotations

from pathlib import Path

import fromargs

from wedge._build import build as build_pyz
from wedge._discover import discover_skills
from wedge._lock import check as check_skills
from wedge._lock import lock as lock_skill
from wedge._publish import publish as publish_skills

DEFAULT_ROOT = "skills"

app = fromargs.App("wedge", help="Shiv a fromargs CLI into a skill as a content-addressed .pyz.")


def _resolve_skill_dirs(root: list[str] | None, skill_dir: list[str]) -> list[Path]:
    """Positional skill dirs win; otherwise glob ``--root`` (default ``skills``)."""
    if skill_dir:
        return [Path(path) for path in skill_dir]
    roots = [Path(r) for r in root] if root else [Path(DEFAULT_ROOT)]
    return discover_skills(roots)


@app.command(name="build")
def build_cmd(skill_dir: str, *, out: str = ".") -> dict[str, object]:
    """Build the .pyz for one skill; does not touch the lock."""
    result = build_pyz(Path(skill_dir), Path(out))
    return {
        "name": result.name,
        "key": result.key,
        "sha256": result.sha256,
        "path": str(result.path),
    }


@app.command(name="lock")
def lock_cmd(skill_dir: str) -> dict[str, object]:
    """Build once, then write the lock and the launcher beside the skill."""
    return lock_skill(Path(skill_dir)).to_dict()


@app.command(name="check")
def check_cmd(
    skill_dir: list[str] | None = None, *, root: list[str] | None = None
) -> dict[str, object]:
    """Verify every skill's lock and launcher without building."""
    dirs = _resolve_skill_dirs(root, skill_dir or [])
    issues = check_skills(dirs)
    if issues:
        detail = "; ".join(f"{issue.skill_dir}: {issue.reason}" for issue in issues)
        raise fromargs.contract_error(RuntimeError(detail), context="wedge check")
    return {"checked": [str(d) for d in dirs], "ok": True}


@app.command(name="publish")
def publish_cmd(*, root: list[str] | None = None, repo: str, target: str) -> dict[str, dict[str, str]]:
    """Publish every discovered skill's .pyz to the rolling ``wedge`` release."""
    roots = [Path(r) for r in root] if root else [Path(DEFAULT_ROOT)]
    dirs = discover_skills(roots)
    results = publish_skills(dirs, repo=repo, target=target)
    failed = {name: r for name, r in results.items() if r["status"] == "failed"}
    if failed:
        detail = "; ".join(f"{name}: {r['reason']}" for name, r in failed.items())
        raise fromargs.contract_error(RuntimeError(detail), context="wedge publish")
    return results


def main() -> None:
    app.main()

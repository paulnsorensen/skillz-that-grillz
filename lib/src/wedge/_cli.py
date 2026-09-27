"""The ``wedge`` CLI: build, lock, check, and publish skill .pyz packages.

Built with ``fromargs.App`` (dogfood): every command returns data and
``run`` prints it as one JSON document on stdout. Every command takes skill
directories as positionals or discovers them under ``--root``, and the
commands that build run the skills in parallel.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import fromargs

from wedge._build import build_many
from wedge._discover import discover_skills
from wedge._fanout import Outcome
from wedge._lock import check as check_skills
from wedge._lock import lock_many
from wedge._publish import publish as publish_skills

DEFAULT_ROOT = "skills"
T = TypeVar("T")

app = fromargs.App("wedge", help="Shiv a fromargs CLI into a skill as a content-addressed .pyz.")


def _dedupe_dirs(dirs: list[Path]) -> list[Path]:
    """One entry per resolved directory, first-seen order and spelling."""
    seen: set[Path] = set()
    deduped: list[Path] = []
    for skill_dir in dirs:
        resolved = skill_dir.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(skill_dir)
    return deduped


def _resolve_skill_dirs(root: list[str] | None, skill_dir: list[str] | None, context: str) -> list[Path]:
    """Positional skill dirs win; otherwise glob ``--root`` (default ``skills``).

    Finding no skill is an error: a wrong root must not pass as a no-op.
    """
    if skill_dir:
        if root:
            raise fromargs.contract_error(
                RuntimeError("skill directories and --root are exclusive"), context=context
            )
        return _dedupe_dirs([Path(path) for path in skill_dir])
    roots = [Path(r) for r in root] if root else [Path(DEFAULT_ROOT)]
    for one_root in roots:
        if not one_root.is_dir():
            raise fromargs.contract_error(
                RuntimeError(f"--root {one_root} is not a directory"), context=context
            )
    dirs = discover_skills(roots)
    if not dirs:
        listed = ", ".join(str(r) for r in roots)
        raise fromargs.contract_error(RuntimeError(f"no */wedge.toml under {listed}"), context=context)
    return _dedupe_dirs(dirs)


def _check_jobs(jobs: int | None, context: str) -> None:
    """Reject ``--jobs`` below 1; a silent fallback would hide the mistake."""
    if jobs is not None and jobs < 1:
        raise fromargs.contract_error(
            RuntimeError(f"--jobs must be at least 1, got {jobs}"), context=context
        )


def _fail(pairs: list[tuple[str, str]], context: str) -> None:
    """Raise one contract error naming every item in ``pairs``, or do nothing."""
    if not pairs:
        return
    detail = "; ".join(f"{name}: {reason}" for name, reason in pairs)
    raise fromargs.contract_error(RuntimeError(detail), context=context)


def _raise_failures(outcomes: list[Outcome[Path, T]], context: str) -> list[T]:
    """The values of every outcome, or one error naming each skill that failed."""
    _fail([(str(o.item), o.error) for o in outcomes if o.error is not None], context)
    return [o.value for o in outcomes if o.value is not None]


@app.command(name="build")
def build_cmd(
    skill_dir: list[str] | None = None,
    *,
    root: list[str] | None = None,
    out: str = ".",
    jobs: int | None = None,
) -> dict[str, dict[str, str]]:
    """Build each skill's .pyz into ``--out``; does not touch the locks."""
    _check_jobs(jobs, "wedge build")
    dirs = _resolve_skill_dirs(root, skill_dir, "wedge build")
    results = _raise_failures(build_many(dirs, Path(out), jobs=jobs), "wedge build")
    return {
        r.name: {"key": r.key, "content_sha256": r.content_sha256, "path": str(r.path)}
        for r in results
    }


@app.command(name="lock")
def lock_cmd(
    skill_dir: list[str] | None = None,
    *,
    root: list[str] | None = None,
    jobs: int | None = None,
) -> dict[str, dict[str, object]]:
    """Build each skill once, then write its lock and launcher beside it."""
    _check_jobs(jobs, "wedge lock")
    dirs = _resolve_skill_dirs(root, skill_dir, "wedge lock")
    results = _raise_failures(lock_many(dirs, jobs=jobs), "wedge lock")
    return {data.name: data.to_dict() for data in results}


@app.command(name="check")
def check_cmd(
    skill_dir: list[str] | None = None, *, root: list[str] | None = None
) -> dict[str, object]:
    """Verify every skill's lock and launcher without building."""
    dirs = _resolve_skill_dirs(root, skill_dir, "wedge check")
    issues = check_skills(dirs)
    _fail([(issue.skill_dir, issue.reason) for issue in issues], "wedge check")
    return {"checked": [str(d) for d in dirs], "ok": True}


@app.command(name="publish")
def publish_cmd(
    skill_dir: list[str] | None = None,
    *,
    root: list[str] | None = None,
    repo: str,
    target: str,
    jobs: int | None = None,
    branch: str | None = None,
) -> dict[str, dict[str, str]]:
    """Publish every skill's .pyz to the rolling ``wedge`` release.

    ``--branch`` refuses to publish a ``--target`` that the branch does not contain.
    """
    _check_jobs(jobs, "wedge publish")
    dirs = _resolve_skill_dirs(root, skill_dir, "wedge publish")
    results = publish_skills(dirs, repo=repo, target=target, jobs=jobs, branch=branch)
    failed = {name: r for name, r in results.items() if r["status"] == "failed"}
    _fail([(name, r["reason"]) for name, r in failed.items()], "wedge publish")
    return results


def main() -> None:
    app.main()

"""Find wedged skills by globbing for ``wedge.toml``."""

from __future__ import annotations

from pathlib import Path


def discover_skills(roots: list[Path]) -> list[Path]:
    """Every skill directory under ``roots`` that has a ``wedge.toml``."""
    skill_dirs: list[Path] = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        skill_dirs.extend(sorted(p.parent for p in root.glob("*/wedge.toml")))
    return skill_dirs

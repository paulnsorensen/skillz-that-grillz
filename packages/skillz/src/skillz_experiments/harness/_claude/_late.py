from __future__ import annotations

import tempfile
from collections.abc import Callable, Collection
from pathlib import Path
from typing import final

from skillz_experiments._candidate import make_workspace
from skillz_experiments.harness._claude._events import loaded_skills, probe_name, probe_skill
from skillz_experiments.harness._claude._settings import Declared, Inventory


@final
class LateBuiltins:
    """Find built-in skills that Claude Code registers after the first `initialize` listing.

    Claude Code loads account state asynchronously, so a new config directory can omit a built-in skill at first.
    An init event then names a skill that the inventory does not know. This class lists the commands again, once
    for each new name, and adds the names that the new listing marks built-in. No model call runs.
    """

    def __init__(self, builtin_listing: Callable[[Path], frozenset[str]]) -> None:
        self._listing = builtin_listing
        self._asked: set[str] = set()

    def adopt(self, events: list[dict[str, object]], expected: Collection[str], inventory: Inventory,
              declared: Declared) -> Inventory:
        """Return `inventory` with each unknown init-event skill that a new listing marks built-in."""
        names = loaded_skills(events) or []
        unknown = {name for name in names if name not in inventory.builtin and name not in inventory.foreign
                   and name not in expected and name not in declared.skills} - self._asked
        if not unknown:
            return inventory
        self._asked |= unknown
        with tempfile.TemporaryDirectory(prefix="skillz-relist-") as directory:
            workspace = make_workspace(Path(directory) / "workspace")
            probe_skill(workspace, probe_name())
            late = self._listing(workspace)
        return Inventory(inventory.builtin | (unknown & late), inventory.foreign)

from __future__ import annotations

import tempfile
import threading
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
    An init event then names a skill that the inventory does not know. Each `adopt` call that finds new names
    lists the commands once and adds the names that the new listing marks built-in. No model call runs.
    A lock serializes the calls, so two threads never list the same names twice or lose an adopted name.
    """

    def __init__(self, builtin_listing: Callable[[Path], frozenset[str]]) -> None:
        self._listing = builtin_listing
        self._asked: set[str] = set()
        self._late: frozenset[str] = frozenset()
        self._lock = threading.Lock()

    def adopt(self, events: list[dict[str, object]], expected: Collection[str], inventory: Inventory,
              declared: Declared) -> Inventory:
        """Return `inventory` with every adopted late name, plus each new unknown init-event skill that a listing marks built-in."""
        names = loaded_skills(events) or []
        with self._lock:
            known = inventory.builtin | self._late
            unknown = {name for name in names if name not in known and name not in inventory.foreign
                       and name not in expected and name not in declared.skills} - self._asked
            if unknown:
                self._asked |= unknown
                with tempfile.TemporaryDirectory(prefix="skillz-relist-") as directory:
                    workspace = make_workspace(Path(directory) / "workspace")
                    probe_skill(workspace, probe_name())
                    listed = self._listing(workspace)
                self._late |= unknown & listed
            return Inventory(inventory.builtin | self._late, inventory.foreign)

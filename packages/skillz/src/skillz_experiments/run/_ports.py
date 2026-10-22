"""The infrastructure boundaries of a run: `git apply` for the replay and the cross-run attempt history store.

The defaults are `GitPatchApplier` in `_replay`, next to `apply_patches`, and `FileHistoryStore` in `_history_store`.
`run(ports=...)` replaces them.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from skillz_experiments.run._history_store import Loaded
from skillz_experiments.run._patch import Patch


class PatchApplier(Protocol):
    def apply(self, directory: Path, patch: Patch) -> str | None:
        """Apply the patches to the clean copy in `directory`. Return the stderr of the first failure, else None."""
        ...


class HistoryStore(Protocol):
    def load(self, skill: str, target_root: str, record: Mapping[str, object], run_id: str) -> Loaded:
        """Load the compatible earlier attempts for the run `record`. A store fault gives a warning, not an error."""
        ...

    def write(self, skill: str, target_root: str, record: Mapping[str, object], run_id: str,
              attempts: Sequence[Mapping[str, object]]) -> str | None:
        """Record the attempts of this run. Return `history-unreadable` when the store fails, else None."""
        ...


@dataclass(frozen=True)
class Ports:
    patches: PatchApplier
    history: HistoryStore

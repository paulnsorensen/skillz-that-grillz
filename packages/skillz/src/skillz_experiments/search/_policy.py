"""Fixed seeded validation subset for the GEPA search."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, final


class _Loader(Protocol):
    def all_ids(self) -> Sequence[int]: ...


class _State(Protocol):
    prog_candidate_val_subscores: list[dict[int, float]]

    def get_program_average_val_subset(self, program_idx: int) -> tuple[float, int]: ...


@final
class FixedSubset:
    def __init__(self, indices: tuple[int, ...]) -> None:
        self._indices = indices

    def get_eval_batch(self, loader: _Loader, state: _State, target_program_idx: int | None = None) -> list[int]:
        del state, target_program_idx
        ids = loader.all_ids()
        return [ids[index] for index in self._indices]

    def get_best_program(self, state: _State) -> int:
        best_idx, best_score, best_coverage = -1, float("-inf"), -1
        for index, scores in enumerate(state.prog_candidate_val_subscores):
            coverage = len(scores)
            mean = sum(scores.values()) / coverage if coverage else float("-inf")
            if mean > best_score or (mean == best_score and coverage > best_coverage):
                best_idx, best_score, best_coverage = index, mean, coverage
        return best_idx

    def get_valset_score(self, program_idx: int, state: _State) -> float:
        return state.get_program_average_val_subset(program_idx)[0]

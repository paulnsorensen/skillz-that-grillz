"""Reserve search room and complete subset-screened finalists in proposal order."""
from __future__ import annotations

import random
from collections.abc import Callable
from typing import cast, final

from skillz_experiments._candidate import Candidate, candidate_files
from skillz_experiments._cases import CodedError, mapping
from skillz_experiments.run._leaderboard import Leaderboard
from skillz_experiments.run._outcomes import failed_outcome
from skillz_experiments.run._plan import VALIDATION_REPEATS
from skillz_experiments.run._record_fields import field, number_field
from skillz_experiments.run._search_phase import SearchPhase
from skillz_experiments.run._state import RunState
from skillz_experiments.search import complete_means, is_search_validation


@final
class Finalists:
    def __init__(self, state: RunState, search: SearchPhase, built: Callable[[Candidate], Candidate]) -> None:
        self.state = state
        self.search = search
        self.built = built
        self.validation = state.cases_for("validation")
        seed = state.record["split_seed"]
        if type(seed) is not int:
            raise CodedError("run-record-tampered", "split_seed must be an integer")
        count = len(self.validation)
        chosen = set(random.Random(seed).sample(range(count), (count + 1) // 2))
        self.indices = tuple(index for index in range(count) if index in chosen)
        self.subset = [self.validation[index].identifier for index in self.indices]
        self.remaining = [case for case in self.validation if case.identifier not in self.subset]

    def search_done(self) -> bool:
        block = self.state.record.get("search")
        return isinstance(block, dict) and cast(dict[str, object], block).get("subset_search_done") is True

    def _proposals(self) -> list[tuple[str, dict[str, str]]]:
        found: list[tuple[str, dict[str, str]]] = []
        seen: set[str] = set()
        for item in self.search.reflections():
            identity, components = item.get("proposed"), item.get("components")
            if isinstance(identity, str) and isinstance(components, dict) and identity not in seen:
                seen.add(identity)
                try:
                    found.append((identity, candidate_files(cast(object, components))))
                except ValueError as error:
                    raise CodedError("run-record-tampered", f"invalid finalist components: {error}") from error
        return found

    def _attempted(self, identity: str) -> set[tuple[str, int]]:
        return {(cast(str, item["case_id"]), cast(int, item.get("repeat", 0))) for item in self.state.outcomes
                if is_search_validation(item) and item["search_candidate"] == identity
                and item.get("status") != "evaluation-failed"}

    def reserved(self) -> int:
        """Hold every admitted proposal's remaining slots until its post-search screen runs."""
        with self.state.lock:
            reserved = 0
            for identity, _ in self._proposals():
                attempted = self._attempted(identity)
                reserved += sum((case.identifier, index) not in attempted
                                for case in self.remaining for index in range(VALIDATION_REPEATS))
            return reserved

    def complete(self) -> None:
        budget = self.state.budget
        budget.reserve_time(number_field(mapping(field(self.state.record, "budget")), "reserve_seconds"))
        try:
            self._complete()
        finally:
            budget.reserve_time(0.0)

    def _complete(self) -> None:
        state = self.state
        with state.lock:
            block = state.record.setdefault("search", {})
            if not isinstance(block, dict):
                raise CodedError("run-record-tampered", "search must be an object")
            block["subset_search_done"] = True
            compared = {item.get("challenger")
                        for item in cast(list[dict[str, object]], cast(dict[str, object], block).get("comparisons", []))}
            state.checkpoint()
        subset = set(self.subset)
        with state.lock:
            rows = [item for item in state.outcomes if item.get("case_id") in subset]
            means = complete_means(rows, subset, VALIDATION_REPEATS)
        leaderboard = Leaderboard(state)
        for identity, components in self._proposals():
            if identity in compared:
                continue
            with state.lock:
                incumbent = leaderboard.best_identity()
                qualifies = identity in means and incumbent in means and means[identity] >= means[incumbent]
            if not qualifies:
                continue
            candidate = self.built(state.seed.changed(components))
            if candidate.identity != identity:
                raise CodedError("run-record-tampered", "a finalist differs from its admitted proposal")
            for case in self.remaining:
                for index in range(VALIDATION_REPEATS):
                    if (case.identifier, index) in self._attempted(identity):
                        continue
                    if len(self.search.search_outcomes()) >= self.search.search_ceiling():
                        return
                    try:
                        _ = state.evaluate_case(candidate, case, "search", repeat=index)
                    except CodedError:
                        raise
                    except (OSError, ValueError, RuntimeError) as error:
                        with state.lock:
                            state.outcomes.append(failed_outcome(case, candidate, error, repeat=index))
                            state.checkpoint()
                        self.search.stop_if_faulted()
            leaderboard.keep_if_best(candidate, self.validation[-1])

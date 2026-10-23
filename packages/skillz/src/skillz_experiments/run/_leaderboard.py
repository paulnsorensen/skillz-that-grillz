"""The validation leaderboard of the search: the incumbent, and the 2-SE rule of the holdout gate that replaces it.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import cast, final

from skillz_experiments._candidate import Candidate, candidate_files
from skillz_experiments._cases import Case
from skillz_experiments.evaluation import task_tokens
from skillz_experiments.gate import Verdict, holdout_deltas, verdict
from skillz_experiments.run._outcomes import mean_tokens
from skillz_experiments.run._record_fields import text_field
from skillz_experiments.run._state import RunState
from skillz_experiments.run._plan import VALIDATION_REPEATS
from skillz_experiments.search import complete_means, scored_runs, unit_score, validation_complete

# A challenger replaces the incumbent only on these verdicts of the holdout gate rule.
REPLACES = ("promote", "promote-cheaper")


@final
class Leaderboard:
    """Keep the incumbent of the search: the seed, until a challenger passes the 2-SE rule against it.

    `keep_if_best` takes the session lock. The read methods take no lock.
    """

    def __init__(self, state: RunState) -> None:
        self._state = state

    def validation_ids(self) -> list[str]:
        return [case.identifier for case in self._state.cases_for("validation")]

    def best_identity(self) -> str:
        """Return the recorded identity of the incumbent: the `best_validated` candidate, else the seed.

        The recorded identity includes the rebuilt `.pyz` of each own target, so it matches the `search_candidate` of its outcomes.
        """
        kept = self._kept()
        return self._state.seed.identity if kept is None else text_field(kept, "identity")

    def _kept(self) -> dict[str, object] | None:
        kept = self._state.record.get("best_validated")
        return cast(dict[str, object], kept) if isinstance(kept, dict) else None

    def keep_if_best(self, candidate: Candidate, case: Case) -> None:
        """Compare a challenger with the incumbent once both complete every validation repeat. The record stays small.

        The comparison is the holdout gate rule: `holdout_deltas` pairs the scored repeats per case, then `verdict`
        applies the 2-SE rule with the repeat noise floor, the floors, the family rule, and the token rule of the run.
        Each comparison joins `search.comparisons`. The challenger becomes the incumbent on `promote` or
        `promote-cheaper`, and `best_validated` keeps its files and that comparison. A pair is compared once.
        """
        state = self._state
        if case.split != "validation" or candidate.identity == state.seed.identity:
            return
        with state.lock:
            validation = self.validation_ids()
            incumbent = self.best_identity()
            challenger = candidate.identity
            if challenger == incumbent or not all(validation_complete(state.outcomes, identity, validation, VALIDATION_REPEATS)
                                                  for identity in (challenger, incumbent)):
                return
            comparisons = self._comparisons()
            if any(item.get("challenger") == challenger and item.get("incumbent") == incumbent for item in comparisons):
                return
            result = self._compare(incumbent, challenger, validation)
            comparison: dict[str, object] = {"challenger": challenger, "incumbent": incumbent, "verdict": result.verdict,
                                             "delta": result.delta, "se": result.se, "reasons": list(result.reasons)}
            comparisons.append(comparison)
            if result.verdict in REPLACES:
                mean = complete_means(state.outcomes, set(validation), VALIDATION_REPEATS).get(challenger)
                state.record["best_validated"] = {"identity": challenger, "mean": mean, "comparison": comparison,
                                                  "files": {name: candidate.files[name] for name in state.seed.editable}}
            state.checkpoint()

    def _comparisons(self) -> list[dict[str, object]]:
        """Return the `search.comparisons` list of the record, created empty. The caller holds the lock."""
        search = cast(dict[str, object], self._state.record.setdefault("search", {}))
        return cast(list[dict[str, object]], search.setdefault("comparisons", []))

    def _compare(self, incumbent: str, challenger: str, validation: list[str]) -> Verdict:
        """Return the gate verdict of `challenger` against `incumbent` on the scored validation repeats.

        The tokens of a case pair the mean task tokens of the scored repeats of each arm.
        """
        state = self._state
        runs = scored_runs(state.outcomes)

        def scores(identity: str) -> dict[str, list[float]]:
            return {name: [unit_score(by_repeat[index]["score"]) for index in sorted(by_repeat)]
                    for name, by_repeat in runs.get(identity, {}).items() if name in validation}

        def tokens(identity: str, name: str) -> float | None:
            by_repeat: Mapping[int, Mapping[str, object]] = runs.get(identity, {}).get(name, {})
            return mean_tokens([task_tokens(item) for item in by_repeat.values()])

        paired = holdout_deltas(scores(incumbent), scores(challenger))
        families = {case.identifier: case.family for case in state.cases_for("validation")}
        return verdict(paired.deltas, thresholds=state.statistics, families=families, se_floor=paired.se_floor,
                       tokens={name: (tokens(incumbent, name), tokens(challenger, name)) for name in paired.deltas})

    def best_validated(self) -> Candidate:
        """Return the incumbent: the `best_validated` candidate, else the seed."""
        kept = self._kept()
        seed = self._state.seed
        return seed if kept is None else seed.changed(candidate_files(kept.get("files")))
"""The validation leaderboard of the search: the best validated candidate and the tie-break by task tokens.
"""
from __future__ import annotations

from typing import cast, final

from skillz_experiments._candidate import Candidate, candidate_files
from skillz_experiments._cases import Case
from skillz_experiments.evaluation import task_tokens
from skillz_experiments.gate import close
from skillz_experiments.run._outcomes import add_validation_scores, complete_means, is_search_validation, mean_tokens
from skillz_experiments.run._record_fields import number_field, text_field
from skillz_experiments.run._state import RunState


@final
class Leaderboard:
    """Rank the search candidates by validation mean. `keep_if_best` takes the session lock. The read methods take no lock."""

    def __init__(self, state: RunState) -> None:
        self._state = state

    def validation_ids(self) -> list[str]:
        return [case.identifier for case in self._state.cases_for("validation")]

    def best_identity(self) -> str:
        """Return the identity of the best validated candidate, or the seed when the seed is best."""
        return self.best_validated(self.validation_ids()).identity

    def _means(self, validation: list[str]) -> dict[str, float]:
        """Return the mean score of each search candidate that has a score on every validation case."""
        return complete_means(add_validation_scores({}, self._state.outcomes), set(validation))

    def _tokens(self, validation: list[str], identities: tuple[str, ...]) -> dict[str, float | None]:
        """Return the mean task tokens of each listed candidate that has an outcome on every validation case.

        A candidate with a missing count on any case maps to None.
        """
        counts: dict[str, dict[str, int | None]] = {}
        for item in (item for item in self._state.outcomes if is_search_validation(item)):
            if item["search_candidate"] in identities:
                counts.setdefault(cast(str, item["search_candidate"]), {})[cast(str, item["case_id"])] = task_tokens(item)
        return {identity: mean_tokens(list(by_case.values())) for identity, by_case in counts.items()
                if set(by_case) == set(validation)}

    def _tied_cheaper(self, validation: list[str], challenger: str, incumbent: str) -> bool:
        """Return whether a token field is set and the challenger has known, lower mean validation task tokens."""
        if not self._state.statistics.token_rule:
            return False
        tokens = self._tokens(validation, (challenger, incumbent))
        new, old = tokens.get(challenger), tokens.get(incumbent)
        return new is not None and old is not None and new < old

    def _beats(self, validation: list[str], mean: float, other: float, challenger: str, incumbent: str) -> bool:
        """Return whether `mean` is above `other`, or ties it and the challenger is cheaper."""
        if close(mean, other):
            return self._tied_cheaper(validation, challenger, incumbent)
        return mean > other

    def keep_if_best(self, candidate: Candidate, case: Case) -> None:
        """Record the files of the best non-seed candidate when it completes the validation set. The record stays small.

        A mean tie goes to lower mean task tokens while a token field is set.
        """
        state = self._state
        if case.split != "validation" or candidate.identity == state.seed.identity:
            return
        with state.lock:
            validation = self.validation_ids()
            mean = self._means(validation).get(candidate.identity)
            kept = state.record.get("best_validated")
            if mean is None:
                return
            if isinstance(kept, dict):
                kept_record = cast(dict[str, object], kept)
                kept_mean = number_field(kept_record, "mean")
                if not self._beats(validation, mean, kept_mean, candidate.identity, text_field(kept_record, "identity")):
                    return
            state.record["best_validated"] = {"identity": candidate.identity, "mean": mean,
                                              "files": {name: candidate.files[name] for name in state.seed.editable}}
            state.checkpoint()

    def best_validated(self, validation: list[str]) -> Candidate:
        """Return the recorded candidate with the best validation mean.

        The seed wins ties, unless a token field is set and the candidate has fewer mean validation task tokens.
        """
        seed = self._state.seed
        means = self._means(validation)
        kept = self._state.record.get("best_validated")
        if not isinstance(kept, dict):
            return seed
        kept = cast(dict[str, object], kept)
        identity = text_field(kept, "identity")
        if identity in means:
            seed_mean = means.get(seed.identity, -1.0)
            if self._beats(validation, means[identity], seed_mean, identity, seed.identity):
                return seed.changed(candidate_files(kept.get("files")))
        return seed

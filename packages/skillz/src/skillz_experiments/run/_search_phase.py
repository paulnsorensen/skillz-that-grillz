"""The search phase state and the callbacks that the GEPA engine drives: evaluate, reuse, admit, and propose.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import cast, final

from skillz_experiments._budget import CallTimeout
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError, digest, mapping
from skillz_experiments.evaluation import task_tokens
from skillz_experiments.run._leaderboard import Leaderboard
from skillz_experiments.run._plan import VALIDATION_REPEATS
from skillz_experiments.run._outcomes import (
    case_ids, fault_reason, feedback_for, failed_outcome, is_unscored, mean_tokens,
    reflection_fault, search_fault, stall_observations,
)
from skillz_experiments.run._record_fields import field, int_field
from skillz_experiments.run._state import RunState
from skillz_experiments.search import FOCUS_REASON, HYPOTHESIS, NOTES, Denylist, Edit, Progress, Signal, assess
from skillz_experiments.search import accept as accept_proposal
from skillz_experiments.search import attempts as build_attempts
from skillz_experiments.search import check as check_leakage
from skillz_experiments.search import focus as focus_component
from skillz_experiments.search import faulted_reflection, focus_advice, is_reflection, search_allowance
from skillz_experiments.search import journal as build_journal
from skillz_experiments.search import notes as accept_notes
from skillz_experiments.search import protected as protected_cases
from skillz_experiments.search import rejected as rejected_memory
from skillz_experiments.search import request as reflection_request

LEAKAGE_REJECTED = "candidate-leakage-rejected"


def recorded_rejections(record: dict[str, object]) -> dict[str, set[str]]:
    """Load the rejected proposal digests per rule from the record. They hold no case text."""
    recorded = mapping(record.get("leakage_rejected", {}))
    found: dict[str, set[str]] = {}
    for rule, keys in recorded.items():
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in cast(list[object], keys)):
            raise ValueError("run record field leakage_rejected must map rules to lists of text")
        found[rule] = set(cast(list[str], keys))
    return found


@final
class SearchPhase:
    """The state of one search and the callbacks of the GEPA engine.

    The callbacks take the session lock only around record reads and writes, and make model calls outside it.
    The read helpers take no lock.
    """

    def __init__(self, state: RunState, *, edit: Edit, search_cases: Mapping[str, Case],
                 built: Callable[[Candidate], Candidate]) -> None:
        self._state = state
        self._edit: Edit = edit
        self._search_cases = search_cases
        self._built = built
        # Rebuilt from the frozen cases on every open. It never reaches `run.json` or an export.
        self._denylist = Denylist.build(search_cases.values(), seed=state.seed)
        self._leak_rules: dict[str, str | None] = {}
        self._ended_proposal: str | None = None
        # Recorded search outcomes that a resumed search reuses, one per earlier call. `recorded_for_reuse` fills it.
        self._reusable: dict[tuple[str, str, str], list[dict[str, object]]] = {}
        # The built identity of each admitted component map, by its digest. `cheaper` reads it.
        self._identities: dict[str, str] = {}
        # The live search evaluations of the running search. Reused outcomes do not count.
        self._live = 0
        # The count of recorded search evaluations when this session started its search. The fault rule counts after it.
        self.fault_base = 0
        # The attempts of compatible earlier runs that `search` receives. They feed the prompt only, never the stall signal.
        self.earlier: tuple[Mapping[str, object], ...] = ()
        self.leak_rejections = recorded_rejections(state.record)

    def search_outcomes(self) -> list[dict[str, object]]:
        """Return the recorded search evaluations on train and validation cases."""
        return [item for item in self._state.outcomes
                if item.get("arm") == "search" and item.get("split") in ("train", "validation")]

    def seed_ref(self) -> tuple[str, str]:
        """Return the digest of the seed component texts and the seed identity. The digest names the parent of a first proposal."""
        seed = self._state.seed
        return digest({name: seed.files[name] for name in seed.editable}), seed.identity

    def attempts(self) -> list[dict[str, object]]:
        return build_attempts(self._state.outcomes, self.seed_ref(), Leaderboard(self._state).validation_ids())

    def reflections(self) -> list[dict[str, object]]:
        """Return the recorded reflection calls, one per proposal."""
        return [item for item in self._state.outcomes if is_reflection(item)]

    def text_dropped(self) -> dict[str, int]:
        """Return the count of each rule that dropped a hypothesis or notes. The recorded outcomes hold it, so a resume keeps it."""
        rules = [cast(str, item[key]) for item in self.reflections() for key in ("hypothesis_dropped", "notes_dropped")
                 if isinstance(item.get(key), str)]
        return dict(sorted(Counter(rules).items()))

    def over_budget(self) -> int:
        """Return the proposals above the edit budget stated in their prompt."""
        return sum(1 for item in self.reflections() if item.get("over_budget") is True)

    def search_ceiling(self) -> int:
        """Return the GEPA call budget: the search allowance plus the seed outcomes that the headroom pass spent.

        The seed outcomes are one per train case and `VALIDATION_REPEATS` per validation case.
        The last iteration can pass it by up to the overshoot margin. The plan admits a run with the same formula.
        """
        estimate = mapping(field(self._state.record, "estimate"))
        validation = VALIDATION_REPEATS * len(self._state.cases_for("validation"))
        spent = int_field(mapping(field(estimate, "headroom")), "outcomes") + validation
        return search_allowance(int_field(estimate, "metric_calls"), validation) + spent

    def _stall(self) -> Signal:
        """Return the stall signal that the recorded outcomes give. The caller holds the lock."""
        validation = {case.identifier for case in self._state.cases_for("validation")}
        return assess(stall_observations(self._state.outcomes, validation), band=self._state.statistics.min_gain)

    def stalled(self) -> bool:
        """Return whether the stall signal is at `stop`. GEPA calls this before each iteration."""
        with self._state.lock:
            return self._stall().level == "stop"

    def share_spent(self, allowance: int) -> bool:
        """Return whether the live search evaluations of this search reach `allowance`. GEPA checks it before each iteration."""
        with self._state.lock:
            return self._live >= allowance

    def stop_if_faulted(self) -> None:
        """Latch and raise `search-faulted` when the recorded search evaluations meet the failure rule."""
        with self._state.lock:
            fault = search_fault(self.search_outcomes()[self.fault_base:])
        if fault is not None:
            self._state.faults.latch(fault)
            raise fault

    def mark_start(self) -> None:
        """Count the faults of this search from the outcomes that exist now."""
        with self._state.lock:
            self.fault_base = len(self.search_outcomes())

    def recorded_for_reuse(self) -> int:
        """Load the scored search outcomes of the record into the reuse queues. Return their count.

        A failed evaluation stays out, so a resume tries it again.
        """
        with self._state.lock:
            self._reusable.clear()
            self._live = 0
            self.fault_base = len(self.search_outcomes())
            for item in self.search_outcomes():
                if "search_candidate" in item and item.get("status") != "evaluation-failed":
                    key = (cast(str, item["search_candidate"]), cast(str, item["split"]), cast(str, item["case_id"]))
                    self._reusable.setdefault(key, []).append(item)
            return sum(len(queue) for queue in self._reusable.values())

    def _leak_rule(self, components: dict[str, str]) -> str | None:
        """Return the leakage rule that the proposal breaks, else None. Each distinct proposal is checked once per session."""
        state = self._state
        key = digest(components)
        with state.lock:
            if key in self._leak_rules:
                return self._leak_rules[key]
        leak = check_leakage(self._denylist, state.seed.files, components)
        rule = None if leak is None else leak.rule
        with state.lock:
            first = key not in self._leak_rules
            rule = self._leak_rules.setdefault(key, rule)
            if first and rule is not None:
                self.leak_rejections.setdefault(rule, set()).add(key)
                state.checkpoint()
            return rule

    def _admit(self, components: dict[str, str]) -> Candidate | dict[str, object]:
        """Return the built candidate of `components`, or the rejection that the leakage check or the build gives."""
        rule = self._leak_rule(components)
        if rule is not None:
            return {"rejected": LEAKAGE_REJECTED, "reason": LEAKAGE_REJECTED, "rule": rule}
        try:
            return self._built(self._state.seed.changed(components))
        except ValueError as error:
            rejected: dict[str, object] = {"rejected": str(error)}
            if isinstance(error, CodedError):
                rejected["reason"] = error.code
            return rejected

    def _reuse(self, candidate: Candidate, case: Case) -> dict[str, object] | None:
        """Return a recorded outcome of `candidate` on `case`, or None.

        Each outcome from before this search serves one evaluation, as GEPA asked for it then. After that, the newest
        scored outcome of the pair in this run serves every repeat: GEPA scores the parent again on each minibatch.
        """
        with self._state.lock:
            queue = self._reusable.get((candidate.identity, case.split, case.identifier))
            if queue:
                return queue.pop(0)
            return next((item for item in reversed(self.search_outcomes())
                         if item.get("search_candidate") == candidate.identity and item.get("split") == case.split
                         and item.get("case_id") == case.identifier and item.get("status") != "evaluation-failed"), None)

    def evaluate_example(self, components: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        state = self._state
        case = self._search_cases.get(cast(str, example))
        if case is None:
            raise ValueError("holdout must not enter optimization")
        candidate = self._admit(components)
        if not isinstance(candidate, Candidate):
            return 0.0, {"task_correct": 0.0, "request": case.request, "case_id": case.identifier} | candidate
        with state.lock:
            self._identities[digest(components)] = candidate.identity
        recorded = self._reuse(candidate, case)
        if recorded is not None:
            Leaderboard(state).keep_if_best(candidate, case)
            return feedback_for(recorded, case)
        with state.lock:
            self._live += 1
        try:
            result = state.evaluate_case(candidate, case, "search")
        except CodedError:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            result = failed_outcome(case, candidate, error)
            with state.lock:
                state.outcomes.append(result)
                state.checkpoint()
            self.stop_if_faulted()
        Leaderboard(state).keep_if_best(candidate, case)
        return feedback_for(result, case)

    def cheaper(self, parent: dict[str, str], child: dict[str, str], examples: Sequence[object]) -> bool:
        """Return whether `child` has known, lower mean task tokens than `parent` on the train cases `examples`.

        GEPA asks after it scores both on the minibatch. Each count comes from the newest scored outcome of the pair.
        A component map that was never admitted, or a missing count, gives False.
        """
        with self._state.lock:
            old, new = (self._train_tokens(self._identities.get(digest(components)), examples) for components in (parent, child))
        return old is not None and new is not None and new < old

    def _train_tokens(self, identity: str | None, examples: Sequence[object]) -> float | None:
        """Return the mean task tokens of `identity` on the train cases `examples`, or None. The caller holds the lock."""
        if identity is None:
            return None
        outcomes = list(reversed(self.search_outcomes()))
        counts: list[int | None] = []
        for example in examples:
            item = next((item for item in outcomes
                         if item.get("search_candidate") == identity and item.get("split") == "train"
                         and item.get("case_id") == example and item.get("status") != "evaluation-failed"), None)
            counts.append(None if item is None else task_tokens(item))
        return mean_tokens(counts)

    def _invoke(self, prompt: str, schema: dict[str, object], asked: str) -> dict[str, object]:
        """Call the reflection model. A failure records its row, marks the proposal as ended, and raises."""
        state = self._state
        try:
            return state.provider.invoke(prompt, schema=schema)
        except CodedError as error:
            state.faults.latch(error)
            raise
        except CallTimeout as error:
            with state.lock:
                state.outcomes.append({"arm": "search", "split": "reflection", "status": "timeout", "reason": "timeout",
                                       "failure": str(error)})
                self._ended_proposal = asked
                state.checkpoint()
            raise ValueError("the reflection call timed out; the proposal ends with no candidate") from error
        except (OSError, ValueError, RuntimeError) as error:
            # A provider failure other than a timeout. GEPA skips the proposal, so the row is the only trace of the paid call.
            with state.lock:
                state.outcomes.append({"arm": "search", "split": "reflection", "status": "failed",
                                       "reason": fault_reason(error), "failure": str(error)})
                self._ended_proposal = asked
                state.checkpoint()
                fault = reflection_fault(self.reflections())
            if fault is not None:
                state.faults.latch(fault)
                raise fault from error
            raise ValueError("the reflection call failed; the proposal ends with no candidate") from error

    def propose(self, candidate: dict[str, str], feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        state = self._state
        state.faults.raise_held()
        asked = hashlib.sha256(repr((sorted(candidate.items()), feedback)).encode()).hexdigest()
        leaderboard = Leaderboard(state)
        with state.lock:
            if self._ended_proposal == asked:
                # GEPA retries a failed proposal once. The retry must not run a second reflection call.
                self._ended_proposal = None
                raise ValueError("the reflection of this proposal ended with no candidate")
            stall = self._stall()
            history = self.attempts()
            # At `shift`, the cohort is this run and the compatible earlier runs. The focus never bypasses validation.
            chosen = focus_component([*self.earlier, *history], state.seed.editable) if stall.level == "shift" else None
            progress = Progress(spent=len(self.search_outcomes()), planned=self.search_ceiling(),
                                proposal=1 + sum(1 for item in self.reflections() if not faulted_reflection(item)), stalled=stall.streak,
                                advice=stall.advice if chosen is None else focus_advice(stall.streak, chosen))
            protect = protected_cases(state.outcomes, leaderboard.best_identity())
            memory = rejected_memory(history, band=state.statistics.min_gain)
            journal = build_journal(state.outcomes)
        feedback = {name: [item for item in items if not is_unscored(item)] for name, items in feedback.items()}
        if not any(feedback.values()):
            with state.lock:
                self._ended_proposal = asked
                state.outcomes.append({"arm": "search", "split": "reflection", "status": "skipped",
                                       "reason": "no-scored-feedback"})
                state.checkpoint()
            raise ValueError("every feedback record is unscored; the proposal ends with no candidate")
        prompt, schema = reflection_request(self._edit, candidate, feedback, components, progress=progress,
                                            cheaper=state.statistics.token_rule, history=history, protect=protect,
                                            memory=memory, journal=journal, earlier=self.earlier)
        result = self._invoke(prompt, schema, asked)
        refusal: ValueError | None = None
        proposal: dict[str, str] = {}
        edits = 0
        hypothesis: str | None = None
        hypothesis_dropped: str | None = None
        notes: str | None = None
        notes_dropped: str | None = None
        try:
            proposal, edits, hypothesis, hypothesis_dropped = accept_proposal(result["answer"], components, candidate)
            notes, notes_dropped = accept_notes(result["answer"])
        except ValueError as error:
            refusal = error
        # The hypothesis and the notes never enter the components, so a leak drops the text and keeps the proposal.
        if hypothesis is not None and (leak := check_leakage(self._denylist, {}, {HYPOTHESIS: hypothesis})) is not None:
            hypothesis, hypothesis_dropped = None, leak.rule
        if notes is not None and (leak := check_leakage(self._denylist, {}, {NOTES: notes})) is not None:
            notes, notes_dropped = None, leak.rule
        with state.lock:
            outcome: dict[str, object] = {"arm": "search", "split": "reflection", "usage": result.get("usage"),
                                          "latency_seconds": result.get("latency_seconds"), "parent": digest(candidate),
                                          "components_digest": None if refusal is not None else digest(proposal),
                                          "minibatch": case_ids(feedback), "hypothesis": hypothesis,
                                          "hypothesis_dropped": hypothesis_dropped, "notes": notes,
                                          "notes_dropped": notes_dropped,
                                          "rejected": None if refusal is None else "reflection-shape",
                                          "changed": [] if refusal is not None else sorted(
                                              name for name in components if proposal.get(name) != candidate.get(name))}
            if chosen is not None:
                # Recorded whenever the shift names a focus, even when the proposal changes another component.
                outcome |= {"focus": chosen, "focus_reason": FOCUS_REASON}
            if refusal is not None:
                outcome["rejected_coded"] = True
            if edits > progress.budget():
                # The budget is advisory. The count shows whether enforcement is worth the wasted reflection calls.
                outcome["over_budget"] = True
            state.outcomes.append(outcome)
            state.checkpoint()
        if refusal is not None:
            raise refusal
        self._record_admission(outcome, proposal)
        return proposal

    def _record_admission(self, outcome: dict[str, object], proposal: dict[str, str]) -> None:
        """Add the admission of `proposal` to its reflection row. A build crash latches `proposal-build-failed`."""
        state = self._state
        try:
            admitted = self._admit(proposal)
        except Exception as error:
            # GEPA catches every proposer exception, retries once, and then skips the proposal. The latch ends the run at the next fault check.
            fault = CodedError("proposal-build-failed", f"the proposal build failed: {error}; fix the cause in that error, then resume")
            with state.lock:
                # The stall signal reads this proposal as unknown, because its candidate never got a score.
                outcome["status"] = "proposal-build-failed"
                outcome["rejected"] = "proposal-build-failed"
                outcome["rejected_coded"] = True
                state.checkpoint()
            state.faults.latch(fault)
            raise fault from error
        with state.lock:
            if isinstance(admitted, Candidate):
                outcome["proposed"] = admitted.identity
            else:
                outcome["rejected"] = str(admitted.get("reason") or admitted["rejected"])
                outcome["rejected_coded"] = admitted.get("reason") is not None
            state.checkpoint()

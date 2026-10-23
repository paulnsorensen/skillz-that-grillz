"""The per-run state machine: the run record, its lock, and the phases that the phase modules drive.
"""
from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast, final

from skillz_experiments._budget import SEARCH_ROLES, Budget, BudgetExhausted, CallTimeout
from skillz_experiments._candidate import Candidate, candidate_files, load_frozen
from skillz_experiments._cases import Case, CodedError, Split, digest, load_cases, mapping
from skillz_experiments._contract import parse
from skillz_experiments.evaluation import CheckMemo
from skillz_experiments._records import open_record, read, write
from skillz_experiments.run._wedge_targets import Rebuilder, reopen
from skillz_experiments.gate import Statistics
from skillz_experiments.harness import ClaudeOptions, Configuration, EnvironmentDiffers, stop_kind
from skillz_experiments.run._fault import FaultLatch
from skillz_experiments.run._headroom import run_headroom
from skillz_experiments.run._holdout import run_gate
from skillz_experiments.run._leaderboard import Leaderboard
from skillz_experiments.run._open import freeze_compatibility, freeze_identities, open_budget
from skillz_experiments.run._outcomes import is_failed, search_counts
from skillz_experiments.run._record_fields import (
    PHASES, POST_GATE_PHASES, field, judge_flag, number_field, phase, record_outcomes, text_field,
)
from skillz_experiments.run._plan import VALIDATION_REPEATS
from skillz_experiments.run._ports import PatchApplier
from skillz_experiments.run._replay_phase import run_replay
from skillz_experiments.run._search_phase import SearchPhase
from skillz_experiments.run._state import Factory, HarnessName, Provider
from skillz_experiments.run._stop import Stop, baseline_rejected, check_timeout, preflight_timeout
from skillz_experiments.search import Edit
from skillz_experiments.search import attempts as build_attempts
from skillz_experiments.search import journal as build_journal
from skillz_experiments.search import search as pareto_search


def _differences(recorded: object, observed: object, name: str = "") -> list[str]:
    """Name each preflight evidence key whose value differs, with both values. Keys sit in dotted paths."""
    if isinstance(recorded, dict) and isinstance(observed, dict):
        before, after = cast(dict[str, object], recorded), cast(dict[str, object], observed)
        return [line for key in sorted(set(before) | set(after))
                for line in _differences(before.get(key), after.get(key), f"{name}.{key}" if name else key)]
    return [] if recorded == observed else [f"{name} (recorded {str(cast(object, recorded))[:80]!r}, observed {str(observed)[:80]!r})"]


# The `search.stop_trigger` of each engine stop reason. An idle search adds no new evaluation, so it stalled.
_ENGINE_TRIGGERS: Mapping[str, str] = {"metric-count": "metric-count", "call-share": "call-share", "stall": "stall",
                                      "idle": "stall"}


def _budget_trigger(budget: Budget, timed_out: bool) -> str:
    """Return the `search.stop_trigger` of a budget stop. The caller has given the reserved gate time back.

    `timed_out` is whether the deadline had passed while the gate time was reserved. When the deadline is still
    past without the reserve, the global deadline fired; else the search deadline did. Otherwise the call cap fired.
    """
    if not timed_out:
        return "call-cap"
    return "deadline" if budget.out_of_time() else "search-deadline"


@final
class Session:
    def __init__(self, out: Path, model: str, adapter: HarnessName, factory: Factory | None,
                 supplied: Configuration | None, *, statistics: Statistics) -> None:
        self.out = out
        self.lock = threading.RLock()
        self.record = open_record(out / "run.json")
        self.statistics = statistics
        if phase(self.record) not in PHASES:
            raise Stop("run-record-tampered", "the recorded phase is unknown; create a new run", {})
        self.contract = parse(field(self.record, "contract"), text_field(self.record, "contract_source"))
        if self.contract.identity != text_field(self.record, "contract_hash"):
            raise ValueError("contract differs from the frozen record; create a new run")
        imported, judged = self._import_cases()
        self.cases = [case for case in imported if case.eligible]
        editable = field(self.record, "editable")
        if not isinstance(editable, list) or not all(isinstance(name, str) for name in cast(list[object], editable)):
            raise ValueError("run record field editable must be a list of text")
        self.seed = Candidate(candidate_files(field(self.record, "seed")), tuple(cast(list[str], editable)), self.contract,
                              frozen=self._seed_frozen())
        self.edit: Edit = cast(Edit, text_field(self.record, "edit"))
        self.outcomes = record_outcomes(self.record)
        options = ClaudeOptions.parse(mapping(self.record.get("claude_options", {})))
        configuration = supplied if supplied is not None else (
            Configuration.split(adapter, model, judge_flag(self.record), options) if factory is None else None)
        self.sizing = options if configuration is None else configuration.sizing_options()
        freeze_identities(self.record, self.seed, model, configuration, judged)
        freeze_compatibility(self.record, self.cases, self.contract, configuration)
        self.budget = open_budget(self.record, self.cases, self.contract, self.sizing)
        self.faults = FaultLatch(self.lock)
        self._search_cases = {case.identifier: case for case in self.cases if case.split != "holdout"}
        # One contract check per candidate identity in this session (ADR-013). It never reaches `run.json`.
        self._checks = CheckMemo()
        # Built before the provider gets `self.checkpoint`, which reads its leakage rejections.
        self._searching = SearchPhase(self, edit=self.edit, search_cases=self._search_cases,
                                      built=lambda candidate: self._built(candidate))
        # Opened before the provider: a `Stop` here must not leave a provider, such as a Claude login, unclosed.
        self.rebuilder = self._open_rebuilder()
        self.provider: Provider = (
            factory(model, self.budget, self.checkpoint) if factory is not None else
            cast(Configuration, configuration).create(model, self.budget, self.checkpoint, out, self.contract.surface,
                                                      self.seed.files))

    def _seed_frozen(self) -> dict[str, bytes]:
        """Load the frozen seed files from the run directory. A missing or changed file is tampering."""
        try:
            return load_frozen(mapping(field(self.record, "seed_frozen")), self.out / "frozen")
        except CodedError as error:
            raise Stop(error.code, str(error), {}) from None

    def _open_rebuilder(self) -> Rebuilder | None:
        """Reopen the site layers of the editable own targets. Return None when no target has editable sources."""
        try:
            return reopen(Path(text_field(self.record, "target_root")), mapping(field(self.record, "own_targets")),
                          self.seed, self.out / "site")
        except CodedError as error:
            raise Stop(error.code, str(error), {"next": "start a new run in a new directory"}) from None

    def _built(self, candidate: Candidate) -> Candidate:
        """Return `candidate` with the host-built `.pyz` of each own target that its sources changed."""
        return candidate if self.rebuilder is None else self.rebuilder.apply(candidate)

    @property
    def phase(self) -> str:
        return text_field(self.record, "phase")

    def _import_cases(self) -> tuple[list[Case], bool]:
        """Load the frozen cases. Return them and whether any case needs a judge."""
        if text_field(self.record, "dataset_hash") != digest(read(self.out / "cases.json")):
            raise ValueError("dataset differs from the frozen record")
        imported = load_cases(self.out / "cases.json", self.contract.grader_types())
        return imported, any(self.contract.judged(case.kind) for case in imported)

    def checkpoint(self) -> None:
        with self.lock:
            spent = self.budget.snapshot()
            self.record["calls"] = spent.calls
            self.record["retries"] = spent.retries
            self.record["calls_by_role"] = dict(sorted(spent.calls_by_role.items()))
            self.record["seconds_by_role"] = {role: round(seconds, 3) for role, seconds in sorted(spent.seconds_by_role.items())}
            self.record["leakage_rejected"] = {rule: sorted(keys)
                                               for rule, keys in sorted(self._searching.leak_rejections.items())}
            write(self.out / "run.json", self.record)

    def cases_for(self, split: Split) -> list[Case]:
        return sorted((case for case in self.cases if case.split == split), key=lambda case: case.identifier)

    def preflight(self) -> None:
        if self.record.get("phase") in POST_GATE_PHASES:
            try:
                _ = self.budget.remaining()
            except BudgetExhausted:
                # Past the deadline the replay makes no model call, so the live preflight has nothing to protect.
                return
        recorded = self.record.get("preflight")
        before = self.budget.calls
        try:
            preflight = self.provider.preflight(cast(dict[str, object], recorded) if isinstance(recorded, dict) else None)
        except CallTimeout as error:
            raise preflight_timeout(str(error)) from error
        finally:
            claimed = self.budget.calls - before
            with self.lock:
                earlier = self.record.get("preflight_calls", 0)
                self.record["preflight_calls"] = (earlier if type(earlier) is int else 0) + claimed
                if claimed:
                    # A preflight that fails after its claims is not checkpointed later, so save the count now.
                    self.checkpoint()
        environment_hash = preflight.get("environment_hash")
        _ = self.record.setdefault("environment_hash", environment_hash)
        if self.record["environment_hash"] != environment_hash:
            before = mapping(cast(dict[str, object], recorded)) if isinstance(recorded, dict) else {}
            changed = _differences(before.get("roles", before), preflight.get("roles", preflight))
            evidence = "; ".join(changed[:6]) or "the environment hash only"
            raise EnvironmentDiffers("runtime environment differs from the frozen record; changed evidence: "
                                     + evidence + "; restore the first-run environment and resume, "
                                     + "or start a new run")
        self.record["preflight"] = preflight
        self.record |= self.provider.run_facts
        self.checkpoint()

    def evaluate_case(self, candidate: Candidate, case: Case, arm: str, *, holdout: bool = False,
                      repeat: int | None = None) -> dict[str, object]:
        """Score one case. The model call runs outside the lock; the record update runs inside it."""
        self.faults.raise_held()
        try:
            result = self.provider.evaluate(candidate, case, holdout=holdout, checks=self._checks)
        except CodedError as error:
            self.faults.latch(error)
            raise
        result.update({"arm": arm, "split": case.split, "case_id": case.identifier})
        if arm == "search":
            result["search_candidate"] = candidate.identity
        if repeat is not None:
            result["repeat"] = repeat
        with self.lock:
            self.outcomes.append(result)
            self.checkpoint()
        return result

    def _evaluate_example(self, components: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        return self._searching.evaluate_example(components, example)

    def _propose(self, candidate: dict[str, str], feedback: Mapping[str, Sequence[Mapping[str, object]]],
                 components: list[str]) -> dict[str, str]:
        return self._searching.propose(candidate, feedback, components)

    def _best_validated(self, validation: list[str]) -> Candidate:
        return Leaderboard(self).best_validated(validation)

    def take_budget_stop(self) -> None:
        """Clear a latched budget stop so the gate can run. Re-raise any other latched fault."""
        self.faults.take_budget_stop()

    def authoritative(self, error: Exception) -> Exception:
        """Return the fault that ends the run: a latched fault other than a budget stop, else `error`."""
        return self.faults.authoritative(error)

    def record_failure(self, error: Exception) -> None:
        """Record the failure text and code, drop a stale code, and forget the preflight pass after a terminal code."""
        self.record["failure"] = str(error)
        code = error.code if isinstance(error, CodedError) else None
        if code is None:
            _ = self.record.pop("failure_code", None)
        else:
            self.record["failure_code"] = code
        if stop_kind(code) == "terminal":
            _ = self.record.pop("preflight", None)
        self.checkpoint()

    def check_name(self) -> None:
        """Stop before any live call when the seed name matches a Claude Code command or a foreign skill."""
        self.provider.check_name(self.seed)

    def check_seed(self) -> None:
        """Stop before any search call when the seed fails the local candidate contract check.

        A final sandbox timeout stops with the resumable `baseline-check-timeout`; a resume checks again.
        """
        checked = self._checks.check(self.provider, self.seed)
        if checked.kind == "timeout":
            raise check_timeout("baseline", checked.reason)
        if not checked.passed:
            raise baseline_rejected(checked.reason)

    def headroom(self) -> None:
        """Score the seed once on every train and validation case. Stop with `cases-too-easy` when a family is saturated."""
        run_headroom(self, self._searching)

    def search(self, earlier: Sequence[Mapping[str, object]] = ()) -> None:
        """Run the one GEPA search over train and validation cases. Holdout cases never enter it.

        The headroom pass runs first. Its train and validation seed outcomes count as spent search evaluations.
        Its validation outcomes stand in for the seed validation pass of GEPA.
        `earlier` holds the attempts of compatible earlier runs, oldest first. Each reflection prompt shows them.
        """
        searching = self._searching
        searching.earlier = tuple(earlier)
        reason = "search-share-spent-best-validated"
        # The search limit that fired. A run with no allowance left spent its metric count before this session.
        trigger = "metric-count"
        timed_out = False
        budget_stopped = False
        # The headroom pass runs under the gate time reserve, like the seed validation pass of GEPA did.
        self.budget.reserve_time(number_field(mapping(field(self.record, "budget")), "reserve_seconds"))
        try:
            self.headroom()
        except BudgetExhausted:
            self.take_budget_stop()
            budget_stopped = True
            reason = "budget-exhausted-best-validated"
            timed_out = self.budget.out_of_time()
        finally:
            self.budget.reserve_time(0.0)
        files = {name: self.seed.files[name] for name in self.seed.editable}
        train = [case.identifier for case in self.cases_for("train")]
        validation = [case.identifier for case in self.cases_for("validation")]
        spent = len(searching.search_outcomes())
        allowance = searching.search_ceiling() - spent
        if budget_stopped:
            pass
        elif allowance >= 1 and searching.stalled():
            # A run that stalled before a resume skips GEPA, which scores the seed again before its first stop check.
            reason, trigger = "converged-stalled", "stall"
        elif allowance >= 1:
            # Search and reflection calls leave the gate its estimated time. Preflight and the gate use all of it.
            self.budget.reserve_time(number_field(mapping(field(self.record, "budget")), "reserve_seconds"))
            # The engine bound counts the runs of distinct candidate and case evaluations, so it adds the reusable outcomes.
            # A repeat in this run is free and does not count. The live evaluations alone end the search at the allowance.
            reusable = searching.recorded_for_reuse()
            try:
                stopped = pareto_search(files, self.edit, cast(list[object], train), cast(list[object], validation),
                                        self._evaluate_example, self._propose, metric_calls=allowance + reusable,
                                        stalled=searching.stalled, spent=lambda: searching.share_spent(allowance),
                                        cheaper=searching.cheaper if self.statistics.token_rule else None,
                                        validation_repeats=VALIDATION_REPEATS)
                self.faults.raise_held()
                reason = "converged-stalled" if stopped == "stall" else "validation-mean"
                trigger = _ENGINE_TRIGGERS[stopped]
            except BudgetExhausted:
                self.take_budget_stop()
                budget_stopped = True
                reason = "budget-exhausted-best-validated"
                # Read before the `finally` gives the reserved time back: the search deadline, not the call cap, ended it.
                timed_out = self.budget.out_of_time()
            finally:
                self.budget.reserve_time(0.0)
        if budget_stopped:
            trigger = _budget_trigger(self.budget, timed_out)
        winner = self._best_validated(validation)
        if reason == "budget-exhausted-best-validated" and winner.identity == self.seed.identity:
            reason = "budget-exhausted-seed-retained"
        scored = searching.search_outcomes()
        ceiling = searching.search_ceiling()
        time_stop = timed_out and 2 * len(scored) < ceiling
        if time_stop:
            reason = "time-exhausted"
        if scored and all(is_failed(item) for item in scored):
            raise CodedError("search-failed", "every search evaluation failed: " + str(scored[-1]["failure"])
                             + "; check the model, then start a new run directory")
        self.record["winner"] = winner.files
        with self.lock:
            rejections = {rule: len(keys) for rule, keys in sorted(searching.leak_rejections.items())}
        recorded = self.record.get("search")
        base = cast(dict[str, object], recorded) if isinstance(recorded, dict) else {}
        self.record["search"] = base | {
            "reason": reason,
            "stop_trigger": trigger,
            "retained_seed": winner.identity == self.seed.identity,
            "leakage_rejections": rejections, **search_counts(scored),
            "proposal_over_budget": searching.over_budget(),
            "attempts": build_attempts(self.outcomes, searching.seed_ref(), Leaderboard(self).validation_ids()),
            "text_dropped": searching.text_dropped(),
            "journal": build_journal(self.outcomes),
        }
        if time_stop:
            with self.lock:
                seconds = {role: round(self.budget.seconds_by_role.get(role, 0.0), 3) for role in SEARCH_ROLES}
            cast(dict[str, object], self.record["search"])["seconds_by_role"] = seconds
        self.record["phase"] = "searched"
        self.checkpoint()

    def gate(self) -> None:
        """Score baseline and winner on the unseen holdout, `repeats` times per case, then record the verdict."""
        run_gate(self, self._built)

    def replay(self, patches: PatchApplier) -> None:
        """Check that the exported patch reproduces the winner, then score the patched copy once on the holdout."""
        run_replay(self, self._built, patches)

    def finish(self) -> None:
        self.record["phase"] = "complete"
        self.checkpoint()

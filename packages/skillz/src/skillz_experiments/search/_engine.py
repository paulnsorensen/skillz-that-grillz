from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import math
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Literal, Protocol, cast, get_args

from skillz_experiments._budget import MAX_CONCURRENT_CALLS

REFLECTION_MINIBATCH = 2
Edit = Literal["prose", "prose+cli"]
Evaluate = Callable[[dict[str, str], object], tuple[float, dict[str, object]]]
Propose = Callable[[dict[str, str], Mapping[str, Sequence[Mapping[str, object]]], list[str]], dict[str, str]]



class _API(Protocol):
    def EngineConfig(self, **kwargs: object) -> object: ...
    def ReflectionConfig(self, **kwargs: object) -> object: ...
    def GEPAConfig(self, **kwargs: object) -> object: ...
    def optimize_anything(self, seed_candidate: dict[str, str], **kwargs: object) -> object: ...


def overshoot(validation: int) -> int:
    """Return the most metric calls that the last GEPA iteration spends beyond `metric_calls`."""
    return 2 * REFLECTION_MINIBATCH + validation


def skip_limit(train: int) -> int:
    """Return the consecutive iterations without a new evaluation that end a search: two epochs of train minibatches.

    Each epoch puts every train case in a minibatch, so a parent with an imperfect case reflects within this limit.
    """
    return 2 * math.ceil(train / REFLECTION_MINIBATCH)


def search(seed: dict[str, str], edit: Edit, train: list[object], validation: list[object],
           evaluate: Evaluate, propose: Propose, *, metric_calls: int,
           workers: int = MAX_CONCURRENT_CALLS, stalled: Callable[[], bool] | None = None,
           spent: Callable[[], bool] | None = None) -> bool:
    """Run one GEPA search over `seed`, which holds only the editable components.

    `metric_calls` bounds the distinct candidate and case evaluations. GEPA scores the parent again on each minibatch,
    and the caller serves that repeat from its record, so a repeat does not count. GEPA skips reflection when the parent
    scores perfectly on the minibatch. `skip_limit` consecutive iterations without a new evaluation end the search.
    GEPA calls `stalled` and `spent` before each iteration and stops when either returns true.
    Return whether `stalled` stopped it. `spent` ends the search without that mark: the caller's live-call share is used up.
    """
    if edit not in get_args(Edit):
        raise ValueError("edit must be prose or prose+cli")
    if not train or not validation:
        raise ValueError("search needs independent train and validation cases")
    if metric_calls < 1:
        raise ValueError("search needs at least one metric call")
    try:
        version = importlib.metadata.version("gepa")
    except importlib.metadata.PackageNotFoundError:
        raise ValueError("GEPA 0.1.4 is unavailable; use the bundled runner or install the experiments extra") from None
    if version != "0.1.4":
        raise ValueError("install the experiments extra with GEPA 0.1.4")
    api = cast(_API, cast(object, importlib.import_module("gepa.optimize_anything")))
    if not seed:
        raise ValueError("search has no editable components")

    lock = threading.Lock()
    evaluated: set[tuple[tuple[tuple[str, str], ...], str]] = set()

    def score(candidate: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        if set(candidate) != set(seed):
            raise ValueError("proposal components differ from the frozen set")
        with lock:
            evaluated.add((tuple(sorted(candidate.items())), repr(example)))
        return evaluate(candidate, example)

    stopped = False
    idle = 0
    seen = 0

    def stop(_state: object) -> bool:
        nonlocal stopped, idle, seen
        with lock:
            # A skipped minibatch, a failed proposal, or a repeated candidate adds no new evaluation.
            idle = idle + 1 if len(evaluated) == seen else 0
            seen = len(evaluated)
            ended = idle >= skip_limit(len(train)) or seen >= metric_calls
        stopped = stopped or (stalled is not None and stalled())
        return stopped or ended or (spent is not None and spent())

    # GEPA turns each proposer exception into a skipped proposal. The caller's session latch re-raises a coded fault.
    # GEPA's own `max_metric_calls` stays unset: it counts each repeat, so free repeats would end the search.
    config = api.GEPAConfig(
        engine=api.EngineConfig(parallel=True,
                                # The clamp enforces the two-call concurrency cap.
                                max_workers=max(1, min(workers, MAX_CONCURRENT_CALLS)),
                                use_cloudpickle=False, cache_evaluation=False, raise_on_exception=True,
                                candidate_selection_strategy="pareto",
                                acceptance_criterion="strict_improvement"),
        reflection=api.ReflectionConfig(custom_candidate_proposer=propose, reflection_lm=None,
                                        module_selector="all", reflection_minibatch_size=REFLECTION_MINIBATCH,
                                        skip_perfect_score=True, perfect_score=1.0),
        stop_callbacks=[stop],
    )
    with open(os.devnull, "w", encoding="utf-8") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        _ = api.optimize_anything(seed, evaluator=score, dataset=train, valset=validation,
                                  objective="Maximize independent task correctness.", config=config)
    return stopped

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Literal, Protocol, cast, get_args

from skillz_experiments._wedge import COMPONENT

Mode = Literal["prompt", "prompt-cli", "cli", "wedge"]
Evaluate = Callable[[dict[str, str], object], tuple[float, dict[str, object]]]
Propose = Callable[[dict[str, str], Mapping[str, Sequence[Mapping[str, object]]], list[str]], dict[str, str]]


class _Result(Protocol):
    @property
    def best_candidate(self) -> dict[str, str]: ...


class _API(Protocol):
    def EngineConfig(self, **kwargs: object) -> object: ...
    def ReflectionConfig(self, **kwargs: object) -> object: ...
    def GEPAConfig(self, **kwargs: object) -> object: ...
    def optimize_anything(self, seed_candidate: dict[str, str], **kwargs: object) -> _Result: ...


def optimize(seed: dict[str, str], mode: Mode, train: list[object], validation: list[object],
             evaluate: Evaluate, propose: Propose, *, code: str | None) -> dict[str, str]:
    if mode not in get_args(Mode):
        raise ValueError("mode must be prompt, prompt-cli, cli, or wedge")
    if not train or not validation:
        raise ValueError("search needs independent train and validation cases")
    try:
        version = importlib.metadata.version("gepa")
    except importlib.metadata.PackageNotFoundError:
        raise ValueError("GEPA 0.1.4 is unavailable; use the bundled runner or install the experiments extra") from None
    if version != "0.1.4":
        raise ValueError("install the experiments extra with GEPA 0.1.4")
    if mode == "cli" and code is None:
        raise ValueError("cli search needs a helper path")
    api = cast(_API, cast(object, importlib.import_module("gepa.optimize_anything")))
    editable = {key: text for key, text in seed.items()
                if mode == "prompt-cli" or (mode == "prompt" and key.endswith(".md"))
                or (mode == "cli" and key == code) or (mode == "wedge" and key in ("SKILL.md", COMPONENT))}
    if not editable:
        raise ValueError("search mode has no editable components")

    def score(candidate: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        if set(candidate) != set(editable):
            raise ValueError("proposal components differ from the frozen set")
        return evaluate(seed | candidate, example)

    config = api.GEPAConfig(
        engine=api.EngineConfig(max_metric_calls=5, max_candidate_proposals=1, parallel=False,
                                use_cloudpickle=False, cache_evaluation=False, raise_on_exception=True,
                                acceptance_criterion="improvement_or_equal"),
        reflection=api.ReflectionConfig(custom_candidate_proposer=propose, reflection_lm=None,
                                        module_selector="all", reflection_minibatch_size=1),
    )
    with open(os.devnull, "w", encoding="utf-8") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        result = api.optimize_anything(editable, evaluator=score, dataset=train, valset=validation,
                                       objective="Maximize independent task correctness.", config=config)
    return seed | result.best_candidate

from __future__ import annotations

import difflib
import fcntl
import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast, final

from skillz_experiments._audit import identity as judge_identity
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, digest, load_cases, mapping, text_map
from skillz_experiments._codex import Codex, VERSION
from skillz_experiments._records import read, write
from skillz_experiments._runtime import Budget, BudgetExhausted
from skillz_experiments._search import optimize


class _Provider(Protocol):
    def preflight(self) -> dict[str, object]: ...
    def close(self) -> None: ...
    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]: ...
    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]: ...


Factory = Callable[[str, Budget, Callable[[], None]], _Provider]


def _engine_hash() -> str:
    return digest({path.name: path.read_text() for path in Path(__file__).parent.glob("*.py")})


@final
class _Session:
    def __init__(self, out: Path, model: str, maximum: int, seconds: float, factory: Factory) -> None:
        self.out = out
        self.record = read(out / "run.json")
        cases_document = read(out / "cases.json")
        if self.record["dataset_hash"] != digest(cases_document):
            raise ValueError("frozen dataset changed")
        imported = load_cases(out / "cases.json")
        audits = [case for case in imported if case.kind == "audit"]
        if audits and (any(not case.eligible for case in imported)
                       or not all(any(case.split == split for case in imported) for split in ("train", "validation"))
                       or sum(case.split == "holdout" for case in imported) != 2):
            raise ValueError("audit comparison requires reviewed, provider-approved cases and complete splits")
        self.cases = [case for case in imported if case.eligible]
        if audits:
            _ = self.record.setdefault("judge", judge_identity(model))
            if self.record["judge"] != judge_identity(model):
                raise ValueError("frozen judge changed; create a new run")
        self.seed = Candidate(text_map(self.record["seed"]), tuple(cast(list[str], self.record["editable"])))
        if self.seed.identity != self.record["seed_hash"]:
            raise ValueError("frozen seed changed")
        _ = self.record.setdefault("engine_hash", _engine_hash())
        if self.record["engine_hash"] != _engine_hash():
            raise ValueError("frozen evaluator changed; create a new run")
        _ = self.record.setdefault("started", time.time())
        _ = self.record.setdefault("started_monotonic", time.monotonic())
        _ = self.record.setdefault("max_seconds", seconds)
        _ = self.record.setdefault("max_invocations", maximum)
        _ = self.record.setdefault("model", model)
        if (self.record["max_seconds"], self.record["max_invocations"], self.record["model"]) != (seconds, maximum, model):
            raise ValueError("run configuration changed; create a new run")
        elapsed = time.monotonic() - cast(float, self.record["started_monotonic"])
        if elapsed < 0:
            raise ValueError("run cannot resume after a monotonic clock reset")
        remaining = seconds - elapsed
        reserve = 3 * sum(2 if case.kind == "audit" else 1 for case in self.cases_for("holdout"))
        self.record["holdout_reserve"] = reserve
        self.budget = Budget(maximum, remaining, reserve=reserve, calls=cast(int, self.record["calls"]))
        self.provider = factory(model, self.budget, self.checkpoint)
        self.arms = mapping(self.record["arms"])
        self.outcomes = cast(list[dict[str, object]], self.record["outcomes"])

    def checkpoint(self) -> None:
        self.record["calls"] = self.budget.calls
        write(self.out / "run.json", self.record)

    def cases_for(self, split: str) -> list[Case]:
        return [case for case in self.cases if case.split == split]

    def evaluate_case(self, candidate: Candidate, case: Case, arm: str, *, holdout: bool = False) -> dict[str, object]:
        result = self.provider.evaluate(candidate, case, holdout=holdout)
        result.update({"arm": arm, "split": case.split})
        self.outcomes.append(result)
        self.checkpoint()
        return result

    def baseline(self) -> None:
        if self.record["phase"] != "prepared":
            raise ValueError("baseline requires a new prepared run")
        cases = self.cases_for("train") + self.cases_for("validation")
        if not cases:
            raise ValueError("baseline requires eligible train and validation cases")
        self.arms["original"] = self.seed.files
        for case in cases:
            result = self.evaluate_case(self.seed, case, "original")
            if result.get("status") == "candidate-contract-rejected":
                raise ValueError("original candidate fails the frozen native/helper contract")
        self.record["phase"] = "baseline"
        self.checkpoint()

    def search(self, mode: str) -> None:
        if self.record["phase"] not in {"baseline", "search"} or mode in self.arms:
            raise ValueError("search requires baseline and an unsearched arm")
        candidates: dict[str, Candidate] = {self.seed.identity: self.seed}
        validation: dict[str, list[dict[str, object]]] = {}

        examples = {case.identifier: case for case in self.cases if case.split != "holdout"}

        def evaluate(components: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
            if not isinstance(example, str) or example not in examples:
                raise ValueError("holdout must not enter optimization")
            case = examples[example]
            candidate = self.seed.changed(components)
            candidates[candidate.identity] = candidate
            result = self.evaluate_case(candidate, case, mode)
            if case.split == "validation":
                validation.setdefault(candidate.identity, []).append(result)
            feedback: dict[str, object] = {"task_correct": result["score"], "request": case.request}
            if mode == "cli":
                usage = mapping(result.get("usage") or {})
                feedback["usage"] = {key: value if type(value := usage.get(key)) is int and value >= 0 else None
                                     for key in ("input_tokens", "output_tokens")}
            return cast(float, result["score"]), feedback

        def propose(candidate: dict[str, str], feedback: Mapping[str, Sequence[Mapping[str, object]]],
                    components: list[str]) -> dict[str, str]:
            schema: dict[str, object] = {"type": "object",
                "properties": {key: {"type": "string"} for key in components},
                "required": components, "additionalProperties": False}
            instruction = ("Improve only scripts/inspect_skill.py. Preserve all skill text and the helper CLI contract. "
                           + "Preserve correctness first; reduce measured input-plus-output tokens for correctness ties. "
                           + "Token feedback combines task and judge usage; null means unknown. "
                           if mode == "cli" else
                           "Improve only the supplied skill text components. Preserve the helper CLI contract. ")
            prompt = (instruction
                      + "Return complete component contents. Do not alter independent checks or permissions.\n"
                      + json.dumps({"candidate": candidate, "feedback": feedback}, default=str))
            result = self.provider.invoke(prompt, schema=schema)
            self.outcomes.append({"arm": mode, "split": "reflection", "usage": result.get("usage"),
                                  "latency_seconds": result.get("latency_seconds")})
            self.checkpoint()
            answer = mapping(result["answer"])
            if set(answer) != set(components) or not all(isinstance(value, str) for value in answer.values()):
                raise ValueError("reflection changed frozen component keys")
            return cast(dict[str, str], answer)

        editable = {key: self.seed.files[key] for key in self.seed.editable}
        try:
            _ = optimize(editable, mode, [case.identifier for case in self.cases_for("train")],
                         [case.identifier for case in self.cases_for("validation")], evaluate, propose)
            winner = _select(candidates, validation, len(self.cases_for("validation")), self.seed)
            reason = "validation-selection"
        except BudgetExhausted:
            winner, reason = self.seed, "budget-exhausted-seed-retained"
        self.arms[mode] = winner.files
        self.record[mode + "_selection"] = {"reason": reason, "retained_seed": winner.identity == self.seed.identity}
        self.record["phase"] = "search"
        self.checkpoint()

    def holdout(self) -> None:
        if set(self.arms) not in ({"original", "prompt", "prompt-cli"}, {"original", "prompt", "cli"}) or self.record.get("holdout_consumed"):
            raise ValueError("holdout requires three locked arms and cannot be resumed")
        cases = self.cases_for("holdout")
        if len(cases) != 2:
            raise ValueError("bounded comparison requires exactly two holdout cases")
        self.record["locked_arms"] = {name: digest(files) for name, files in self.arms.items()}
        self.record["holdout_consumed"] = True
        self.checkpoint()
        for name, files in self.arms.items():
            candidate = Candidate(text_map(files), self.seed.editable)
            for case in cases:
                _ = self.evaluate_case(candidate, case, name, holdout=True)
        self.record["phase"] = "complete"
        self.record["improvement"] = "inconclusive-bounded-smoke-test"
        self.checkpoint()


def _select(candidates: dict[str, Candidate], validation: dict[str, list[dict[str, object]]],
            count: int, seed: Candidate) -> Candidate:
    ranked: list[tuple[float, float, str]] = []
    for identity, results in validation.items():
        if len(results) != count:
            continue
        score = sum(cast(float, result["score"]) for result in results) / count
        tokens = [mapping(result["usage"]).get("input_tokens") for result in results]
        output = [mapping(result["usage"]).get("output_tokens") for result in results]
        measured = tokens + output
        total = sum(cast(int, value) for value in measured) if all(isinstance(value, int) for value in measured) else float("inf")
        ranked.append((-score, total, identity))
    return candidates[min(ranked)[2]] if ranked else seed


def execute(out: Path, stage: str, model: str, *, live: bool = False, maximum: int = 20,
            seconds: float = 1200, factory: Factory = Codex, mode: str = "prompt") -> dict[str, object]:
    if not live:
        raise ValueError("live model calls require --live")
    with (out / "run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if read(out / "run.json").get("holdout_consumed"):
            raise ValueError("holdout is consumed; completed evidence is immutable")
        session = _Session(out, model, maximum, seconds, factory)
        try:
            preflight = session.provider.preflight()
            environment_hash = preflight.get("environment_hash")
            _ = session.record.setdefault("environment_hash", environment_hash)
            if session.record["environment_hash"] != environment_hash:
                raise ValueError("frozen runtime environment changed")
            session.record["preflight"] = preflight
            session.record["codex_version"] = VERSION
            session.checkpoint()
            if stage in {"baseline", "self-test"}:
                session.baseline()
            if stage == "search":
                session.search(mode)
            if stage == "self-test":
                session.search("prompt")
                session.search("prompt-cli")
            if stage in {"evaluate", "self-test"}:
                session.holdout()
            return summary(session.record)
        except (OSError, ValueError, RuntimeError):
            session.record["phase"] = "infrastructure-failure"
            session.checkpoint()
            raise
        finally:
            session.provider.close()


def summary(record: dict[str, object]) -> dict[str, object]:
    return {key: record.get(key) for key in
            ("schema_version", "phase", "calls", "model", "codex_version", "improvement", "locked_arms")}


def _export_outcome(item: dict[str, object]) -> dict[str, object]:
    allowed = {"arm", "split", "score", "status", "loaded", "helper_executed", "candidate_hash", "case_hash",
               "latency_seconds", "judge_latency_seconds", "evidence_valid", "matched", "false_positives",
               "false_negatives", "precision", "recall", "detection_f1", "severity_accuracy", "actionability_rate"}
    result = {key: value for key, value in item.items() if key in allowed}
    for key in ("usage", "task_usage", "judge_usage"):
        if key in item:
            value = item[key]
            result[key] = None if value is None else {
                name: mapping(value).get(name) for name in ("input_tokens", "cached_input_tokens", "output_tokens")}
    return result


def export(out: Path, destination: Path, arm: str) -> dict[str, object]:
    record = read(out / "run.json")
    if record.get("phase") != "complete":
        raise ValueError("export requires a completed locked evaluation")
    if record.get("engine_hash") != _engine_hash():
        raise ValueError("frozen evaluator changed; create a new run")
    if "judge" in record and record["judge"] != judge_identity(cast(str, record["model"])):
        raise ValueError("frozen judge changed; create a new run")
    arms = mapping(record["arms"])
    if arm not in {"prompt", "prompt-cli", "cli"} or arm not in arms:
        raise ValueError("export arm must be an evaluated prompt, prompt-cli, or cli arm")
    seed, candidate = text_map(record["seed"]), text_map(arms[arm])
    lines = (line for name in seed if seed[name] != candidate[name]
             for line in difflib.unified_diff(seed[name].splitlines(keepends=True),
                   candidate[name].splitlines(keepends=True), fromfile="a/" + name, tofile="b/" + name))
    patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
    destination.mkdir(mode=0o700)
    patch_path = destination / "candidate.patch"
    descriptor = patch_path.open("x", encoding="utf-8")
    patch_path.chmod(0o600)
    with descriptor:
        _ = descriptor.write(patch)
    report = summary(record) | {"sharing": "private-local-only", "arm": arm,
                                "outcomes": [_export_outcome(item) for item in cast(list[dict[str, object]], record["outcomes"])], "cost_usd": None}
    write(destination / "report.json", report)
    return {"export": str(destination), "sharing": "private-local-only", "installed": False}

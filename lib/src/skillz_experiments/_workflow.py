from __future__ import annotations

import difflib
import fcntl
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Protocol, cast, final, get_args

from skillz_experiments._audit import identity as judge_identity
from skillz_experiments._candidate import Candidate, candidate_files
from skillz_experiments._cases import Case, CodedError, Split, digest, load_cases, mapping
from skillz_experiments._codex import Codex, VERSION
from skillz_experiments._contract import Contract, parse, resolve
from skillz_experiments._harness import Configuration, Harness
from skillz_experiments._records import read, write
from skillz_experiments._runtime import Budget, BudgetExhausted
from skillz_experiments._search import Mode, optimize
from skillz_experiments._wedge import COMPONENT, admit, new_script

BRIEF_LIMIT = 65536


class _Provider(Protocol):
    def preflight(self) -> dict[str, object]: ...
    def close(self) -> None: ...
    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]: ...
    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]: ...


Factory = Callable[[str, Budget, Callable[[], None]], _Provider]


def _engine_hash() -> str:
    return digest({path.name: path.read_text() for path in Path(__file__).parent.glob("*.py")})


def _field(record: dict[str, object], key: str) -> object:
    if key not in record:
        raise ValueError(f"run record lacks {key}")
    return record[key]


def _text_field(record: dict[str, object], key: str) -> str:
    value = _field(record, key)
    if not isinstance(value, str):
        raise ValueError(f"run record field {key} must be text")
    return value


def _number_field(record: dict[str, object], key: str) -> float:
    value = _field(record, key)
    if type(value) not in (int, float):
        raise ValueError(f"run record field {key} must be a number")
    return cast(float, value)


def _outcomes(record: dict[str, object]) -> list[dict[str, object]]:
    value = _field(record, "outcomes")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in cast(list[object], value)):
        raise ValueError("run record field outcomes must be a list of objects")
    return cast(list[dict[str, object]], value)


@dataclass(frozen=True)
class _Resume:
    dataset_hash: str
    seed_hash: str
    seed: Candidate
    contract: Contract | None
    calls: int
    arms: dict[str, object]
    outcomes: list[dict[str, object]]


def _resume_fields(record: dict[str, object]) -> _Resume:
    """Validate the run-record fields that a resume reads, once, at the read boundary."""
    editable = _field(record, "editable")
    if not isinstance(editable, list) or not all(isinstance(name, str) for name in cast(list[object], editable)):
        raise ValueError("run record field editable must be a list of text")
    calls = _field(record, "calls")
    if type(calls) is not int or calls < 0:
        raise ValueError("run record field calls must be a nonnegative integer")
    _ = _text_field(record, "phase")
    contract: Contract | None = None
    if "contract" in record:
        contract = parse(record["contract"], _text_field(record, "contract_source"))
        if contract.identity != _text_field(record, "contract_hash"):
            raise ValueError("contract differs from the frozen record; create a new run")
    return _Resume(_text_field(record, "dataset_hash"), _text_field(record, "seed_hash"),
                   Candidate(candidate_files(_field(record, "seed")), tuple(cast(list[str], editable)), contract),
                   contract, calls, mapping(_field(record, "arms")), _outcomes(record))


@final
class _Session:
    def __init__(self, out: Path, model: str, maximum: int, seconds: float, factory: Factory,
                 configuration: Configuration | None = None) -> None:
        self.out = out
        self.record = read(out / "run.json")
        resume = _resume_fields(self.record)
        self.contract = resolve(resume.contract)
        imported = self._import_cases(resume.dataset_hash)
        self.cases = [case for case in imported if case.eligible]
        self.seed = resume.seed
        self._freeze_identities(model, configuration, any(self.contract.grader(case.kind).type == "audit" for case in imported),
                                 resume.seed_hash)
        self.budget = self._open_budget(model, maximum, seconds, resume.calls)
        self.provider = factory(model, self.budget, self.checkpoint)
        self.arms = resume.arms
        self.outcomes = resume.outcomes
        self._candidates: dict[str, Candidate] = {}
        self._validation: dict[str, list[dict[str, object]]] = {}

    def _import_cases(self, dataset_hash: str) -> list[Case]:
        if dataset_hash != digest(read(self.out / "cases.json")):
            raise ValueError("dataset differs from the frozen record")
        imported = load_cases(self.out / "cases.json", self.contract.grader_types())
        rules = self.contract
        if any(rules.grader(case.kind).type == "audit" for case in imported) and (
                any(not case.eligible for case in imported)
                or not all(any(case.split == split for case in imported) for split in ("train", "validation"))
                or sum(case.split == "holdout" for case in imported) != 2):
            raise ValueError("audit comparison requires reviewed, provider-approved cases and complete splits")
        return imported

    def _freeze_identities(self, model: str, configuration: Configuration | None, audit: bool, seed_hash: str) -> None:
        if configuration is not None:
            target = self.record.get("target_root")
            if not isinstance(target, str):
                raise ValueError("run lacks candidate boundary; create a new run")
            configuration.check_boundary(Path(target))
            identity = configuration.identity()
            _ = self.record.setdefault("harness", identity)
            if self.record["harness"] != identity:
                raise ValueError("harness configuration differs from the frozen record; create a new run")
        if audit:
            judge_model = configuration.roles["judge"].model if configuration is not None else model
            _ = self.record.setdefault("judge_model", judge_model)
            _ = self.record.setdefault("judge", judge_identity(judge_model))
            if self.record["judge"] != judge_identity(judge_model):
                raise ValueError("judge differs from the frozen record; create a new run")
        if self.seed.identity != seed_hash:
            raise ValueError("seed differs from the frozen record")
        _ = self.record.setdefault("engine_hash", _engine_hash())
        if self.record["engine_hash"] != _engine_hash():
            raise ValueError("evaluator differs from the frozen record; create a new run")

    def _open_budget(self, model: str, maximum: int, seconds: float, calls: int) -> Budget:
        _ = self.record.setdefault("started", time.time())
        _ = self.record.setdefault("started_monotonic", time.monotonic())
        _ = self.record.setdefault("max_seconds", seconds)
        _ = self.record.setdefault("max_invocations", maximum)
        _ = self.record.setdefault("model", model)
        if (self.record["max_seconds"], self.record["max_invocations"], self.record["model"]) != (seconds, maximum, model):
            raise ValueError("run configuration differs from the frozen record; create a new run")
        elapsed = time.monotonic() - _number_field(self.record, "started_monotonic")
        if elapsed < 0:
            raise ValueError("run cannot resume after a monotonic clock reset")
        if seconds - elapsed <= 0:
            raise BudgetExhausted("global deadline exhausted")
        reserve = 3 * sum(self.contract.calls(case.kind) for case in self.cases_for("holdout"))
        self.record["holdout_reserve"] = reserve
        return Budget(maximum, seconds - elapsed, reserve=reserve, calls=calls)

    def checkpoint(self) -> None:
        self.record["calls"] = self.budget.calls
        write(self.out / "run.json", self.record)

    def cases_for(self, split: Split) -> list[Case]:
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

    def _wedge_candidate(self, components: dict[str, str]) -> Candidate:
        skill = components["SKILL.md"]
        if components[COMPONENT] == "{}" and skill == self.seed.files["SKILL.md"]:
            return self.seed
        added = admit(self.seed.files, components)
        files = self.seed.files | {"SKILL.md": skill} | added
        return Candidate(files, self.seed.editable, self.seed.contract, new_script(self.seed.files, files))

    def _evaluate_example(self, mode: Mode, components: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        case = next((item for item in self.cases if item.identifier == example and item.split != "holdout"), None)
        if case is None:
            raise ValueError("holdout must not enter optimization")
        if mode == "wedge":
            try:
                candidate = self._wedge_candidate(components)
            except ValueError as error:
                return 0.0, {"task_correct": 0.0, "request": case.request, "rejected": str(error)}
        else:
            candidate = self.seed.changed(components)
        self._candidates[candidate.identity] = candidate
        result = self.evaluate_case(candidate, case, mode)
        if case.split == "validation":
            self._validation.setdefault(candidate.identity, []).append(result)
        feedback: dict[str, object] = {"task_correct": result["score"], "request": case.request}
        if "scores" in result:
            feedback["scores"] = result["scores"]
        if mode in ("cli", "wedge"):
            usage = mapping(result.get("usage") or {})
            feedback["usage"] = {key: value if type(value := usage.get(key)) is int and value >= 0 else None
                                 for key in ("input_tokens", "output_tokens")}
        return cast(float, result["score"]), feedback

    def _propose(self, mode: Mode, brief: str | None, candidate: dict[str, str],
                 feedback: Mapping[str, Sequence[Mapping[str, object]]], components: list[str]) -> dict[str, str]:
        prompt, schema = _reflection_request(mode, candidate, feedback, components, brief)
        result = self.provider.invoke(prompt, schema=schema)
        self.outcomes.append({"arm": mode, "split": "reflection", "usage": result.get("usage"),
                              "latency_seconds": result.get("latency_seconds")})
        self.checkpoint()
        answer = mapping(result["answer"])
        if set(answer) != set(components) or not all(isinstance(value, str) for value in answer.values()):
            raise ValueError("reflection keys differ from the frozen components")
        return cast(dict[str, str], answer)

    def search(self, mode: Mode, brief: str | None = None) -> None:
        if self.record["phase"] not in {"baseline", "search"} or mode in self.arms:
            raise ValueError("search requires baseline and an unsearched arm")
        self._candidates = {self.seed.identity: self.seed}
        self._validation = {}
        editable = ({"SKILL.md": self.seed.files["SKILL.md"], COMPONENT: "{}"} if mode == "wedge"
                    else {key: self.seed.files[key] for key in self.seed.editable})
        try:
            helper = self.contract.helper
            _ = optimize(editable, mode, [case.identifier for case in self.cases_for("train")],
                         [case.identifier for case in self.cases_for("validation")],
                         partial(self._evaluate_example, mode), partial(self._propose, mode, brief),
                         **({"code": helper.path} if helper is not None else {}))
            winner = _select(self._candidates, self._validation, len(self.cases_for("validation")), self.seed)
            reason = "validation-selection"
        except BudgetExhausted:
            winner, reason = self.seed, "budget-exhausted-seed-retained"
        self.arms[mode] = winner.files
        self.record[mode + "_selection"] = {"reason": reason, "retained_seed": winner.identity == self.seed.identity}
        self.record["phase"] = "search"
        self.checkpoint()

    def holdout(self) -> None:
        locked = ({"original", "prompt", "prompt-cli"}, {"original", "prompt", "cli"}, {"original", "prompt", "wedge"})
        if set(self.arms) not in locked or self.record.get("holdout_consumed"):
            raise ValueError("holdout requires three locked arms and an unused holdout")
        cases = self.cases_for("holdout")
        if len(cases) != 2:
            raise ValueError("bounded comparison requires exactly two holdout cases")
        _ = self.budget.remaining()
        self.record["locked_arms"] = {name: digest(files) for name, files in self.arms.items()}
        self.record["holdout_consumed"] = True
        self.checkpoint()
        for name, files in self.arms.items():
            files = candidate_files(files)
            candidate = Candidate(files, self.seed.editable, self.seed.contract,
                                  new_script(self.seed.files, files) or self.seed.script)
            for case in cases:
                _ = self.evaluate_case(candidate, case, name, holdout=True)
        self.record["phase"] = "complete"
        self.record["improvement"] = "inconclusive-bounded-smoke-test"
        measured = [mapping(result.get("usage") or {}).get(key) for result in self.outcomes
                    if result.get("split") == "holdout" for key in ("input_tokens", "output_tokens")]
        self.record["token_comparison"] = ("measured-bounded-smoke-test" if all(type(value) is int for value in measured)
                                            else "inconclusive-unknown-usage")
        self.checkpoint()


_STE_RULE = ("Write every proposed Markdown component in ASD-STE100 Simplified Technical English: "
             "active voice, present tense, one instruction per sentence, and at most 20 words per sentence. ")


def _reflection_request(mode: Mode, candidate: dict[str, str],
                        feedback: Mapping[str, Sequence[Mapping[str, object]]],
                        components: list[str], brief: str | None = None) -> tuple[str, dict[str, object]]:
    schema: dict[str, object] = {"type": "object",
        "properties": {key: {"type": "string"} for key in components},
        "required": components, "additionalProperties": False}
    if mode == "cli":
        instruction = (f"Improve only {components[0]}. Preserve all skill text and the helper CLI contract. "
                       + "Preserve correctness first; reduce measured input-plus-output tokens for correctness ties. "
                       + "Token feedback combines task and judge usage; null means unknown. ")
    elif mode == "wedge":
        instruction = (f"Improve SKILL.md and add one stdlib-only Python script. Set {COMPONENT} to a JSON object with "
                       + "exactly one key, scripts/<name>.py, whose value is the script source. "
                       + "Reference that path in SKILL.md. Add no other file. Preserve the helper CLI contract. "
                       + "Preserve correctness first; reduce measured input-plus-output tokens for correctness ties. "
                       + "Token feedback combines task and judge usage; null means unknown. ")
    else:
        instruction = "Improve only the supplied skill text components. Preserve the helper CLI contract. "
    head = (instruction + _STE_RULE
            + "Return complete component contents. Do not alter independent checks or permissions.")
    block = f" Wedge brief:\n{brief.rstrip()}\n" if brief else "\n"
    return head + block + json.dumps({"candidate": candidate, "feedback": feedback}, default=str), schema


def _select(candidates: dict[str, Candidate], validation: dict[str, list[dict[str, object]]],
            count: int, seed: Candidate) -> Candidate:
    ranked: list[tuple[float, float, bool, str]] = []
    for identity, results in validation.items():
        if len(results) != count:
            continue
        score = sum(cast(float, result["score"]) for result in results) / count
        tokens = [mapping(result["usage"]).get("input_tokens") for result in results]
        output = [mapping(result["usage"]).get("output_tokens") for result in results]
        measured = tokens + output
        total = sum(cast(int, value) for value in measured) if all(isinstance(value, int) for value in measured) else float("inf")
        ranked.append((-score, total, identity != seed.identity, identity))
    return candidates[min(ranked)[3]] if ranked else seed


def _read_brief(path: Path) -> str:
    with path.open("rb") as stream:
        raw = stream.read(BRIEF_LIMIT + 1)
    if len(raw) > BRIEF_LIMIT:
        raise ValueError(f"brief exceeds {BRIEF_LIMIT} bytes")
    return raw.decode("utf-8")


def execute(out: Path, stage: str, model: str, *, live: bool = False, maximum: int = 20,
            seconds: float = 1200, factory: Factory = Codex, mode: Mode = "prompt",
            harness_config: Path | None = None, brief: Path | None = None) -> dict[str, object]:
    if stage == "search" and mode == "wedge" and brief is None:
        raise ValueError("wedge search requires --brief PATH")
    if brief is not None and mode != "wedge":
        raise ValueError("--brief applies to wedge mode only")
    brief_text = _read_brief(brief) if brief is not None else None
    if not live:
        raise ValueError("live model calls require --live")
    if stage == "search" and mode in ("cli", "prompt-cli") and \
            resolve(_resume_fields(read(out / "run.json")).contract).helper is None:
        raise CodedError("helper-missing", f"{mode} search needs a contract helper; declare `helper` in the contract")
    with (out / "run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if read(out / "run.json").get("holdout_consumed"):
            raise ValueError("holdout ran; the run cannot resume")
        configuration = Configuration.load(harness_config, model) if factory is Codex or harness_config is not None else None
        session = _Session(out, model, maximum, seconds,
                           configuration.create if configuration is not None else factory, configuration)
        try:
            recorded = session.record.get("preflight")
            preflight = (session.provider.preflight(cast(dict[str, object], recorded) if isinstance(recorded, dict) else None)
                         if isinstance(session.provider, Harness) else session.provider.preflight())
            environment_hash = preflight.get("environment_hash")
            _ = session.record.setdefault("environment_hash", environment_hash)
            if session.record["environment_hash"] != environment_hash:
                raise ValueError("runtime environment differs from the frozen record")
            session.record["preflight"] = preflight
            if configuration is None or any(role.adapter == "codex" for role in configuration.roles.values()):
                session.record["codex_version"] = VERSION
            session.checkpoint()
            if stage in {"baseline", "self-test"}:
                session.baseline()
            if stage == "search":
                session.search(mode, brief_text)
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
            ("schema_version", "phase", "calls", "model", "codex_version", "harness", "judge",
             "improvement", "token_comparison", "locked_arms")}


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
        raise ValueError("evaluator differs from the frozen record; create a new run")
    if "judge" in record:
        judge_model = _text_field(record, "judge_model" if "judge_model" in record else "model")
        if record["judge"] != judge_identity(judge_model):
            raise ValueError("judge differs from the frozen record; create a new run")
    arms = mapping(_field(record, "arms"))
    if arm not in get_args(Mode) or arm not in arms:
        raise ValueError("export arm must be an evaluated prompt, prompt-cli, cli, or wedge arm")
    outcomes = _outcomes(record)
    seed, candidate = candidate_files(_field(record, "seed")), candidate_files(arms[arm])
    names = (*seed, *(name for name in candidate if name not in seed))
    lines = (line for name in names if seed.get(name) != candidate[name]
             for line in difflib.unified_diff(seed.get(name, "").splitlines(keepends=True),
                   candidate[name].splitlines(keepends=True),
                   fromfile="a/" + name if name in seed else "/dev/null", tofile="b/" + name))
    patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
    destination.mkdir(mode=0o700)
    descriptor = os.open(destination / "candidate.patch", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        _ = stream.write(patch)
    report = summary(record) | {"sharing": "private-local-only", "arm": arm,
                                "outcomes": [_export_outcome(item) for item in outcomes], "cost_usd": None}
    write(destination / "report.json", report)
    return {"export": str(destination), "sharing": "private-local-only", "installed": False}

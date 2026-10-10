from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

from typing_extensions import override

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._cli import main
from skillz_experiments._gate import case_deltas, verdict
from skillz_experiments._records import read, write
from skillz_experiments._search import Edit
from skillz_experiments._runtime import Budget
from skillz_experiments._workflow import Stop, _reflection_request, export, run  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.usefixtures("host_login")


@final
class Recorder:
    """A fake provider. The seed scores 0 and any proposal that says `improved` scores 1."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.evaluated: list[tuple[str, bool]] = []
        self.prompts: list[str] = []
        self.threads = 0

    def factory(self, _model: str, budget: Budget, checkpoint: Callable[[], None]) -> Provider:
        return Provider(self, budget, checkpoint)


class Provider:
    def __init__(self, recorder: Recorder, budget: Budget, checkpoint: Callable[[], None]) -> None:
        self.recorder: Recorder = recorder
        self.budget: Budget = budget
        self.checkpoint: Callable[[], None] = checkpoint

    def preflight(self) -> dict[str, object]:
        return {"isolation": "test-provider"}

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        self.budget.claim(holdout=holdout)
        self.checkpoint()
        with self.recorder.lock:
            self.recorder.evaluated.append((case.identifier, holdout))
        return {"score": float("improved" in candidate.files["SKILL.md"]), "loaded": True, "helper_executed": True,
                "candidate_hash": candidate.identity, "case_hash": case.identifier,
                "usage": {"input_tokens": 10, "cached_input_tokens": 3, "output_tokens": 2}}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        assert candidate is None and case is None and schema is not None
        self.budget.claim(holdout=holdout)
        self.checkpoint()
        with self.recorder.lock:
            self.recorder.prompts.append(prompt)
        return {"answer": {name: "improved" for name in cast(list[str], schema["required"])}}


def split_ids(out: Path, split: str) -> list[str]:
    cases = cast(list[dict[str, object]], json.loads((out / "cases.json").read_text())["cases"])
    return [str(case["id"]) for case in cases if case["split"] == split]


@pytest.mark.usefixtures("umask_022")
def test_run_gates_baseline_and_winner_on_unseen_holdout_with_three_repeats(tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    target, out, provider = make_target(tmp_path), tmp_path / "run", Recorder()
    seed_text = (target / "SKILL.md").read_text()
    result = approved_run(target, out, factory=provider.factory)
    assert result["phase"] == "complete"
    holdout = split_ids(out, "holdout")
    assert len(holdout) >= 6
    searched = {name for name, held in provider.evaluated if not held}
    assert searched and searched.isdisjoint(holdout)
    assert {name for name, held in provider.evaluated if held} == set(holdout)
    assert not any(f"request-{name}-end" in prompt for name in holdout for prompt in provider.prompts)
    record = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    scored = [item for item in outcomes if item["split"] == "holdout"]
    for arm in ("baseline", "winner"):
        for name in holdout:
            assert sorted(cast(int, item["repeat"]) for item in scored if item["arm"] == arm and item["case_id"] == name) == [0, 1, 2]
    scores = {arm: {name: [float(cast(float, item["score"])) for item in scored
                           if item["arm"] == arm and item["case_id"] == name] for name in holdout}
              for arm in ("baseline", "winner")}
    expected = verdict(case_deltas(scores["baseline"], scores["winner"]))
    gate = cast(dict[str, object], record["gate"])
    assert (gate["verdict"], gate["delta"], gate["se"], gate["cases"]) == (
        expected.verdict, expected.delta, expected.se, expected.cases)
    assert gate["repeats"] == 3 and gate["verdict"] == "promote"
    assert int(str(record["calls"])) <= 200
    assert (target / "SKILL.md").read_text() == seed_text
    destination = tmp_path / "export"
    exported = export(out, destination)
    assert exported["installed"] is False
    assert (destination / "candidate.patch").stat().st_mode & 0o077 == 0
    check = subprocess.run(["git", "apply", "--check", str(destination / "candidate.patch")], cwd=target,
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert not any(f"request-{name}-end" in (destination / "report.json").read_text() for name in holdout)


@pytest.mark.parametrize("command", ["dataset", "baseline", "search", "evaluate"])
def test_removed_commands_exit_with_a_coded_error_naming_run(command: str,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    assert main([command, "anything"]) == 1
    error = cast(dict[str, str], json.loads(capsys.readouterr().err))
    assert error["code"] == "command-removed" and "`run`" in error["error"] and command in error["error"]


def test_old_schema_run_record_stops_with_a_coded_error(tmp_path: Path, make_target: Callable[..., Path]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    out.mkdir(mode=0o700)
    write(out / "run.json", {"schema_version": 1, "phase": "baseline"})
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, "local-test", live=True)
    assert caught.value.code == "run-schema-old"


def test_missing_draft_stops_with_the_facts_and_makes_no_model_call(tmp_path: Path, make_target: Callable[..., Path]) -> None:
    target, out, provider = make_target(tmp_path), tmp_path / "run", Recorder()
    with pytest.raises(Stop) as caught:
        _ = run(target, out, "local-test", live=True, factory=provider.factory)
    assert caught.value.code == "cases-missing"
    assert "facts" in caught.value.data and str(out / "cases.draft.json") in str(caught.value.data["draft"])
    assert provider.evaluated == [] and provider.prompts == [] and not (out / "run.json").exists()


def test_unapproved_cases_stop_and_a_wrong_hash_is_not_enough(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]]) -> None:
    target, out, provider = make_target(tmp_path), tmp_path / "run", Recorder()
    _ = write_draft(out)
    for approval in (None, "0" * 64):
        with pytest.raises(Stop) as caught:
            _ = run(target, out, "local-test", live=True, approve_cases=approval, factory=provider.factory)
        assert caught.value.code == "cases-unapproved"
        assert "question" in caught.value.data and "case_hash" in caught.value.data
    assert provider.evaluated == [] and provider.prompts == [] and not (out / "run.json").exists()


def test_unapproved_budget_stops_with_the_estimate_and_makes_no_model_call(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, provider = make_target(tmp_path), tmp_path / "run", Recorder()
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as caught:
        _ = run(target, out, "local-test", live=True, approve_cases=case_hash, approve_budget=calls - 1,
                factory=provider.factory)
    assert caught.value.code == "budget-unapproved"
    assert cast(dict[str, int], caught.value.data["estimate"])["calls"] == calls
    assert provider.evaluated == [] and provider.prompts == [] and not (out / "run.json").exists()


def test_oversized_holdout_fails_closed_before_any_approval_is_asked(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, _calls = approvals(target, out)
    with pytest.raises(Stop) as caught:
        _ = run(target, out, "local-test", live=True, repeats=40, approve_cases=case_hash, approve_budget=200)
    assert caught.value.code == "budget-unapproved"


def test_a_run_without_live_stops_after_the_approvals_and_makes_no_call(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, provider = make_target(tmp_path), tmp_path / "run", Recorder()
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as caught:
        _ = run(target, out, "local-test", approve_cases=case_hash, approve_budget=calls, factory=provider.factory)
    assert caught.value.code == "live-required" and caught.value.data["next"] == "call run again with --live"
    assert provider.evaluated == [] and read(out / "run.json")["phase"] == "prepared"


@final
class Failing(Provider):
    """Fail the first holdout call, then behave."""

    failed = False

    @override
    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        if holdout and not Failing.failed:
            Failing.failed = True
            raise RuntimeError("provider fell over")
        return super().evaluate(candidate, case, holdout=holdout)


def test_a_failed_gate_keeps_the_search_and_resumes_with_the_approved_budget_without_repeating_work(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    Failing.failed = False
    target, out, provider = make_target(tmp_path), tmp_path / "run", Recorder()
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)

    def factory(_model: str, budget: Budget, checkpoint: Callable[[], None]) -> Provider:
        return Failing(provider, budget, checkpoint)

    with pytest.raises(RuntimeError, match="fell over"):
        _ = run(target, out, "local-test", live=True, approve_cases=case_hash, approve_budget=calls, factory=factory)
    record = read(out / "run.json")
    assert record["phase"] == "searched" and record["failure"] == "provider fell over"
    spent = cast(int, record["calls"])
    searched = len(provider.evaluated)
    result = run(target, out, "local-test", live=True, factory=factory)
    assert result["phase"] == "complete"
    assert len(provider.evaluated) > searched
    assert not any(not held for _, held in provider.evaluated[searched:])
    assert cast(int, read(out / "run.json")["calls"]) <= calls and spent < calls
    assert run(target, out, "local-test", live=True, factory=factory) == result


def test_a_winner_equal_to_the_baseline_is_inconclusive_and_spends_no_holdout_call(tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"

    @final
    class Unchanged(Provider):
        @override
        def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
                   *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
            assert schema is not None
            seed = cast(dict[str, str], json.loads(prompt.rsplit("\n", 1)[1])["candidate"])
            self.budget.claim(holdout=holdout)
            return {"answer": {name: seed[name] for name in cast(list[str], schema["required"])}}

    provider = Recorder()
    def factory(_model: str, budget: Budget, checkpoint: Callable[[], None]) -> Provider:
        return Unchanged(provider, budget, checkpoint)

    result = approved_run(target, out, factory=factory)
    gate = cast(dict[str, object], result["gate"])
    assert gate["verdict"] == "inconclusive" and gate["reason"] == "winner-equals-baseline"
    assert gate["reasons"] == ["winner-equals-baseline"]
    assert not any(held for _, held in provider.evaluated)


@pytest.mark.parametrize(("edit", "components"), [("prose", ["SKILL.md"]),
                                                  ("prose+cli", ["SKILL.md", "scripts/inspect_skill.py"])])
def test_every_reflection_request_requires_ste_prose_and_carries_the_candidate(
        edit: str, components: list[str], limits: tuple[int, int]) -> None:
    advisory, maximum = limits
    prompt, schema = _reflection_request(cast(Edit, edit), {name: "text" for name in components}, {}, components)
    assert "ASD-STE100" in prompt and schema["required"] == components
    assert f"at most {advisory} words per procedural sentence" in prompt
    assert f"at most {maximum} words per descriptive sentence" in prompt
    payload = cast(dict[str, object], json.loads(prompt.split("\n", 1)[1]))
    assert payload["candidate"] == {name: "text" for name in components}
    assert ("helper script components" in prompt) == (edit == "prose+cli")


def test_prose_and_cli_run_proposes_a_helper_edit_and_exports_it_without_holdout_text(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, provider = make_target(tmp_path, helper=True), tmp_path / "run", Recorder()
    _ = write_draft(out)
    case_hash, calls = approvals(target, out, edit="prose+cli")
    result = run(target, out, "local-test", live=True, edit="prose+cli", approve_cases=case_hash,
                 approve_budget=calls, factory=provider.factory)
    assert result["phase"] == "complete" and provider.prompts
    payload = cast(dict[str, dict[str, str]], json.loads(provider.prompts[0].rsplit("\n", 1)[1]))
    assert set(payload["candidate"]) == {"SKILL.md", "scripts/echo.py"}
    holdout = split_ids(out, "holdout")
    assert not any(f"request-{name}-end" in prompt for name in holdout for prompt in provider.prompts)
    _ = export(out, tmp_path / "export")
    assert "scripts/echo.py" in (tmp_path / "export/candidate.patch").read_text()
    assert (target / "scripts/echo.py").read_text().startswith("import json")

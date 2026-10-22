"""The run `Provider` port: Session builds one provider through one factory and calls every provider alike."""
from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments._candidate import candidate_files
from skillz_experiments._contract import parse
from skillz_experiments._records import read
from skillz_experiments.harness import Configuration, EnvironmentDiffers
from skillz_experiments.run import run
from skillz_experiments.run import _session as session_module
from test_run_restore import MODEL, State
from test_run_restore import _Fake, _harness, _stopped_at_gate  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.usefixtures("host_login", "headroom_off")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


class _Facts(_Fake):
    run_facts: dict[str, object] = {"provider_fact": "kept"}


def _create_spy(monkeypatch: pytest.MonkeyPatch, state: State) -> list[tuple[Path | None, object, object]]:
    """Record the `out`, surface, and files that each `Configuration.create` call gets, and hand back a fake provider.

    The real `create` rejects a surface on a non-Claude harness, so the fake stands in for it.
    """
    calls: list[tuple[Path | None, object, object]] = []

    def create(self: Configuration, model: str, budget: Budget, checkpoint: Callable[[], None],
               out: Path | None = None, surface: object = None, files: object = None):  # noqa: ANN202
        del self, model, budget, checkpoint
        calls.append((out, surface, files))
        return _Fake(state)

    monkeypatch.setattr(Configuration, "create", create)
    return calls


@pytest.mark.ac("gh-398:AC-1")
def test_a_fake_provider_receives_the_recorded_preflight_evidence(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _stopped_at_gate(tmp_path, target, write_draft, approvals, state)
    recorded = read(out / "run.json")["preflight"]
    assert state.preflights == [None]
    result = run(target, out, MODEL, live=True, factory=state.factory())
    assert result["phase"] == "complete" and state.preflights == [None, recorded]


@pytest.mark.ac("gh-398:AC-2")
def test_the_run_facts_of_a_fake_provider_reach_the_run_record(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state = make_target(tmp_path), tmp_path / "run", State()
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
            factory=lambda _model, _budget, _checkpoint: _Facts(state))
    assert read(out / "run.json")["provider_fact"] == "kept"


@pytest.mark.ac("gh-398:AC-3")
@pytest.mark.parametrize("supplied", [True, False], ids=["supplied-configuration", "split-configuration"])
def test_without_a_factory_the_default_factory_calls_configuration_create_with_out_surface_and_files(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch, supplied: bool) -> None:
    target, out, state = make_target(tmp_path, surface={"tools": ["lookup"]}), tmp_path / "run", State()
    configuration = _harness(tmp_path, monkeypatch, state)
    calls = _create_spy(monkeypatch, state)
    _ = write_draft(out)
    case_hash, approved = approvals(target, out)
    _ = run(target, out, MODEL, adapter="codex", live=True, approve_cases=case_hash,
            approve_budget=approved, configuration=configuration if supplied else None)
    assert len(calls) == 1
    created_out, surface, files = calls[0]
    record = read(out / "run.json")
    expected = parse(record["contract"], str(record["contract_source"])).surface
    assert expected is not None and expected.tools == ("lookup",)
    assert created_out == out and surface == expected
    assert files and files == candidate_files(record["seed"])


@pytest.mark.ac("gh-398:AC-4")
def test_session_names_no_harness_and_builds_the_provider_in_one_call() -> None:
    tree = ast.parse(Path(session_module.__file__).read_text())
    named = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert "Harness" not in named | imported
    creates = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and node.func.attr == "create"]
    lambdas = [inner for node in ast.walk(tree) if isinstance(node, ast.Lambda) for inner in ast.walk(node)]
    assert len(creates) == 1 and any(creates[0] is inner for inner in lambdas)
    providers = [node for node in ast.walk(tree) if isinstance(node, ast.Assign | ast.AnnAssign)
                 and any(isinstance(target, ast.Attribute) and target.attr == "provider"
                         for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
    assert len(providers) == 1 and isinstance(providers[0].value, ast.Call)


@pytest.mark.ac("gh-398:AC-5")
def test_a_fake_whose_preflight_returns_another_environment_hash_stops_as_environment_differs(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _stopped_at_gate(tmp_path, target, write_draft, approvals, state)
    seen = len(state.evaluated)
    state.environment = "e2"
    with pytest.raises(EnvironmentDiffers, match=r"environment_hash \(recorded '.*', observed 'e2'\)"):
        _ = run(target, out, MODEL, live=True, factory=state.factory())
    assert len(state.evaluated) == seen

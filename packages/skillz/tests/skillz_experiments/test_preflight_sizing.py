"""Size the preflight calls from the resolved role configuration, so a baseline finishes every slot within its approval."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from fake_provider import search_approval
from skillz_experiments._cases import mapping
from skillz_experiments._cli import main
from skillz_experiments._records import read
from skillz_experiments.harness import Configuration
from skillz_experiments.run import run
from skillz_experiments.run._plan import PREFLIGHT_CALLS, preflight_calls
from test_run_search import MODEL, Provider, Search

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]


def _claim_like_the_harness(claude_roles: int) -> Callable[[Provider, dict[str, object] | None], dict[str, object]]:
    """Return a preflight that claims two budgeted calls per Claude role, as `Harness.preflight` does on a first run.

    Each Claude role runs a Bash sandbox check and a Read file-access check, and each check claims one call.
    A resume reuses the recorded pass and claims none.
    """
    def preflight(self: Provider, recorded: dict[str, object] | None = None) -> dict[str, object]:
        if recorded is None and self.budget.calls == 0:
            for _ in range(2 * claude_roles):
                self.budget.claim(role="preflight")
        return {"isolation": "test-provider"}
    return preflight


def _judged_target(tmp_path: Path, make_target: Maker) -> Path:
    """Return a target whose only kind is judged, so each case costs a task call and a judge call."""
    target = make_target(tmp_path)
    path = target / "evals/autoimprove.json"
    contract = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    contract["kinds"] = {"task": {"grader": "judge", "rubric": "Grade each criterion."}}
    _ = path.write_text(json.dumps(contract), encoding="utf-8")
    return target


@pytest.mark.ac("gh-756:AC-10")
@pytest.mark.usefixtures("host_login")
def test_a_rubric_baseline_with_an_explicit_claude_simulator_finishes_every_slot_within_its_approval(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    base = Configuration.single("claude", MODEL)
    configuration = Configuration(base.roles | {"simulator": base.roles["judge"]})
    monkeypatch.setattr(Provider, "preflight", _claim_like_the_harness(len(configuration.roles)))
    target, out = _judged_target(tmp_path, make_target), tmp_path / "run"
    _ = write_draft(out, families=6)
    case_hash, calls = approvals(target, out, MODEL, configuration=configuration)
    state = Search(target)
    report = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory,
                 configuration=configuration)
    record = read(out / "run.json")
    assert report["search"] == "not-run" and "headroom" in record
    assert mapping(record["estimate"])["calls"] == calls == mapping(record["budget"])["maximum"]
    assert cast(int, record["calls"]) == calls
    assert mapping(record["calls_by_role"])["preflight"] == preflight_calls(configuration.identity()) == 8

    approved = search_approval(target, out, MODEL, configuration=configuration)
    _ = run(target, out, MODEL, live=True, search=True, approve_budget=approved, factory=state.factory,
            configuration=configuration)
    assert read(out / "run.json")["plan_mode"] == "search"


@pytest.mark.ac("gh-756:AC-10")
@pytest.mark.usefixtures("host_login")
def test_a_default_baseline_keeps_the_six_call_preflight_allowance(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = _judged_target(tmp_path, make_target), tmp_path / "run"
    _ = write_draft(out, families=6)
    _, calls = approvals(target, out, MODEL)
    _, with_roles = approvals(target, out, MODEL, configuration=Configuration.single("claude", MODEL))
    assert calls == with_roles
    assert preflight_calls(None) == preflight_calls(Configuration.single("claude", MODEL).identity()) == PREFLIGHT_CALLS == 6


def _preview_calls(capsys: pytest.CaptureFixture[str], target: Path, draft: Path, *extra: str) -> int:
    assert main(["plan", "--target", str(target), "--draft", str(draft), "--edit", "prose", *extra]) == 0
    preview = mapping(mapping(cast(object, json.loads(capsys.readouterr().out)))["preview"])
    return cast(int, mapping(preview["estimate"])["calls"])


@pytest.mark.ac("gh-756:AC-10")
def test_a_preview_with_four_claude_roles_sizes_the_eight_call_preflight_that_search_approval_counts(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, capsys: pytest.CaptureFixture[str]) -> None:
    target, out = _judged_target(tmp_path, make_target), tmp_path / "run"
    _ = write_draft(out, families=6)
    draft = out / "cases.draft.json"
    roles = tmp_path / "roles.json"
    _ = roles.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "roles": {"simulator": {}}}), encoding="utf-8")
    three = tmp_path / "three.json"
    _ = three.write_text(json.dumps({"schema_version": 1, "adapter": "claude"}), encoding="utf-8")
    # A raised cap keeps the search below it, so the preflight difference shows in the calls.
    cap = ("--planning-call-cap", "400")
    default = _preview_calls(capsys, target, draft, *cap)
    assert _preview_calls(capsys, target, draft, *cap, "--harness-config", str(three)) == default
    configured = _preview_calls(capsys, target, draft, *cap, "--harness-config", str(roles))
    base = Configuration.single("claude", MODEL)
    four = Configuration(base.roles | {"simulator": base.roles["judge"]})
    assert Configuration.identity_from_file(roles) == {name: {"adapter": "claude"} for name in four.roles}
    assert configured - default == preflight_calls(four.identity()) - PREFLIGHT_CALLS == 2

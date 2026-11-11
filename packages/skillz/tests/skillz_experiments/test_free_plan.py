from __future__ import annotations

import ast
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from test_journey_kinds import journeys, task_and_journey, tasks

from skillz_experiments._cases import load_cases
from skillz_experiments._cli import main
from skillz_experiments._contract import load_contract
from skillz_experiments._intake import case_hash, freeze, load_draft, split_cases
from skillz_experiments.harness import Configuration
from skillz_experiments.harness._roles import parse_role_offline
from skillz_experiments.run import Stop, run

Maker = Callable[..., Path]


def _plan(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, object]]:
    code = main(["plan", *args])
    return code, cast(dict[str, object], json.loads(capsys.readouterr().out))


def _config(path: Path, **fields: object) -> Path:
    _ = path.write_text(json.dumps({"schema_version": 1, **fields}))
    return path


@pytest.mark.ac("gh-721:AC-4")
def test_plan_adds_preview_but_validate_keeps_diagnostic_schema(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    code, report = _plan(capsys, "--target", str(target), "--edit", "prose")
    assert code == 0 and "preview" in report
    code = main(["validate", "--target", str(target), "--edit", "prose"])
    validated = cast(dict[str, object], json.loads(capsys.readouterr().out))
    assert code == 0 and set(validated) == {"ok", "contract", "cases", "split", "findings"}


@pytest.mark.ac("gh-721:AC-7")
def test_planning_cap_changes_size_ceiling_not_runtime_approval(
        tmp_path: Path, make_target: Maker, write_draft: Callable[[Path], list[str]],
        capsys: pytest.CaptureFixture[str]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    code, report = _plan(capsys, "--target", str(target), "--draft", str(out / "cases.draft.json"),
                         "--edit", "prose", "--planning-call-cap", "300")
    preview = cast(dict[str, object], report["preview"])
    inputs = cast(dict[str, object], preview["inputs"])
    assert code == 0 and inputs["requested_cap"] == 300 and inputs["effective_ceiling_calls"] == 300
    assert not (out / "cases.json").exists()
    code, high = _plan(capsys, "--target", str(target), "--draft", str(out / "cases.draft.json"),
                       "--edit", "prose", "--planning-call-cap", "1001")
    blocked = cast(dict[str, object], high["preview"])
    assert code == 1 and blocked["estimate"] is None and blocked["editable_files"] == ["SKILL.md"]
    assert cast(dict[str, object], blocked["edit"])["resolved"] == "prose"
    assert any(item["code"] == "budget-cap-exceeded" for item in cast(list[dict[str, object]], high["findings"]))


@pytest.mark.ac("gh-721:AC-10")
@pytest.mark.ac("gh-721:AC-14")
def test_invalid_unused_role_retains_edit_and_split_without_estimate(
        tmp_path: Path, make_target: Maker, write_draft: Callable[[Path], list[str]],
        capsys: pytest.CaptureFixture[str]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    config = _config(tmp_path / "config.json", roles={"judge": {"adapter": "claude", "sandbox_seconds": 601}})
    code, report = _plan(capsys, "--target", str(target), "--draft", str(out / "cases.draft.json"),
                         "--edit", "prose", "--harness-config", str(config))
    preview = cast(dict[str, object], report["preview"])
    assert code == 1 and preview["editable_files"] == ["SKILL.md"]
    assert preview["estimate"] is None and preview["assignments"]
    assert cast(dict[str, object], preview["unavailable"])["estimate"] == "configuration-invalid"
    assert any("sandbox_seconds" in str(item["message"]) for item in cast(list[dict[str, object]], report["findings"]))


@pytest.mark.ac("gh-721:AC-17")
def test_parsed_draft_with_bad_criteria_keeps_assignments_not_estimate(
        tmp_path: Path, make_target: Maker, write_draft: Callable[[Path], list[str]],
        capsys: pytest.CaptureFixture[str]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    path = out / "cases.draft.json"
    contract_path = target / "evals/autoimprove.json"
    contract = cast(dict[str, object], json.loads(contract_path.read_text()))
    contract["kinds"] = {"task": {"grader": "judge", "rubric": "Grade each criterion."}}
    _ = contract_path.write_text(json.dumps(contract))
    cases = cast(list[dict[str, object]], json.loads(path.read_text()))
    cast(dict[str, object], cases[0]["expected"])["criteria"] = []
    _ = path.write_text(json.dumps(cases))
    code, report = _plan(capsys, "--target", str(target), "--draft", str(path), "--edit", "prose")
    preview = cast(dict[str, object], report["preview"])
    assert code == 1 and preview["assignments"] and preview["estimate"] is None
    assert cast(dict[str, object], preview["unavailable"])["estimate"] == "case-criteria-missing"


@pytest.mark.ac("gh-721:AC-18")
def test_offline_adapter_rejects_evaluated_journey_transport(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "culture"
    fixture = Path(__file__).parent / "fixtures/culture-journey"
    _ = shutil.copytree(fixture, target)
    _ = (target / "SKILL.md.fixture").rename(target / "SKILL.md")
    _ = (target / "evals/gates/journey.py.fixture").rename(target / "evals/gates/journey.py")
    journeys = [{"id": f"j{family}-{index}", "family": f"f{family}", "kind": "journey",
                 "request": f"Discuss {family}.{index}", "brief": "Lead the conversation.", "max_turns": 3,
                 "expected": {"must": ["asks a question"]}, "source": "skill"}
                for family in range(5) for index in range(3 if family < 3 else 2)]
    _ = (target / "evals/journeys.json").write_text(json.dumps(journeys))
    config = _config(tmp_path / "config.json", adapter="command", command=["absent-executable"], identity="offline")
    code, report = _plan(capsys, "--target", str(target), "--edit", "prose", "--harness-config", str(config))
    assert code == 1
    assert any(item["code"] == "journey-unsupported" for item in cast(list[dict[str, object]], report["findings"])), report["findings"]


@pytest.mark.ac("gh-721:AC-13")
def test_runtime_configuration_still_requires_model_and_executable(tmp_path: Path) -> None:
    config = _config(tmp_path / "config.json", adapter="command", command=["absent-executable"], identity="offline")
    assert parse_role_offline({"adapter": "command", "command": ["absent-executable"], "identity": "offline"}, tmp_path).model is None
    assert Configuration.sizing_options_from_file(config).sandbox_seconds == 20
    with pytest.raises(ValueError, match="executable"):
        _ = Configuration.load(config, "local")
    with pytest.raises(ValueError, match="model"):
        _ = Configuration.load(_config(tmp_path / "codex.json"), None)


@pytest.mark.ac("gh-721:AC-6")
def test_plan_estimate_matches_the_free_runtime_budget_stop(
        tmp_path: Path, make_target: Maker, write_draft: Callable[[Path], list[str]],
        capsys: pytest.CaptureFixture[str]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    code, report = _plan(capsys, "--target", str(target), "--draft", str(out / "cases.draft.json"), "--edit", "prose")
    assert code == 0
    preview = cast(dict[str, object], report["preview"])
    with pytest.raises(Stop) as first:
        _ = run(target, out, "local-test", live=True, edit="prose")
    assert first.value.code == "cases-unapproved"
    approved = cast(str, first.value.data["case_hash"])
    with pytest.raises(Stop) as second:
        _ = run(target, out, "local-test", live=True, edit="prose", approve_cases=approved)
    assert second.value.code == "budget-unapproved"
    assert preview["estimate"] == second.value.data["estimate"]


@pytest.mark.ac("gh-721:AC-8")
def test_plan_export_only_raises_the_approved_run_surface_cap() -> None:
    from skillz_experiments import run as run_module

    root = Path(__file__).resolve().parents[4]
    caps = cast(dict[str, object], json.loads((root / "depth-ratchet.json").read_text()))
    assert callable(run_module.plan)
    source = ast.parse((root / "packages/skillz/src/skillz_experiments/run/__init__.py").read_text())
    exports = next(node.value for node in source.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets))
    assert set(cast(list[str], ast.literal_eval(exports))) == {
        "DEFAULT_SEED", "Stop", "audit_targets", "compare", "export", "frozen_targets", "plan",
        "resolve_statistics", "run", "scored_kinds", "summary", "watch"}
    assert cast(dict[str, object], cast(dict[str, object], caps["slices"])["run"])["M09.public_surface"] == 12


@pytest.mark.ac("gh-721:AC-5")
def test_invalid_draft_is_partial_with_a_specific_unavailable_reason(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target, draft = make_target(tmp_path), tmp_path / "broken.json"
    _ = draft.write_text("{")
    code, report = _plan(capsys, "--target", str(target), "--draft", str(draft), "--edit", "prose")
    preview = cast(dict[str, object], report["preview"])
    assert code == 1 and preview["editable_files"] == ["SKILL.md"]
    assert preview["estimate"] is None and preview["assignments"] == []
    assert cast(dict[str, object], preview["unavailable"])["estimate"] == "invalid"



@pytest.mark.ac("gh-721:AC-5")
def test_edit_failure_keeps_independent_plan_inputs(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    config = _config(tmp_path / "config.json", adapter="claude", command=["absent-executable"], sandbox_seconds=60)
    code, report = _plan(capsys, "--target", str(target), "--edit", "prose+cli",
                         "--harness-config", str(config), "--repeats", "3", "--planning-call-cap", "300")
    preview = cast(dict[str, object], report["preview"])
    inputs = cast(dict[str, object], preview["inputs"])
    assert code == 1 and preview["estimate"] is None
    assert {"repeats": 3, "task_timeout": 60, "requested_cap": 300,
            "effective_ceiling_calls": 300}.items() <= inputs.items()
    assert preview["not_checked"]
    assert cast(dict[str, object], preview["unavailable"])["estimate"] == "helper-missing"
    assert any(item["code"] == "helper-missing" for item in cast(list[dict[str, object]], report["findings"]))

@pytest.mark.ac("gh-721:AC-10")
def test_plan_rejects_conflicting_timeout_sources_without_harness_lookup(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    config = _config(tmp_path / "config.json", adapter="claude", command=["absent-executable"])
    code, report = _plan(capsys, "--target", str(target), "--edit", "prose",
                         "--harness-config", str(config), "--sandbox-seconds", "60")
    assert code == 1
    assert any(item["code"] == "usage" for item in cast(list[dict[str, object]], report["findings"]))


@pytest.mark.ac("gh-721:AC-18")
def test_offline_adapter_rejects_selected_judge_capture(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    contract_path = target / "evals/autoimprove.json"
    contract = cast(dict[str, object], json.loads(contract_path.read_text()))
    contract["kinds"] = {"task": {"grader": "judge", "rubric": "Grade each criterion.",
                                  "capture": ["/bin/true"]}}
    _ = contract_path.write_text(json.dumps(contract))
    config = _config(tmp_path / "config.json", roles={"task": {"adapter": "claude",
                                                                "command": ["absent-executable"]}})
    code, report = _plan(capsys, "--target", str(target), "--edit", "prose", "--harness-config", str(config))
    assert code == 1
    assert any(item["code"] == "contract-capture-unsupported"
               for item in cast(list[dict[str, object]], report["findings"]))


@pytest.mark.ac("gh-721:AC-7")
def test_a_nonfitting_requested_size_keeps_fitting_alternative_separate(
        tmp_path: Path, make_target: Maker, write_draft: Callable[[Path], list[str]],
        capsys: pytest.CaptureFixture[str]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    code, report = _plan(capsys, "--target", str(target), "--draft", str(out / "cases.draft.json"),
                         "--edit", "prose", "--repeats", "20")
    preview = cast(dict[str, object], report["preview"])
    assert code == 0 and preview["estimate"] is None
    assert cast(dict[str, object], preview["unavailable"])["estimate"] == "budget-unapproved"
    alternatives = cast(dict[str, object], preview["alternatives"])
    assert isinstance(alternatives["smallest_approval"], int)
    assert isinstance(alternatives["estimate"], dict)


@pytest.mark.ac("gh-721:AC-5")
def test_invalid_draft_keeps_independent_seed_projection(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    entries: list[dict[str, object]] = [{"name": f"{family}-{index}", "prompt": f"prompt-{family}-{index}",
                "expected_output": "out", "files": [], "family": family}
               for family in ("a", "b", "c") for index in range(2)]
    _ = (target / "evals/evals.json").write_text(json.dumps({"skill_name": "echo-skill", "evals": entries}))
    draft = tmp_path / "invalid.json"
    _ = draft.write_text("{")
    code, report = _plan(capsys, "--target", str(target), "--draft", str(draft), "--edit", "prose")
    preview = cast(dict[str, object], report["preview"])
    seed = cast(dict[str, object], preview["seed"])
    split = cast(dict[str, object], seed["split"])
    assert code == 1 and preview["estimate"] is None and preview["assignments"] == []
    assert split["families"] == {"a": 2, "b": 2, "c": 2}
    assert cast(dict[str, object], preview["unavailable"])["estimate"] == "invalid"


@pytest.mark.ac("gh-721:AC-5")
@pytest.mark.parametrize("cap,reason", [(None, "valid-draft-required"), ("1001", "budget-cap-exceeded")])
def test_partial_preview_keeps_reflection_size_warning(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str], cap: str | None, reason: str) -> None:
    target = make_target(tmp_path, helper=True)
    line = "# " + "x" * 98 + "\n"
    for name in ("scripts/big_a.py", "scripts/big_b.py"):
        _ = (target / name).write_text(line * 1010)
    args = ["--target", str(target), "--edit", "prose+cli"]
    if cap is not None:
        args.extend(("--planning-call-cap", cap))
    code, report = _plan(capsys, *args)
    preview = cast(dict[str, object], report["preview"])
    warnings = cast(list[str], preview["warnings"])
    assert code == (0 if cap is None else 1) and preview["estimate"] is None
    assert cast(dict[str, object], preview["unavailable"])["estimate"] == reason
    assert len(warnings) == 1 and "reflection limit" in warnings[0]
    codes = [item["code"] for item in cast(list[dict[str, object]], report["findings"])]
    assert codes == (["edit-warning"] if cap is None else ["budget-cap-exceeded", "edit-warning"])


@pytest.mark.ac("gh-721:AC-14")
@pytest.mark.parametrize("drafted", [False, True])
def test_invalid_repeats_keep_completed_preview(
        tmp_path: Path, make_target: Maker, write_draft: Callable[[Path], list[str]],
        capsys: pytest.CaptureFixture[str], drafted: bool) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    args = ["--target", str(target), "--edit", "prose", "--repeats", "0"]
    if drafted:
        _ = write_draft(out)
        args.extend(("--draft", str(out / "cases.draft.json")))
    code, report = _plan(capsys, *args)
    preview = cast(dict[str, object], report["preview"])
    assert code == 1 and preview["estimate"] is None
    assert preview["editable_files"] == ["SKILL.md"]
    assert cast(dict[str, object], preview["inputs"])["repeats"] == 0
    assert preview["not_checked"]
    assert cast(dict[str, object], preview["unavailable"])["estimate"] == "invalid-repeats"
    assert any(item["code"] == "invalid-repeats" for item in cast(list[dict[str, object]], report["findings"]))


@pytest.mark.ac("gh-721:AC-6")
@pytest.mark.ac("gh-721:AC-7")
def test_journey_cap_and_eligibility_match_freeze_and_runtime_stop(
        tmp_path: Path, make_target: Maker, capsys: pytest.CaptureFixture[str]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    task_and_journey(target)
    out.mkdir(mode=0o700)
    draft = out / "cases.draft.json"
    pending = {"id": "pending", "family": "pending", "kind": "trigger",
               "request": "Check the trigger.", "source": "skill"}
    _ = draft.write_text(json.dumps([*journeys("journey", "j", 6, 2), *tasks(2), pending]))
    code, report = _plan(capsys, "--target", str(target), "--draft", str(draft), "--edit", "prose",
                         "--kind", "journey", "--planning-call-cap", "1001")
    preview = cast(dict[str, object], report["preview"])
    inputs = cast(dict[str, object], preview["inputs"])
    assignments = cast(list[dict[str, object]], preview["assignments"])
    assert code == 0 and inputs["requested_cap"] == 1001
    assert inputs["effective_ceiling_calls"] == 1000
    assert {item["id"] for item in assignments if item["scored"]} == {
        item["id"] for item in assignments if str(item["id"]).startswith("j")}
    assert {item["id"] for item in assignments if not item["scored"]} == {
        "t0-0", "t0-1", "t1-0", "t1-1", "pending"}

    contract = load_contract(target)
    seed = cast(int, inputs["seed"])
    cases = split_cases(load_draft(draft, contract.journey_kinds(), False), seed, None, ["journey"])
    frozen = tmp_path / "frozen.json"
    freeze(frozen, cases, seed, case_hash(cases, seed))
    loaded = [case for case in load_cases(frozen, contract.grader_types()) if case.eligible]
    assert {case.identifier for case in loaded} == {item["id"] for item in assignments if item["scored"]}
    with pytest.raises(Stop) as first:
        _ = run(target, out, "local-test", live=True, edit="prose", kinds=("journey",))
    assert first.value.code == "cases-unapproved"
    with pytest.raises(Stop) as second:
        _ = run(target, out, "local-test", live=True, edit="prose", kinds=("journey",),
                approve_cases=cast(str, first.value.data["case_hash"]))
    assert second.value.code == "budget-unapproved"
    assert preview["estimate"] is None
    assert cast(dict[str, object], preview["unavailable"])["estimate"] == second.value.code

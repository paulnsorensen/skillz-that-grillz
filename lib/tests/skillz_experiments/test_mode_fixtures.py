"""Offline behavioral fixtures for every /skillz mode and the repo-local skillz-self-update skill.

Each fixture case pairs a request and a starting tree with a deterministic command grader. The tests
run that grader on a recorded golden output (accepted) and on seeded-bad outputs (rejected). They
also pin the mode steps in the skill text that each fixture depends on. No test calls a model.

Every output reaches the grader through `stage_task` and `snapshot_outputs`, the path that a live run
uses. A recorded output holds only the files that a task writes or changes.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from collections.abc import Callable, Mapping
from types import ModuleType
from typing import cast

import pytest

from skillz_experiments import _graders
from skillz_experiments._candidate import make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import Case, load_cases, mapping
from skillz_experiments._cli import main
from skillz_experiments._codex import Codex
from skillz_experiments._contract import Contract, parse
from skillz_experiments._facts import audit_facts

ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = ROOT / "skills/skillz"
PUBLISHED = SKILL_DIR / "evals/mode-fixtures.json"
FIXTURES = Path(__file__).parent / "fixtures"
SELF_UPDATE = FIXTURES / "self-update.json"
SELF_SKILL = ROOT / ".agents/skills/skillz-self-update/SKILL.md"
LAYOUT = SKILL_DIR / "references/harness-layout.md"
Tree = dict[str, str]


def _document(path: Path) -> dict[str, object]:
    return mapping(cast(object, json.loads(path.read_text(encoding="utf-8"))))


def _outputs(path: Path) -> dict[str, dict[str, object]]:
    return {key: mapping(value) for key, value in _document(path).items()}


def _rules(path: Path) -> Contract:
    return parse(_document(path)["target"], "manifest")


def _cases(path: Path) -> dict[str, Case]:
    return {case.identifier: case for case in load_cases(path, _rules(path).grader_types())}


def _variants(entry: dict[str, object], key: str) -> dict[str, Tree]:
    return {name: cast(Tree, tree) for name, tree in mapping(entry[key]).items()}


UNROUTABLE = "http://127.0.0.1:9"


class Sandbox:
    """Run the grader argv the way a task sandbox would: fixtures at the root, outputs under `output/`.

    The socket patch below covers only this process. The grader subprocess gets proxy variables that
    point to an unroutable address. A test also pins that every grader script imports only `json`,
    `pathlib`, and `re`, so a grader has no network client to route.
    """

    def __init__(self) -> None:
        self.cwds: list[Path] = []

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str]:
        self.cwds.append(workspace)
        command = [sys.executable, *argv[1:]] if argv[0] == "python3" else argv
        env = {**os.environ, **{name: UNROUTABLE for name in ("http_proxy", "https_proxy", "all_proxy",
                                                              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}}
        env["no_proxy"] = env["NO_PROXY"] = ""
        run = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=30, check=False, env=env)
        return run.returncode, run.stdout


def live(case: Case, written: Mapping[str, str | None]) -> Tree:
    """Stage the case, apply the files that a task writes, and snapshot the workspace as a live run does.

    A `None` value deletes a staged file.
    """
    with tempfile.TemporaryDirectory(prefix="mode-fixture-") as directory:
        workspace = make_workspace(Path(directory) / "workspace")
        stage_task(workspace, None, case)
        for name, text in written.items():
            target = workspace / name
            if text is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = target.write_text(text, encoding="utf-8")
        return snapshot_outputs(workspace)


def grade(path: Path, case: Case, written: Tree) -> float:
    return _graders.command(Sandbox(), _rules(path).kinds[case.kind].argv, case, live(case, written))


def _script(path: Path, kind: str) -> str:
    argv = _rules(path).kinds[kind].argv
    assert argv[:3] == ("python3", "-I", "-c") and len(argv) == 4
    return argv[3]


PUBLISHED_CASES = _cases(PUBLISHED)
PUBLISHED_OUTPUTS = _outputs(FIXTURES / "mode-outputs.json")
SELF_UPDATE_CASES = _cases(SELF_UPDATE)
SELF_UPDATE_OUTPUTS = _document(FIXTURES / "self-update-outputs.json")


def _matrix() -> list[tuple[Path, str]]:
    return ([(PUBLISHED, name) for name in PUBLISHED_CASES] + [(SELF_UPDATE, name) for name in SELF_UPDATE_CASES])


def _recorded(path: Path, name: str) -> dict[str, object]:
    return SELF_UPDATE_OUTPUTS if path == SELF_UPDATE else PUBLISHED_OUTPUTS[name]


def _golden(path: Path, name: str) -> Tree:
    return cast(Tree, mapping(_recorded(path, name)["golden"]))


BAD_CASES = [(path, name, variant) for path, name in _matrix() for variant in _variants(_recorded(path, name), "bad")]


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("a fixture test must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)


# Fixture shape


def test_published_manifest_declares_one_kind_per_mode_with_complete_splits() -> None:
    rules = _rules(PUBLISHED)
    assert set(rules.kinds) == {"add", "improve", "wedge-candidates", "wedge-clean", "wedge-agent", "contract"}
    assert all(grader.type == "command" and grader.argv for grader in rules.kinds.values())
    assert {case.kind for case in PUBLISHED_CASES.values()} == set(rules.kinds)
    splits = [case.split for case in PUBLISHED_CASES.values()]
    assert splits.count("holdout") == 2 and "train" in splits and "validation" in splits
    assert all(case.eligible and case.visibility == "public" for case in PUBLISHED_CASES.values())
    assert all(isinstance(case.expected, dict) for case in PUBLISHED_CASES.values())
    assert set(PUBLISHED_OUTPUTS) == set(PUBLISHED_CASES)


@pytest.mark.parametrize(("path", "name"), _matrix(), ids=[name for _, name in _matrix()])
def test_the_grader_lives_in_the_argv_and_never_in_a_staged_file(path: Path, name: str) -> None:
    case = _cases(path)[name]
    script = _script(path, case.kind)
    assert not any(file.startswith("check") or "'score'" in text for file, text in case.files.items())
    imported = {alias.name for node in ast.walk(ast.parse(script)) if isinstance(node, ast.Import) for alias in node.names}
    assert imported == {"json", "pathlib", "re"}


@pytest.mark.parametrize(("path", "name"), _matrix(), ids=[name for _, name in _matrix()])
def test_the_live_snapshot_holds_the_staged_files_and_an_untouched_workspace_scores_zero(path: Path, name: str) -> None:
    case = _cases(path)[name]
    assert set(live(case, {})) == set(case.files)
    assert grade(path, case, {}) == 0.0


def test_self_update_case_stays_out_of_the_published_tree() -> None:
    assert SELF_UPDATE.is_relative_to(ROOT / "lib/tests") and not SELF_UPDATE.is_relative_to(SKILL_DIR)
    assert {case.kind for case in SELF_UPDATE_CASES.values()} == {"self-update"}
    assert "self-update" not in _rules(PUBLISHED).kinds


# Golden accepted, seeded-bad rejected, deterministic


@pytest.mark.parametrize(("path", "name"), _matrix(), ids=[name for _, name in _matrix()])
def test_golden_output_is_accepted_by_the_mode_grader(path: Path, name: str) -> None:
    cases = _cases(path)
    assert grade(path, cases[name], _golden(path, name)) == 1.0


@pytest.mark.parametrize(("path", "name", "variant"), BAD_CASES, ids=[f"{n}:{v}" for _, n, v in BAD_CASES])
def test_seeded_bad_output_is_rejected_by_the_mode_grader(path: Path, name: str, variant: str) -> None:
    bad = _variants(_recorded(path, name), "bad")[variant]
    assert grade(path, _cases(path)[name], bad) == 0.0


def test_a_deleted_staged_file_counts_as_written_and_fails_a_wedge_grader() -> None:
    name = "mode-wedge-candidates"
    bad = _variants(PUBLISHED_OUTPUTS[name], "bad")["deletes-the-skill"]
    assert bad["skills/demo/SKILL.md"] is None and bad["brief.md"] == _golden(PUBLISHED, name)["brief.md"]
    assert "skills/demo/SKILL.md" not in live(PUBLISHED_CASES[name], bad)
    assert grade(PUBLISHED, PUBLISHED_CASES[name], bad) == 0.0
    assert grade(PUBLISHED, PUBLISHED_CASES[name], _golden(PUBLISHED, name)) == 1.0


def test_every_case_has_a_golden_and_a_seeded_bad_output() -> None:
    for path, name in _matrix():
        assert _golden(path, name) and _variants(_recorded(path, name), "bad")


def test_two_runs_give_identical_results() -> None:
    def run() -> list[float]:
        scores = [grade(path, _cases(path)[name], _golden(path, name)) for path, name in _matrix()]
        return scores + [grade(path, _cases(path)[name], _variants(_recorded(path, name), "bad")[variant])
                         for path, name, variant in BAD_CASES]

    first = run()
    assert first == run() and first.count(1.0) == len(_matrix())


# add and improve: audit-facts and the inspector are the deterministic checker


def _defects(tmp_path: Path, case: Case, output: Tree, skill: str) -> list[str]:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    for name, text in {**case.files, **output}.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text(text, encoding="utf-8")
    facts = cast(list[dict[str, object]], audit_facts(root / "skills" / skill)["checks"])
    found = {str(check["id"]) for check in facts if check["status"] == "fail"}
    run = subprocess.run([sys.executable, str(SKILL_DIR / "scripts/inspect_skill.py"), str(root / "skills" / skill / "SKILL.md")],
                         capture_output=True, text=True, check=True)
    if mapping(cast(object, json.loads(run.stdout)))["long_sentences"]:
        found.add("long-sentence")
    return sorted(found)


ADD_IMPROVE = [("mode-add", "release-notes"), ("mode-improve", "demo")]
SEEDED_DEFECTS = {
    ("mode-add", "no-sidecar"): ["sidecar.exists"],
    ("mode-add", "no-readme-row"): ["registration.readme-row"],
    ("mode-add", "name-differs-from-directory"): ["name.matches-directory"],
    ("mode-add", "long-sentence"): ["long-sentence"],
    ("mode-improve", "defect-left-unfixed"): ["long-sentence"],
}


@pytest.mark.parametrize(("name", "skill"), ADD_IMPROVE)
def test_audit_facts_accept_the_golden_tree(tmp_path: Path, name: str, skill: str) -> None:
    assert _defects(tmp_path, PUBLISHED_CASES[name], _golden(PUBLISHED, name), skill) == []
    expected = mapping(PUBLISHED_CASES[name].expected)
    assert expected["audit_facts_failures"] == [] and expected["long_sentences"] == []


@pytest.mark.parametrize(("name", "variant"), list(SEEDED_DEFECTS))
def test_audit_facts_name_the_seeded_defect(tmp_path: Path, name: str, variant: str) -> None:
    skill = dict(ADD_IMPROVE)[name]
    bad = _variants(PUBLISHED_OUTPUTS[name], "bad")[variant]
    assert _defects(tmp_path, PUBLISHED_CASES[name], bad, skill) == SEEDED_DEFECTS[(name, variant)]


LONG = " ".join(["word"] * 22) + "."
PARITY_INPUTS = {
    "prose": LONG,
    "tilde-fence": f"~~~\n{LONG}\n~~~",
    "backtick-fence": f"```\n{LONG}\n```",
    "indented-fence": f"   ```\n   {LONG}\n   ```",
    "long-fence-short-closer": f"````\n```\n{LONG}\n````",
    "mixed-fence-closer": f"```\n~~~\n{LONG}\n```",
    "blockquote": f"> {LONG}",
    "nested-blockquote": f"> > {LONG}",
    "blockquote-fence": f"> ```\n> {LONG}\n> ```",
    "list-item-fence": f"1. item\n   ```\n   {LONG}\n   ```",
    "list-item-tilde-fence": f"- item\n  ~~~\n  {LONG}\n  ~~~\n",
    "list-dedent": f"- item\n  more\ntext\n{LONG}",
    "list-continuation": f"- {LONG}",
    "table-row": f"| {LONG} |",
    "inline-code-fence": f"```not a fence``` {LONG}",
}


def _grader_longest(source: str) -> object:
    match = re.search(r"^MARKER = .*?^    return best$", source, re.M | re.S)
    assert match is not None
    scope: dict[str, object] = {"re": re}
    exec(match[0], scope)  # noqa: S102 - the grader source from the repository manifest
    return scope["longest"]


def _inspector() -> ModuleType:
    spec = importlib.util.spec_from_file_location("inspect_skill", SKILL_DIR / "scripts/inspect_skill.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("kind", ["add", "improve"])
@pytest.mark.parametrize("name", list(PARITY_INPUTS))
def test_grader_longest_sentence_matches_the_inspector(kind: str, name: str) -> None:
    longest = cast(Callable[[str], int], _grader_longest(_script(PUBLISHED, kind)))
    inspector = _inspector()
    prose = cast(Callable[[list[str], int], list[dict[str, int]]], inspector.prose_sentences)
    advisory = cast(int, inspector.ADVISORY_WORDS)
    text = f"---\nname: x\n---\n\n{PARITY_INPUTS[name]}\n"
    lines = text.splitlines()
    reported = prose(lines, lines.index("---", 1) + 1)
    expected = max((hit["words"] for hit in reported), default=0)
    got = longest(text)
    assert (got if got > advisory else 0) == expected


def test_improve_starting_tree_holds_the_seeded_defects(tmp_path: Path) -> None:
    case = PUBLISHED_CASES["mode-improve"]
    expected = mapping(case.expected)
    assert _defects(tmp_path, case, {}, "demo") == ["long-sentence", "sidecar.exists"]
    assert expected["seeded_defects"] == ["sidecar.exists", "long-sentence"]


# wedge: no file is written and every candidate has all seven fields


def _template_fields() -> list[str]:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    line = next(row for row in text.splitlines() if row.startswith("**Inputs**"))
    return re.findall(r"\*\*([^*]+)\*\*", line)


@pytest.mark.parametrize("name", ["mode-wedge-candidates", "mode-wedge-clean", "mode-wedge-agent"])
def test_wedge_golden_writes_only_the_brief_and_every_candidate_has_seven_fields(name: str) -> None:
    golden = _golden(PUBLISHED, name)
    case = PUBLISHED_CASES[name]
    assert list(golden) == ["brief.md"] and not set(golden) & set(case.files)
    parts = re.split(r"^### Candidate ", golden["brief.md"], flags=re.M)[1:]
    expected = mapping(case.expected)
    assert len(parts) == expected["candidates"]
    fields = _template_fields()
    assert len(fields) == 7 and all(f"**{field}**" in part for part in parts for field in fields)
    if "phrase" in expected:
        assert str(expected["phrase"]) in golden["brief.md"]


def test_wedge_grader_checks_the_seven_fields_of_the_skill_template() -> None:
    script = _script(PUBLISHED, "wedge-candidates")
    assert all(f"'{field}'" in script for field in _template_fields())
    stops = [_script(PUBLISHED, kind) for kind in ("wedge-clean", "wedge-agent")]
    assert all("list(changed) == ['brief.md']" in text for text in [script, *stops])


# contract drafting: a draft stops the real runtime path


def _target_with_contract(tmp_path: Path, status: str) -> tuple[Path, dict[str, object]]:
    case = PUBLISHED_CASES["mode-contract"]
    target = tmp_path / "demo"
    (target / "evals").mkdir(parents=True)
    _ = (target / "SKILL.md").write_text(case.files["skills/demo/SKILL.md"], encoding="utf-8")
    draft = mapping(cast(object, json.loads(_golden(PUBLISHED, "mode-contract")["skills/demo/evals/autoimprove.json"])))
    document = draft | {"status": status}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(document), encoding="utf-8")
    return target, document


def _cases_file(tmp_path: Path) -> Path:
    cases = [{"id": split, "family": split, "split": split, "kind": "rewrite", "request": "Rewrite the note.",
              "files": {"note.txt": "hello\n"}, "expected": {"text": "hello"},
              "provenance": "test", "provider_approved": True}
             for split in ("train", "validation", "holdout", "holdout")]
    cases[3] = cases[3] | {"id": "holdout-2", "family": "holdout-2"}
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": cases}), encoding="utf-8")
    return path


def test_drafted_contract_is_rejected_with_contract_unapproved_before_any_model_call(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []

    def invoke(self: Codex, *args: object, **kwargs: object) -> dict[str, object]:
        del self, args, kwargs
        calls.append("model")
        raise AssertionError("a draft contract must stop the run before any model call")

    monkeypatch.setattr(Codex, "invoke", invoke)
    target, _ = _target_with_contract(tmp_path, "draft")
    code = main(["self-test", "--model", "controlled", "--live", "--target", str(target),
                 "--manifest", str(_cases_file(tmp_path)), "--out", str(tmp_path / "live")])
    error = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert code == 1 and error["code"] == "contract-unapproved" and calls == []
    assert not (tmp_path / "live").exists()
    with pytest.raises(Exception, match="draft") as raised:
        _ = parse(_target_with_contract(tmp_path / "again", "draft")[1], "skill")
    assert getattr(raised.value, "code") == "contract-unapproved"


def test_the_same_contract_runs_once_the_user_approves_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target, _ = _target_with_contract(tmp_path, "approved")
    code = main(["dataset", str(_cases_file(tmp_path)), "--target", str(target), "--out", str(tmp_path / "run")])
    assert code == 0 and (tmp_path / "run/run.json").is_file()
    assert capsys.readouterr().err == ""


# live profile: the existing dataset and baseline commands, never part of the gate


def test_published_manifest_prepares_a_run_against_the_skill_and_baseline_needs_live(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = tmp_path / "run"
    assert main(["dataset", str(PUBLISHED), "--target", str(SKILL_DIR), "--out", str(run)]) == 0
    assert json.loads(capsys.readouterr().out)
    assert main(["baseline", str(run), "--model", "controlled"]) == 1
    assert "live" in capsys.readouterr().err.lower()


# Mode steps that the fixtures depend on


def _flat(text: str) -> str:
    return " ".join(text.split())


def _part(path: Path, heading: str | None) -> str:
    text = path.read_text(encoding="utf-8")
    if heading is None:
        return _flat(text)
    match = re.search(rf"^## {re.escape(heading)}\n.*?(?=^## |\Z)", text, re.M | re.S)
    assert match is not None, heading
    return _flat(match[0])


SKILL = SKILL_DIR / "SKILL.md"
EXPERIMENTS = SKILL_DIR / "references/experiments.md"
ADD_NAME = ("1. Confirm the name: kebab-case, ≤64 chars, directory name equals `name:`, "
            "and no collision in `skills/`, `~/.claude/skills`, `~/.agents/skills`.")
IMPROVE_SPECULATIVE = (
    "3. Put every `<speculative>` finding and every protocol-semantic change to the user as one approval question, "
    "with your recommendation for each. Apply the approved ones and record the declined ones. "
    "A delegated run returns these findings to its parent, and the parent asks. A PR body or a report is not approval.")
WEDGE_CANDIDATES = (
    "2. List each candidate with its line. A candidate is a fixed parse, validation, count, filter, sort, or projection "
    "that every run repeats. A bundled script without an output contract is also a candidate. "
    "Classification, recommendations, and user decisions stay in prose; they are never candidates.")
STEPS: list[tuple[str, Path, str | None, str]] = [
    ("add", SKILL, "Mode: add", ADD_NAME),
    ("add", SKILL, "Mode: add", "Add `agents/openai.yaml` when the skill is user-only."),
    ("add", SKILL, "Mode: add", "Register the skill in its repo's index"),
    ("add", SKILL, "Mode: add", "The inspector reports an empty `long_sentences` list."),
    ("improve", SKILL, "Mode: improve", "Apply every `<certain>` finding of severity medium or higher"),
    ("improve", SKILL, "Mode: improve", IMPROVE_SPECULATIVE),
    ("improve", SKILL, "Mode: improve", "Tighten; do not redesign."),
    ("improve", SKILL, "Mode: improve", "Report before/after tokens and the residual findings."),
    ("wedge", SKILL, "Mode: wedge", "writes no code and starts no build"),
    ("wedge", SKILL, "Mode: wedge", WEDGE_CANDIDATES),
    ("wedge", SKILL, "Mode: wedge", "`/wedge` packages only skills, then stop."),
    ("wedge", SKILL, "Mode: wedge",
     "command name, inputs, output shape, ordering with tie-breaks, empty result, errors, and side effects"),
    ("wedge", SKILL, "Mode: wedge", "report `No offload candidates` and stop"),
    ("wedge", SKILL, "Mode: wedge", "Run /wedge with candidate <n> of this brief."),
    ("wedge", SKILL, "Mode: wedge", "every candidate cites a line and has all seven contract fields"),
    ("contract", EXPERIMENTS, "The autoimprove contract", "It stops with `contract-unapproved` when `status` is `draft`."),
    ("contract", EXPERIMENTS, "No contract", "Save every drafted contract with `\"status\": \"draft\"`, for both choices."),
    ("contract", EXPERIMENTS, "No contract", "Choose judge-only grading."),
    ("contract", EXPERIMENTS, "No contract", "The runner reports `contract-unapproved` for a draft."),
    ("self-update", SELF_SKILL, None, "Read `skills/skillz/references/harness-layout.md`, including `## Sources` and `## Rejected`."),
    ("self-update", SELF_SKILL, None, "Check every source in `## Sources` for changes since `Checked:`."),
    ("self-update", SELF_SKILL, None, "Update its matrix, rules, template, and `Checked:` date."),
    ("self-update", SELF_SKILL, None, "Record each rejected claim and its reason under `## Rejected`."),
    ("self-update", SELF_SKILL, None, "Keep the published modes and the `/skillz wedge` and `autoimprove` boundaries unchanged."),
    ("self-update", SELF_SKILL, None, "Done means: `Checked:` is today"),
]


@pytest.mark.parametrize(("mode", "path", "heading", "phrase"), STEPS, ids=[f"{m}:{p[:48]}" for m, _, _, p in STEPS])
def test_mode_steps_keep_the_invariant_that_the_fixture_depends_on(mode: str, path: Path, heading: str | None,
                                                                 phrase: str) -> None:
    assert phrase in _part(path, heading), f"{mode} step changed: {phrase}"


MUTATIONS = [
    ("add", "Mode: add", ADD_NAME, ", and no collision in `skills/`, `~/.claude/skills`, `~/.agents/skills`", ""),
    ("improve", "Mode: improve", IMPROVE_SPECULATIVE,
     "Put every `<speculative>` finding and every protocol-semantic change to the user as one approval question,"
     + " with your recommendation for each.", "Apply every `<speculative>` finding directly."),
    ("wedge", "Mode: wedge", WEDGE_CANDIDATES, "stay in prose; they are never candidates.", "stay in prose."),
]


@pytest.mark.parametrize(("mode", "heading", "step", "old", "new"), MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_a_seeded_regression_in_a_skill_copy_fails_the_pinned_step(tmp_path: Path, mode: str, heading: str, step: str,
                                                                 old: str, new: str) -> None:
    text = SKILL.read_text(encoding="utf-8")
    scratch = tmp_path / "SKILL.md"
    _ = scratch.write_text(text, encoding="utf-8")
    assert step in _part(scratch, heading), f"{mode}: the control copy must hold the step"
    assert text.count(old) == 1, f"{mode}: the mutation must hit one place"
    _ = scratch.write_text(text.replace(old, new), encoding="utf-8")
    assert step not in _part(scratch, heading), f"{mode}: the regression went unnoticed"


def test_every_fixture_kind_has_pinned_steps() -> None:
    assert {mode for mode, _, _, _ in STEPS} == {"add", "improve", "wedge", "contract", "self-update"}


# self-update: a stale Checked date, offline, on a fixture copy


def test_self_update_fixture_mirrors_the_real_reference_shape_and_starts_stale() -> None:
    case = SELF_UPDATE_CASES["self-update-stale-date"]
    real, stale = LAYOUT.read_text(encoding="utf-8"), case.files["harness-layout.md"]
    digest = mapping(cast(object, json.loads(case.files["digest.json"])))
    for text in (real, stale):
        assert re.search(r"^Checked: \d{4}-\d{2}-\d{2}\.", text, re.M) and "\n## Sources\n" in text
        assert "\n## Rejected\n" in text
    assert f"Checked: {digest['checked']}." not in stale
    assert f"Checked: {digest['checked']}." in _golden(SELF_UPDATE, "self-update-stale-date")["harness-layout.md"]


def test_grading_runs_in_a_scratch_workspace_and_a_writing_grader_cannot_touch_the_repository() -> None:
    watched = [LAYOUT, SKILL, SELF_SKILL, ROOT / "README.md"]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in watched]
    case = SELF_UPDATE_CASES["self-update-stale-date"]
    sentinel = "grader-probe-file.txt"
    writer = ("python3", "-I", "-c", f"import pathlib; pathlib.Path('{sentinel}').write_text('x')")
    sandbox = Sandbox()
    try:
        _ = _graders.command(sandbox, writer, case, {})
        assert not (ROOT / sentinel).exists() and not Path(sentinel).exists()
    finally:
        (ROOT / sentinel).unlink(missing_ok=True)
    assert sandbox.cwds and not any(cwd.is_relative_to(ROOT) for cwd in sandbox.cwds)
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in watched] == before

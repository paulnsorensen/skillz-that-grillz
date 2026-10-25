"""Offline checks of the `changelog-proof` live scenario: the fixture, the command check, the draft check, the author
drivers, the CLI check, and the pass rule.

None of them makes a model call, so the `skillz-pytest` gate step runs them.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

import changelog_proof
from changelog_check import entry_for, verdict
from changelog_proof import (CONTRACT, FIXTURE, PYPROJECT, WEDGE_TOML, check_draft, codex_argv, match_entries,
                             patch_files, proof_verdict, reference_draft)
from skillz_experiments import _graders
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, load_cases
from skillz_experiments._contract import parse
from skillz_experiments.run._outcomes import feedback_for
from skillz_experiments.search import HarnessText

HERE = Path(__file__).resolve().parent
EXAMPLE: dict[str, object] = {"ticket": "ABC-123", "type": "fix", "scope": "parser", "summary": "handle empty body.",
                              "breaking": False}


def _changes() -> list[dict[str, object]]:
    return [cast(dict[str, object], json.loads(cast(dict[str, str], case["files"])["change.json"]))
            for case in reference_draft()]


def _planted(change: dict[str, object]) -> str:
    """Return the entry that the seed `format.md` asks for: the ticket id in parentheses at the end."""
    correct = entry_for(change)
    ticket = change.get("ticket")
    return correct.replace(f"[{ticket}] ", "") + f" ({ticket})" if ticket else correct


def _grade(change: dict[str, object], entry: str | None, tmp_path: Path) -> tuple[float, str]:
    """Run the contract argv as the grader does: `change.json` at the root and the entry under `output/`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    _ = (tmp_path / "change.json").write_text(json.dumps(change), encoding="utf-8")
    if entry is not None:
        (tmp_path / "output").mkdir()
        _ = (tmp_path / "output/entry.md").write_text(entry, encoding="utf-8")
    argv = cast(list[str], cast(dict[str, dict[str, object]], CONTRACT["kinds"])["task"]["argv"])
    run = subprocess.run([sys.executable, *argv[1:]], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    return float(cast(dict[str, float], json.loads(run.stdout))["score"]), run.stderr


@pytest.mark.ac("autoimprove-live-proof:AC-1")
def test_the_fixture_has_the_gap_note_one_planted_rule_and_a_hybrid_contract_with_no_helper() -> None:
    guide = (FIXTURE / "SKILL.md.fixture").read_text(encoding="utf-8")
    assert "No helper writes the entry yet" in guide and "references/format.md" in guide
    rules = (FIXTURE / "references/format.md.fixture").read_text(encoding="utf-8")
    assert re.findall(r"^(\d)\. \*\*", rules, re.MULTILINE) == ["1", "2", "3", "4", "5", "6"]
    contract = parse(CONTRACT, "test")
    assert contract.grader("task").type == "hybrid" and contract.helper is None
    assert 'include = ["lib/fromargs"]' in WEDGE_TOML and "cyclopts" in PYPROJECT


@pytest.mark.ac("autoimprove-live-proof:AC-1")
def test_the_format_example_breaks_only_the_ticket_rule(tmp_path: Path) -> None:
    rules = (FIXTURE / "references/format.md.fixture").read_text(encoding="utf-8")
    example = re.search(r"```json\n(.*?)\n```", rules, re.DOTALL)
    entry = re.search(r"```text\n(.*?)\n```", rules, re.DOTALL)
    assert example and entry and json.loads(example[1]) == EXAMPLE
    message = verdict(EXAMPLE, entry[1] + "\n")
    assert message is not None and "rule 1 (ticket id)" in message, message
    # Moving the ticket id to the front, as the corrected rule 1 says, gives the oracle entry: no other rule differs.
    moved = "- [ABC-123] " + entry[1].removeprefix("- ").removesuffix(" (ABC-123)")
    assert moved == entry_for(EXAMPLE) and _grade(EXAMPLE, moved + "\n", tmp_path)[0] == 1


@pytest.mark.ac("autoimprove-live-proof:AC-2")
@pytest.mark.ac("autoimprove-live-proof:AC-4")
def test_an_entry_that_follows_the_planted_rule_scores_0_with_one_message_that_names_the_rule_and_the_line(
        tmp_path: Path) -> None:
    score, message = _grade(EXAMPLE, _planted(EXAMPLE) + "\n", tmp_path)
    assert score == 0 and message.count("\n") == 1, message
    assert message.startswith("entry.md:1: rule 1 (ticket id):"), message


@pytest.mark.ac("autoimprove-live-proof:AC-3")
def test_every_oracle_entry_of_the_reference_draft_scores_1(tmp_path: Path) -> None:
    for index, change in enumerate(_changes()):
        score, message = _grade(change, entry_for(change) + "\n", tmp_path / str(index))
        assert score == 1 and not message, (change, message)


BROKEN: dict[str, tuple[Callable[[dict[str, object]], str], int]] = {
    "planted": (_planted, 1),
    "no-ticket": (lambda change: entry_for(change).replace(f"[{change['ticket']}] ", ""), 1),
    "label": (lambda change: entry_for(change).replace("] ", "] Updated ", 1), 2),
    "scope": (lambda change: entry_for(change | {"scope": None}), 3),
    "summary": (lambda change: entry_for(change).replace(": ", ": the ", 1), 4),
    "breaking": (lambda change: entry_for(change | {"breaking": not change["breaking"]}), 5),
    "two-lines": (lambda change: entry_for(change) + "\n" + entry_for(change), 6),
}
"""Each entry breaks one rule of a ticketed, scoped change, and the number is the rule that the check names."""


@pytest.mark.ac("autoimprove-live-proof:AC-4")
@pytest.mark.ac("autoimprove-live-proof:AC-2")
@pytest.mark.parametrize("name", sorted(BROKEN))
def test_an_entry_that_breaks_one_rule_scores_0_and_names_that_rule(name: str, tmp_path: Path) -> None:
    make, number = BROKEN[name]
    change = EXAMPLE | {"breaking": True}
    score, message = _grade(change, make(change) + "\n", tmp_path)
    assert score == 0 and f"rule {number} (" in message and message.startswith("entry.md:"), message


@pytest.mark.ac("autoimprove-live-proof:AC-4")
def test_a_missing_entry_and_an_entry_with_a_ticket_for_a_change_without_one_score_0(tmp_path: Path) -> None:
    assert _grade(EXAMPLE, None, tmp_path / "missing")[0] == 0
    change = EXAMPLE | {"ticket": None}
    score, message = _grade(change, "- [ABC-123] " + entry_for(change).removeprefix("- ") + "\n", tmp_path / "extra")
    assert score == 0 and "rule 1 (ticket id)" in message, message


@pytest.mark.ac("autoimprove-live-proof:AC-5")
def test_no_check_message_repeats_20_characters_of_the_expected_entry() -> None:
    messages = 0
    for change in [EXAMPLE, *_changes()]:
        expected = entry_for(change)
        windows = {expected[start:start + 20] for start in range(len(expected) - 19)}
        for make, _number in BROKEN.values():
            message = verdict(change, make(change) + "\n")
            if message is None:
                continue
            messages += 1
            assert not [window for window in windows if window in message], (expected, message)
    assert messages > 100


class _Sandboxed:
    """A task transport that runs the grader argv in the workspace, as the harness sandbox does."""

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str, str]:
        run = subprocess.run([sys.executable, *argv[1:]], cwd=workspace, capture_output=True, text=True, timeout=20)
        return run.returncode, run.stdout, run.stderr


def _case(tmp_path: Path) -> Case:
    item = reference_draft()[0] | {"split": "train", "provenance": "test", "provider_approved": True,
                                   "files": {"change.json": json.dumps(EXAMPLE)}}
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": [item]}), encoding="utf-8")
    return load_cases(path, parse(CONTRACT, "test").grader_types())[0]


@pytest.mark.ac("autoimprove-live-proof:AC-6")
def test_the_proposer_feedback_of_a_planted_rule_failure_carries_the_check_message(tmp_path: Path) -> None:
    case = _case(tmp_path)
    argv = tuple(cast(list[str], cast(dict[str, dict[str, object]], CONTRACT["kinds"])["task"]["argv"]))
    score, diagnostic = _graders.command(_Sandboxed(), argv, case, {"entry.md": _planted(EXAMPLE) + "\n"})
    assert score == 0
    result: dict[str, object] = {"score": 0.0, "scores": {"command": 0.0, "judge": None}, "gate_diagnostic": diagnostic}
    _, feedback = feedback_for(result, case, HarnessText(), Candidate({"SKILL.md": "x"}, ("SKILL.md",)))
    assert "entry.md:1: rule 1 (ticket id)" in cast(str, feedback["gate_feedback"]), feedback


@pytest.mark.ac("autoimprove-live-proof:AC-7")
def test_the_reference_draft_passes_the_draft_check_with_six_families_of_three() -> None:
    draft = reference_draft()
    assert check_draft(draft) == []
    assert len(draft) == 18 and len({case["family"] for case in draft}) == 6
    assert len({cast(dict[str, object], case["expected"])["entry"] for case in draft}) == 18
    assert {change["type"] for change in _changes()} == {"feat", "fix", "perf", "docs", "refactor"}


@pytest.mark.ac("autoimprove-live-proof:AC-7")
@pytest.mark.parametrize(("change", "problem"), [
    ({"request": "Write the entry with the ticket id first."}, "the request can state the enforced rule"),
    ({"request": "Write the entry for WEB-389."}, "the request can state the enforced rule"),
    ({"expected": {"entry": "- Fixed: x", "criteria": [{"id": "a", "text": "t"}]}}, "the oracle says"),
    ({"expected": {"entry": "- [WEB-389] Fixed in api: Explain retry flags"}}, "the scored case has no criteria"),
    ({"files": {"change.json": '{"ticket": "x"}'}}, "ticket must be null"),
    ({"files": {}}, "files lack change.json"),
])
def test_the_draft_check_refuses_a_malformed_task_case(change: dict[str, object], problem: str) -> None:
    draft = reference_draft()
    draft[0] |= change
    problems = check_draft(draft)
    assert len(problems) == 1 and problems[0].startswith("fixes-0: ") and problem in problems[0], problems


@pytest.mark.ac("autoimprove-live-proof:AC-7")
def test_the_draft_check_refuses_too_few_full_families_and_a_family_with_one_ticket() -> None:
    draft = [case for case in reference_draft() if case["family"] != "fixes"]
    assert check_draft(draft) == ["5 families hold 3 task cases; the scenario needs 6"]
    draft = reference_draft()
    for case in draft[:2]:
        change = cast(dict[str, object], json.loads(cast(dict[str, str], case["files"])["change.json"])) | {"ticket": None}
        entry = entry_for(change)
        case["files"] = {"change.json": json.dumps(change)}
        case["expected"] = {"entry": entry, "criteria": [{"id": "entry", "text": entry}]}
    assert check_draft(draft) == ["family fixes has 1 cases with a ticket; the scenario needs 2"]
    assert check_draft({"cases": []}) == ["the draft is not a list of case objects"]


FAKE_AGENT = """#!{python}
import json, os, pathlib, sys
prompt = sys.stdin.read()
argv = sys.argv[1:]
out = pathlib.Path(argv[-1] if argv[0] == "-p" else argv[argv.index("--add-dir") + 1])
log = {{"argv": argv, "prompt": prompt, "home": os.environ.get("HOME"), "codex_home": os.environ.get("CODEX_HOME"),
       "skills": sorted(str(path) for path in pathlib.Path.cwd().glob("*/skills/*"))}}
(out / "agent.json").write_text(json.dumps(log))
(out / "cases.draft.json").write_text(pathlib.Path({draft!r}).read_text())
if argv[0] == "-p":
    print(json.dumps({{"num_turns": 7, "total_cost_usd": 0.5, "result": "done"}}))
else:
    print(json.dumps({{"type": "item.completed", "item": {{"type": "agent_message", "text": "done"}}}}))
    print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 9}}}}))
"""


def _target(tmp_path: Path) -> Path:
    target = tmp_path / "repo/changelog-entry"
    target.mkdir(parents=True)
    _ = (target / "SKILL.md").write_text("x\n", encoding="utf-8")
    git = ["git", "-C", str(target), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    for command in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "fixture"]):
        _ = subprocess.run([*git, *command], check=True, capture_output=True)
    return target


@pytest.mark.ac("autoimprove-live-proof:AC-8")
@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_each_harness_runs_the_journey_in_its_own_agent_with_the_skillz_skill_and_the_same_prompt(
        harness: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    draft = tmp_path / "draft.json"
    _ = draft.write_text(json.dumps(reference_draft()), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex"):
        _ = (bin_dir / name).write_text(FAKE_AGENT.format(python=sys.executable, draft=str(draft)), encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    host = tmp_path / "host-codex"
    host.mkdir()
    _ = (host / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(host))
    work = tmp_path / "work"
    work.mkdir()
    authored, fields = changelog_proof.author(_target(tmp_path), work, harness, "task-model")
    assert authored == reference_draft(), fields
    assert fields["author_harness"] == harness and fields["author_exit"] == 0 and fields["draft_cases"] == 18, fields
    assert fields["author_turns"] == (7 if harness == "claude" else None), fields
    log = cast(dict[str, str], json.loads((work / "author-run/agent.json").read_text(encoding="utf-8")))
    sigil = "/" if harness == "claude" else "$"
    assert log["prompt"].startswith(f"{sigil}skillz autoimprove "), log["prompt"]
    assert f"Use the {harness} harness. The task model is task-model." in log["prompt"]
    assert "Our changelog entries start with the ticket id in square brackets" in log["prompt"]
    skills_dir = ".claude" if harness == "claude" else ".agents"
    assert log["skills"] == [str(work / "author" / skills_dir / "skills/skillz")], log
    if harness == "codex":
        assert log["home"] == str(work / "author-home") and log["codex_home"] == str(work / "author-home/.codex")
        assert (work / "author-home/.codex/auth.json").resolve() == host / "auth.json"
        assert sorted(path.name for path in (work / "author-home/.codex").iterdir()) == ["auth.json"]


@pytest.mark.ac("autoimprove-live-proof:AC-8")
def test_a_wrong_codex_draft_stops_the_author_stage_with_the_shared_draft_check(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    draft = tmp_path / "draft.json"
    _ = draft.write_text(json.dumps(reference_draft()[:3]), encoding="utf-8")
    (tmp_path / "bin").mkdir()
    _ = (tmp_path / "bin/codex").write_text(FAKE_AGENT.format(python=sys.executable, draft=str(draft)), encoding="utf-8")
    (tmp_path / "bin/codex").chmod(0o755)
    (tmp_path / "host").mkdir()
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "host"))
    (tmp_path / "work").mkdir()
    authored, fields = changelog_proof.author(_target(tmp_path), tmp_path / "work", "codex", "m")
    assert authored is None and fields["code"] == "draft-wrong" and "families hold 3" in str(fields["error"]), fields


@pytest.mark.ac("autoimprove-live-proof:AC-8")
def test_the_codex_author_session_may_write_only_the_run_directory_and_its_home() -> None:
    argv = codex_argv(Path("/d"), Path("/o"), Path("/h"), "gpt-6-astra")
    assert argv[:2] == ["codex", "exec"] and argv[-1] == "-"
    for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--json", "--skip-git-repo-check"):
        assert flag in argv
    assert argv[argv.index("--sandbox") + 1] == "workspace-write" and argv[argv.index("-C") + 1] == "/d"
    assert [argv[index + 1] for index, part in enumerate(argv) if part == "--add-dir"] == ["/o", "/h"]
    assert argv[argv.index("--model") + 1] == "gpt-6-astra"


def _archive(tmp_path: Path, body: str) -> Path:
    """Write a stand-in for the built CLI. Python runs a plain script that has a `.pyz` name."""
    archive = tmp_path / "build/changelog-format.pyz"
    archive.parent.mkdir(parents=True)
    _ = archive.write_text(f"import json, sys\nfrom pathlib import Path\nsys.path.insert(0, {str(HERE)!r})\n"
                           + f"from changelog_check import entry_for\n{body}", encoding="utf-8")
    return archive


GUIDE = "Run `python3 .agents/skills/changelog-entry/scripts/changelog-format.pyz change.json`.\n"
WRITES = "Path('entry.md').write_text(entry_for(json.loads(Path(sys.argv[1]).read_text())) + '\\n')\n"
PRINTS = "print(json.dumps({'entry': entry_for(json.loads(Path(sys.argv[1]).read_text()))}))\n"
PLANTED = ("change = json.loads(Path(sys.argv[1]).read_text())\n"
           "entry = entry_for(change)\n"
           "if change['ticket']:\n"
           "    entry = entry.replace('[' + change['ticket'] + '] ', '') + ' (' + change['ticket'] + ')'\n"
           "print(entry)\n")


@pytest.mark.ac("autoimprove-live-proof:AC-13")
@pytest.mark.parametrize("body", [WRITES, PRINTS], ids=["writes-entry-md", "prints-json"])
def test_the_cli_check_passes_a_cli_that_keeps_every_rule_on_every_case(body: str, tmp_path: Path) -> None:
    fields = match_entries(GUIDE, _archive(tmp_path, body), "changelog-format", reference_draft(), tmp_path / "cases")
    assert fields["cli"] == "ok" and fields["cli_cases"] == 18 and fields["cli_mismatches"] == [], fields


@pytest.mark.ac("autoimprove-live-proof:AC-13")
def test_the_cli_check_names_each_case_that_breaks_a_rule(tmp_path: Path) -> None:
    fields = match_entries(GUIDE, _archive(tmp_path, PLANTED), "changelog-format", reference_draft(), tmp_path / "cases")
    assert fields["cli"] == "mismatch" and "fixes-0" in cast(list[str], fields["cli_mismatches"]), fields
    assert "no-ticket-0" not in cast(list[str], fields["cli_mismatches"]), fields
    assert "rule 1 (ticket id)" in cast(list[str], fields["cli_faults"])[0], fields
    draft = reference_draft()
    ticket = _changes()[4]["ticket"]
    one = _archive(tmp_path / "one", "change = json.loads(Path(sys.argv[1]).read_text())\n"
                   + f"print(entry_for(change) + ('.' if change['ticket'] == {ticket!r} else ''))\n")
    wrong = [str(draft[4]["id"])]
    fields = match_entries(GUIDE, one, "changelog-format", draft, tmp_path / "one-cases")
    assert wrong and fields["cli"] == "mismatch" and fields["cli_mismatches"] == sorted(wrong), fields


@pytest.mark.ac("autoimprove-live-proof:AC-13")
def test_the_cli_check_reports_no_invocation_when_no_guide_command_gives_an_entry(tmp_path: Path) -> None:
    failing = _archive(tmp_path, "sys.exit(2)\n")
    assert match_entries(GUIDE, failing, "changelog-format", reference_draft(), tmp_path / "a")["cli"] == "no-invocation"
    placeholder = "Run `python3 scripts/changelog-format.pyz <file>`.\n"
    good = _archive(tmp_path / "good", WRITES)
    assert match_entries(placeholder, good, "changelog-format", reference_draft(), tmp_path / "b")["cli"] == "no-invocation"


def test_patch_files_lists_each_changed_path(tmp_path: Path) -> None:
    patch = tmp_path / "candidate.patch"
    _ = patch.write_text("diff --git a/references/format.md b/references/format.md\n--- a\n+++ b\n"
                         + "diff --git a/src/changelog_format/__init__.py b/src/changelog_format/__init__.py\n", encoding="utf-8")
    assert patch_files(patch) == ["references/format.md", "src/changelog_format/__init__.py"]
    assert patch_files(tmp_path / "absent.patch") == []


CLAIMS: dict[str, object] = {
    "runner": {"status": "supported", "checks": {"preflight": "pass"}},
    "export": {"status": "supported", "reasons": []},
    "improvement": {"status": "supported", "verdict": "promote", "reasons": []},
    "apply": True}


def _complete(**fields: object) -> dict[str, object]:
    return {"stage": "complete", "phase": "complete", "code": None, "verdict": "promote", "delta": 0.6, "se": 0.1,
            "holdout_scored": 6, "patch_files": ["SKILL.md", "src/changelog_format/__init__.py"],
            "format_md_changed": False, "claims": CLAIMS, "cli": "ok", "cli_target": "changelog-format",
            "cli_cases": 18} | fields


@pytest.mark.ac("autoimprove-live-proof:AC-10")
def test_a_record_with_claims_apply_and_cli_ok_passes_and_names_the_evidence() -> None:
    passed, reason = proof_verdict(_complete())
    assert passed, reason
    for part in ("verdict promote", "delta 0.6", "SE 0.1", "6 holdout cases", "src/changelog_format/__init__.py",
                 "CLI changelog-format ok on 18 cases", "format.md changed: False"):
        assert part in reason, reason


@pytest.mark.ac("autoimprove-live-proof:AC-9")
def test_a_promote_cheaper_record_fails_and_names_each_claim_with_its_reasons() -> None:
    claims = CLAIMS | {"apply": False, "improvement": {"status": "inconclusive", "reasons": ["cheaper-not-better"]},
                       "export": {"status": "inconclusive", "reasons": ["replay-missing"]}}
    passed, reason = proof_verdict(_complete(verdict="promote-cheaper", claims=claims))
    assert not passed and "claims.apply is False" in reason, reason
    assert "improvement inconclusive (cheaper-not-better)" in reason and "export inconclusive (replay-missing)" in reason
    assert "runner supported" in reason and "cli=ok" in reason, reason


@pytest.mark.ac("autoimprove-live-proof:AC-9")
@pytest.mark.parametrize("cli", ["missing", "mismatch", "no-invocation", "build-failed", None])
def test_a_supported_promotion_without_a_correct_cli_fails_and_names_the_cli_status(cli: str | None) -> None:
    passed, reason = proof_verdict(_complete(cli=cli))
    assert not passed and f"cli={cli}" in reason and "improvement supported" in reason, reason


@pytest.mark.ac("autoimprove-live-proof:AC-9")
@pytest.mark.parametrize(("stage", "code"), [("author", "draft-wrong"), ("run", "cases-too-easy"),
                                             ("export", "export-failed")])
def test_a_stopped_run_fails_and_names_the_stage_the_code_and_the_claims(stage: str, code: str) -> None:
    passed, reason = proof_verdict({"stage": stage, "code": code})
    assert not passed and f"stage {stage}" in reason and f"code {code}" in reason, reason
    assert "runner missing" in reason and "cli=None" in reason, reason


@pytest.mark.ac("autoimprove-live-proof:AC-9")
def test_a_failed_runner_check_is_named_as_a_reason() -> None:
    claims = CLAIMS | {"apply": False, "runner": {"status": "refuted", "checks": {"holdout-coverage": "fail",
                                                                                "preflight": "pass"}}}
    passed, reason = proof_verdict(_complete(claims=claims))
    assert not passed and "runner refuted (holdout-coverage=fail)" in reason, reason

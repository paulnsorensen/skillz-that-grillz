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
from changelog_check import RULES, entries_for, entry_for, verdict
from changelog_proof import (CONTRACT, FIXTURE, NAME, PYPROJECT, TOKEN_RULE, WEDGE_TOML, check, check_draft,
                             codex_argv, match_entries, patch_files, proof_verdict, reference_draft)
from skillz_experiments import _graders
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, load_cases
from skillz_experiments._contract import parse
from skillz_experiments.gate import DEFAULT_STATISTICS, Statistics, verdict as gate_verdict
from skillz_experiments.run._outcomes import feedback_for
from skillz_experiments.run._patch import _patch_lines, _patch_text  # pyright: ignore[reportPrivateUsage]
from skillz_experiments.search import HarnessText

HERE = Path(__file__).resolve().parent
Changes = list[dict[str, object]]
EXAMPLE: Changes = [
    {"ticket": "ABC-123", "type": "fix", "scope": "parser", "summary": "handle empty body.", "breaking": False},
    {"ticket": None, "type": "feat", "scope": None, "summary": "add export command", "breaking": True}]


def _cases() -> list[Changes]:
    return [cast(Changes, json.loads(cast(dict[str, str], case["files"])["changes.json"])) for case in reference_draft()]


def _planted_line(change: dict[str, object]) -> str:
    """Return the entry that the seed `format.md` asks for: the ticket id in parentheses at the end."""
    correct = entry_for(change)
    ticket = change.get("ticket")
    return correct.replace(f"[{ticket}] ", "") + f" ({ticket})" if ticket else correct


def _planted(changes: Changes) -> str:
    return "".join(_planted_line(change) + "\n" for change in changes)


def _grade(changes: Changes, text: str | None, tmp_path: Path) -> tuple[float, str]:
    """Run the contract argv as the grader does: `changes.json` at the root and the entries under `output/`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    _ = (tmp_path / "changes.json").write_text(json.dumps(changes), encoding="utf-8")
    if text is not None:
        (tmp_path / "output").mkdir()
        _ = (tmp_path / "output/entries.md").write_text(text, encoding="utf-8")
    argv = cast(list[str], cast(dict[str, dict[str, object]], CONTRACT["kinds"])["task"]["argv"])
    run = subprocess.run([sys.executable, *argv[1:]], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    return float(cast(dict[str, float], json.loads(run.stdout))["score"]), run.stderr


def _lines(changes: Changes) -> list[str]:
    return [entry_for(change) for change in changes]


def _site(changes: Changes) -> int:
    """Return the index of the first change that has a ticket and a scope."""
    return next(index for index, change in enumerate(changes) if change["ticket"] and change["scope"])


def _text(lines: list[str]) -> str:
    return "".join(line + "\n" for line in lines)


@pytest.mark.ac("autoimprove-live-proof:AC-1")
def test_the_fixture_has_the_gap_note_one_planted_rule_and_a_command_contract_with_no_helper() -> None:
    guide = (FIXTURE / "SKILL.md.fixture").read_text(encoding="utf-8")
    assert "No helper writes the entries yet" in guide and "references/format.md" in guide
    assert "changes.json" in guide and "entries.md" in guide and '{"count": N}' in guide
    # SKILL.md must not name the seed CLI, so the search cannot edit it in place of adding `@new-cli`.
    assert "change-check" not in guide
    rules = (FIXTURE / "references/format.md.fixture").read_text(encoding="utf-8")
    headings = cast(list[str], re.findall(r"^\d\. \*\*(.+?)\.\*\*", rules, re.MULTILINE))
    assert [heading.lower() for heading in headings] == list(RULES.values()), headings
    contract = parse(CONTRACT, "test")
    assert contract.grader("task").type == "command" and contract.helper is None
    assert 'include = ["lib/fromargs"]' in WEDGE_TOML and "cyclopts" in PYPROJECT


@pytest.mark.ac("autoimprove-live-proof:AC-1")
def test_the_format_example_breaks_only_the_ticket_rule(tmp_path: Path) -> None:
    rules = (FIXTURE / "references/format.md.fixture").read_text(encoding="utf-8")
    example = re.search(r"```json\n(.*?)\n```", rules, re.DOTALL)
    output = re.search(r"```text\n(.*?)\n```", rules, re.DOTALL)
    assert example and output and json.loads(example[1]) == EXAMPLE
    message = verdict(EXAMPLE, output[1] + "\n")
    assert message is not None and message.startswith("entries.md:1: rule 1 (ticket id)"), message
    # Moving the ticket id to the front, as the corrected rule 1 says, gives the oracle text: no other rule differs.
    moved = ["- [ABC-123] " + output[1].splitlines()[0].removeprefix("- ").removesuffix(" (ABC-123)"),
             output[1].splitlines()[1]]
    assert _text(moved) == entries_for(EXAMPLE) and _grade(EXAMPLE, _text(moved), tmp_path)[0] == 1


@pytest.mark.ac("autoimprove-live-proof:AC-2")
@pytest.mark.ac("autoimprove-live-proof:AC-4")
def test_a_file_that_follows_the_planted_rule_scores_0_with_one_message_that_names_the_rule_and_the_line(
        tmp_path: Path) -> None:
    changes = _cases()[0]
    first = next(index for index, change in enumerate(changes) if change["ticket"])
    score, message = _grade(changes, _planted(changes), tmp_path)
    assert score == 0 and message.count("\n") == 1, message
    assert message.startswith(f"entries.md:{first + 1}: rule 1 (ticket id):"), message
    assert cast(str, verdict(EXAMPLE, "\n")).startswith("entries.md:1: rule 6")
    assert cast(str, verdict(EXAMPLE, entries_for(EXAMPLE) + "extra\n")).startswith("entries.md:3: rule 6")


TRICKY: Changes = [
    {"ticket": None, "type": "feat", "scope": None, "summary": "add HTTP-2 support", "breaking": False},
    {"ticket": None, "type": "fix", "scope": "io", "summary": "handle UTF-8 names", "breaking": False},
    {"ticket": "ABC-1", "type": "docs", "scope": None, "summary": "document BREAKING changes", "breaking": False},
    {"ticket": "ABC-2", "type": "feat", "scope": None, "summary": "remove v1 **BREAKING**", "breaking": True}]
"""Valid changes whose summaries hold an id-like word or the breaking marker."""


@pytest.mark.ac("autoimprove-live-proof:AC-3")
def test_the_oracle_text_of_every_reference_case_and_of_tricky_summaries_scores_1(tmp_path: Path) -> None:
    for index, changes in enumerate([*_cases(), TRICKY, EXAMPLE]):
        score, message = _grade(changes, entries_for(changes), tmp_path / str(index))
        assert score == 1 and not message, (index, message)


BROKEN: dict[str, tuple[Callable[[dict[str, object]], str], int]] = {
    "planted": (_planted_line, 1),
    "no-ticket": (lambda change: entry_for(change).replace(f"[{change['ticket']}] ", ""), 1),
    "label": (lambda change: entry_for(change).replace("] ", "] Updated ", 1), 2),
    "scope": (lambda change: entry_for(change | {"scope": None}), 3),
    "summary": (lambda change: entry_for(change).replace(": ", ": the ", 1), 4),
    "breaking": (lambda change: entry_for(change | {"breaking": not change["breaking"]}), 5),
}
"""Each entry breaks one rule of a ticketed, scoped change, and the number is the rule that the check names."""


@pytest.mark.ac("autoimprove-live-proof:AC-4")
@pytest.mark.ac("autoimprove-live-proof:AC-2")
@pytest.mark.parametrize("name", sorted(BROKEN))
def test_one_broken_line_of_a_release_scores_0_and_the_message_names_that_rule_and_line(
        name: str, tmp_path: Path) -> None:
    make, number = BROKEN[name]
    changes = _cases()[3]
    site = _site(changes)
    lines = _lines(changes)
    lines[site] = make(changes[site])
    score, message = _grade(changes, _text(lines), tmp_path)
    assert score == 0 and message.startswith(f"entries.md:{site + 1}: rule {number} ("), message


@pytest.mark.ac("autoimprove-live-proof:AC-4")
def test_a_release_file_with_a_wrong_shape_scores_0_and_names_rule_6_and_the_first_wrong_line(
        tmp_path: Path) -> None:
    changes = _cases()[1]
    lines = _lines(changes)
    count = len(lines)
    shapes = {"missing": (None, 1), "empty": ("", 1), "blank": (_text([*lines[:4], "", *lines[4:]]), 5),
              "short": (_text(lines[:-3]), count - 2), "long": (_text([*lines, "- x"]), count + 1),
              "no-newline": ("\n".join(lines), count)}
    for name, (text, line) in shapes.items():
        score, message = _grade(changes, text, tmp_path / name)
        assert score == 0 and message.startswith(f"entries.md:{line}: rule 6 (one line per change)"), (name, message)


@pytest.mark.ac("autoimprove-live-proof:AC-4")
def test_an_entry_with_a_ticket_for_a_change_without_one_scores_0(tmp_path: Path) -> None:
    changes = [change | {"ticket": None} if index == 1 else change for index, change in enumerate(EXAMPLE)]
    lines = _lines(changes)
    lines[1] = "- [ABC-123] " + lines[1].removeprefix("- ")
    score, message = _grade(changes, _text(lines), tmp_path)
    assert score == 0 and message.startswith("entries.md:2: rule 1 (ticket id)"), message


@pytest.mark.ac("autoimprove-live-proof:AC-5")
def test_no_check_message_repeats_20_characters_of_an_expected_entry() -> None:
    messages = 0
    for changes in [EXAMPLE, TRICKY, *_cases()]:
        windows = {line[start:start + 20] for line in _lines(changes) for start in range(len(line) - 19)}
        for make, _number in BROKEN.values():
            for site in range(len(changes)):
                lines = _lines(changes)
                lines[site] = make(changes[site])
                message = verdict(changes, _text(lines))
                if message is None:
                    continue
                messages += 1
                assert not [window for window in windows if window in message], (site, message)
    assert messages > 100


class _Sandboxed:
    """A task transport that runs the grader argv in the workspace, as the harness sandbox does."""

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str, str]:
        run = subprocess.run([sys.executable, *argv[1:]], cwd=workspace, capture_output=True, text=True, timeout=20)
        return run.returncode, run.stdout, run.stderr


def _case(tmp_path: Path) -> Case:
    item = reference_draft()[0] | {"split": "train", "provenance": "test", "provider_approved": True,
                                   "files": {"changes.json": json.dumps(EXAMPLE)}, "expected": {"count": 2}}
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": [item]}), encoding="utf-8")
    return load_cases(path, parse(CONTRACT, "test").grader_types())[0]


@pytest.mark.ac("autoimprove-live-proof:AC-6")
def test_the_proposer_feedback_of_a_planted_rule_failure_carries_the_check_message(tmp_path: Path) -> None:
    case = _case(tmp_path)
    argv = tuple(cast(list[str], cast(dict[str, dict[str, object]], CONTRACT["kinds"])["task"]["argv"]))
    score, diagnostic = _graders.command(_Sandboxed(), argv, case, {"entries.md": _planted(EXAMPLE)})
    assert score == 0
    result: dict[str, object] = {"score": 0.0, "scores": {"command": 0.0, "judge": None}, "gate_diagnostic": diagnostic}
    harness = HarnessText.build([], phrases=[f"${NAME}", "output/entries.md"])
    _, feedback = feedback_for(result, case, harness, Candidate({"SKILL.md": "x"}, ("SKILL.md",)))
    assert "entries.md:1: rule 1 (ticket id)" in cast(str, feedback["gate_feedback"]), feedback


@pytest.mark.ac("autoimprove-live-proof:AC-7")
def test_the_reference_draft_passes_the_draft_check_with_six_families_of_three_releases() -> None:
    draft = reference_draft()
    assert check_draft(draft) == []
    assert len(draft) == 18 and len({case["family"] for case in draft}) == 6
    cases = _cases()
    assert all(30 <= len(changes) <= 40 for changes in cases)
    assert all(sum(1 for change in changes if change["ticket"]) >= 5 for changes in cases)
    assert [cast(dict[str, object], case["expected"]) for case in draft] == [{"count": len(changes)} for changes in cases]
    assert {change["type"] for changes in cases for change in changes} == {"feat", "fix", "perf", "docs", "refactor"}
    assert any(str(change["summary"]).endswith(".") for changes in cases for change in changes)
    assert reference_draft() == draft


def _with(case: dict[str, object], changes: Changes, **fields: object) -> None:
    case |= {"files": {"changes.json": json.dumps(changes)}, "expected": {"count": len(changes)}} | fields


def _first_ticket(case: dict[str, object]) -> str:
    changes = cast(Changes, json.loads(cast(dict[str, str], case["files"])["changes.json"]))
    return next(str(change["ticket"]) for change in changes if change["ticket"])


def _few_tickets(changes: Changes) -> Changes:
    return [change | {"ticket": None} if index >= 4 else change for index, change in enumerate(changes)]


Mutate = Callable[[dict[str, object]], object]
MALFORMED: list[tuple[Mutate, str]] = [
    (lambda case: case.update(request="Write the entries with the ticket id first."), "the request can state"),
    (lambda case: case.update(request=f"Write the entries for {_first_ticket(case)}."), "the request can state"),
    (lambda case: case.update(expected={"count": 3}), "expected count 3, the oracle says"),
    (lambda case: case.update(expected={}), "expected count null, the oracle says"),
    (lambda case: case.update(files={"changes.json": '[{"ticket": "x"}]'}), "change 0: ticket must be null"),
    (lambda case: case.update(files={"changes.json": "{}"}), "changes.json is not a nonempty JSON list"),
    (lambda case: case.update(files={}), "files lack changes.json"),
    (lambda case: _with(case, _cases()[0][:29]), "holds 29 changes"),
    (lambda case: _with(case, [*_cases()[0], *_cases()[1]][:41]), "holds 41 changes"),
    (lambda case: _with(case, _few_tickets(_cases()[0])), "holds 4 changes with a ticket"),
]


@pytest.mark.ac("autoimprove-live-proof:AC-7")
@pytest.mark.parametrize(("mutate", "problem"), MALFORMED)
def test_the_draft_check_refuses_a_malformed_task_case(mutate: Mutate, problem: str) -> None:
    draft = reference_draft()
    _ = mutate(draft[0])
    problems = check_draft(draft)
    assert len(problems) == 1 and problems[0].startswith("patch-release-0: ") and problem in problems[0], problems


@pytest.mark.ac("autoimprove-live-proof:AC-7")
def test_the_draft_check_refuses_too_few_full_families() -> None:
    draft = [case for case in reference_draft() if case["family"] != "patch-release"]
    assert check_draft(draft) == ["5 families hold 3 task cases; the scenario needs 6"]
    assert check_draft({"cases": []}) == ["the draft is not a list of case objects"]


@pytest.mark.ac("autoimprove-live-proof:AC-7")
def test_the_draft_check_accepts_ordinary_request_words_and_tricky_summaries() -> None:
    draft = reference_draft()
    draft[0]["request"] = "Read changes.json first, then restart the log and begin its changelog entries."
    _with(draft[15], TRICKY * 9)
    assert check_draft(draft) == []


FAKE_AGENT = """#!{python}
import json, os, pathlib, sys
prompt = sys.stdin.read()
argv = sys.argv[1:]
out = pathlib.Path(argv[-1] if argv[0] == "-p" else argv[argv.index("--add-dir") + 1])
log = {{"argv": argv, "prompt": prompt, "home": os.environ.get("HOME"), "codex_home": os.environ.get("CODEX_HOME"),
       "env": sorted(os.environ), "skills": sorted(str(path) for path in pathlib.Path.cwd().glob("*/skills/*"))}}
(out / "agent.json").write_text(json.dumps(log))
(out / "cases.draft.json").write_text(pathlib.Path({draft!r}).read_text())
if argv[0] == "-p":
    print(json.dumps({{"num_turns": 7, "total_cost_usd": 0.5, "result": "done"}}))
else:
    print(json.dumps({{"type": "item.completed", "item": {{"type": "agent_message", "text": "done"}}}}))
    print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 9}}}}))
"""


def _fake_agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, draft: list[dict[str, object]]) -> Path:
    """Put fake `claude` and `codex` agents on PATH that write `draft`. Return the host Codex home."""
    path = tmp_path / "draft.json"
    _ = path.write_text(json.dumps(draft), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex"):
        _ = (bin_dir / name).write_text(FAKE_AGENT.format(python=sys.executable, draft=str(path)), encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    host = tmp_path / "host-codex"
    host.mkdir()
    _ = (host / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(host))
    (tmp_path / "work").mkdir()
    return host


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
def test_each_harness_runs_the_author_stage_in_its_own_agent_with_the_skillz_skill_and_the_same_prompt(
        harness: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = _fake_agents(tmp_path, monkeypatch, reference_draft())
    work = tmp_path / "work"
    authored, fields = changelog_proof.author(_target(tmp_path), work, harness, "task-model")
    assert authored == reference_draft(), fields
    assert fields["author_harness"] == harness and fields["author_exit"] == 0 and fields["draft_cases"] == 18, fields
    assert fields["author_turns"] == (7 if harness == "claude" else None), fields
    log = cast(dict[str, object], json.loads((work / "author-run/agent.json").read_text(encoding="utf-8")))
    prompt = cast(str, log["prompt"])
    sigil = "/" if harness == "claude" else "$"
    assert prompt.startswith(f"{sigil}skillz autoimprove "), prompt
    assert f"Use the {harness} harness. The task model is task-model." in prompt
    assert "Our changelog entries start with the ticket id in square brackets" in prompt
    skills_dir = ".claude" if harness == "claude" else ".agents"
    assert log["skills"] == [str(work / "author" / skills_dir / "skills/skillz")], log
    if harness == "codex":
        assert log["home"] == str(work / "author-home") and log["codex_home"] == str(work / "codex-home")
        assert log["env"] == ["CODEX_HOME", "HOME", "LANG", "PATH", "TMPDIR", "UV_CACHE_DIR"], log
        assert (work / "codex-home/auth.json").resolve() == host / "auth.json"
        assert sorted(path.name for path in (work / "codex-home").iterdir()) == ["auth.json"]


@pytest.mark.ac("autoimprove-live-proof:AC-8")
@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_wrong_draft_stops_the_author_stage_with_the_shared_draft_check(
        harness: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _fake_agents(tmp_path, monkeypatch, reference_draft()[:3])
    authored, fields = changelog_proof.author(_target(tmp_path), tmp_path / "work", harness, "m")
    assert authored is None and fields["code"] == "draft-wrong" and "families hold 3" in str(fields["error"]), fields


@pytest.mark.ac("autoimprove-live-proof:AC-8")
def test_the_codex_author_session_may_write_only_its_own_directories() -> None:
    argv = codex_argv(Path("/d"), Path("/o"), Path("/h"), "gpt-6-astra")
    assert argv[:2] == ["codex", "exec"] and argv[-1] == "-"
    for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--json", "--skip-git-repo-check"):
        assert flag in argv
    configs = [argv[index + 1] for index, part in enumerate(argv) if part == "-c"]
    assert {"sandbox_workspace_write.exclude_slash_tmp=true", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "sandbox_workspace_write.network_access=true"} == set(configs), configs
    assert argv[argv.index("--sandbox") + 1] == "workspace-write" and argv[argv.index("-C") + 1] == "/d"
    assert [argv[index + 1] for index, part in enumerate(argv) if part == "--add-dir"] == ["/o", "/h"]
    assert argv[argv.index("--model") + 1] == "gpt-6-astra"


def _archive(tmp_path: Path, body: str) -> Path:
    """Write a stand-in for the built CLI. Python runs a plain script that has a `.pyz` name."""
    archive = tmp_path / "build/changelog-format.pyz"
    archive.parent.mkdir(parents=True)
    _ = archive.write_text(f"import json, sys\nfrom pathlib import Path\nsys.path.insert(0, {str(HERE)!r})\n"
                           + f"from changelog_check import entries_for, entry_for\n{body}", encoding="utf-8")
    return archive


GUIDE = "Run `python3 .agents/skills/changelog-entry/scripts/changelog-format.pyz changes.json`.\n"
LOAD = "changes = json.loads(Path(sys.argv[1]).read_text())\n"
WRITES = LOAD + "Path('entries.md').write_text(entries_for(changes))\n"
PRINTS = LOAD + "print(json.dumps({'entries': [entry_for(change) for change in changes]}))\n"
PRINTS_TEXT = LOAD + "print(entries_for(changes), end='')\n"
PLANTED = LOAD + """for change in changes:
    entry = entry_for(change)
    if change['ticket']:
        entry = entry.replace('[' + change['ticket'] + '] ', '') + ' (' + change['ticket'] + ')'
    print(entry)
"""


@pytest.mark.ac("autoimprove-live-proof:AC-13")
@pytest.mark.parametrize("body", [WRITES, PRINTS, PRINTS_TEXT], ids=["writes-entries-md", "prints-json", "prints-text"])
def test_the_cli_check_passes_a_cli_that_keeps_every_rule_on_every_case(body: str, tmp_path: Path) -> None:
    fields = match_entries(GUIDE, _archive(tmp_path, body), "changelog-format", reference_draft(), tmp_path / "cases")
    assert fields["cli"] == "ok" and fields["cli_cases"] == 18 and fields["cli_mismatches"] == [], fields


@pytest.mark.ac("autoimprove-live-proof:AC-13")
def test_the_cli_check_names_each_case_that_breaks_a_rule(tmp_path: Path) -> None:
    fields = match_entries(GUIDE, _archive(tmp_path, PLANTED), "changelog-format", reference_draft(), tmp_path / "cases")
    assert fields["cli"] == "mismatch" and "few-tickets-0" in cast(list[str], fields["cli_mismatches"]), fields
    assert re.match(r"breaking-release-0: entries\.md:\d+: rule 1 \(ticket id\)", cast(list[str], fields["cli_faults"])[0])
    draft = reference_draft()
    one = _archive(tmp_path / "one", LOAD + f"text = entries_for(changes)\n"
                   + f"print(text.replace('\\n', '.\\n', 1) if changes == {_cases()[4]!r} else text, end='')\n")
    fields = match_entries(GUIDE, one, "changelog-format", draft, tmp_path / "one-cases")
    assert fields["cli"] == "mismatch" and fields["cli_mismatches"] == [draft[4]["id"]], fields
    assert cast(list[str], fields["cli_faults"])[0].startswith(f"{draft[4]['id']}: entries.md:1: rule 4 (summary)")


@pytest.mark.ac("autoimprove-live-proof:AC-13")
def test_the_cli_check_reports_no_invocation_when_no_guide_command_gives_entries(tmp_path: Path) -> None:
    failing = _archive(tmp_path, "sys.exit(2)\n")
    assert match_entries(GUIDE, failing, "changelog-format", reference_draft(), tmp_path / "a")["cli"] == "no-invocation"
    placeholder = "Run `python3 scripts/changelog-format.pyz <file>`.\n"
    good = _archive(tmp_path / "good", WRITES)
    assert match_entries(placeholder, good, "changelog-format", reference_draft(), tmp_path / "b")["cli"] == "no-invocation"
    count_only = _archive(tmp_path / "count", LOAD + "print(json.dumps({'count': len(changes)}))\n")
    assert match_entries(GUIDE, count_only, "changelog-format", reference_draft(), tmp_path / "c")["cli"] == "no-invocation"


@pytest.mark.ac("autoimprove-live-proof:AC-13")
def test_the_cli_check_skips_a_help_command_listed_before_the_real_one(tmp_path: Path) -> None:
    body = "if sys.argv[1] == '--help':\n    print('usage: changelog-format FILE')\n    sys.exit(0)\n" + WRITES
    guide = ("Run `python3 scripts/changelog-format.pyz --help` for usage.\n"
             + "Run `python3 scripts/changelog-format.pyz changes.json`.\n")
    fields = match_entries(guide, _archive(tmp_path, body), "changelog-format", reference_draft(), tmp_path / "cases")
    assert fields["cli"] == "ok" and fields["cli_command"] == "python3 scripts/changelog-format.pyz changes.json", fields

def _runner_patch() -> str:
    """Return a patch in the runner's own skill-file format: a change, an added file, and a deleted file."""
    lines = [*_patch_lines("references/format.md", "1. old\n", "1. new\n", git=False),
             *_patch_lines("src/changelog_format/__init__.py", None, "def main():\n    pass\n", git=False),
             *_patch_lines("notes.md", "gone\n", None, git=False)]
    return _patch_text(lines)


@pytest.mark.ac("autoimprove-live-proof:AC-10")
def test_patch_files_lists_each_path_of_a_runner_patch(tmp_path: Path) -> None:
    patch = tmp_path / "candidate.patch"
    _ = patch.write_text(_runner_patch(), encoding="utf-8")
    assert patch_files(patch) == ["references/format.md", "src/changelog_format/__init__.py", "notes.md"]
    assert patch_files(tmp_path / "absent.patch") == []


@pytest.mark.ac("autoimprove-live-proof:AC-10")
def test_the_scenario_check_records_the_claims_and_whether_the_patch_changed_format_md(tmp_path: Path) -> None:
    (tmp_path / "export").mkdir()
    _ = (tmp_path / "export/candidate.patch").write_text(_runner_patch(), encoding="utf-8")
    _ = (tmp_path / "export/report.json").write_text(json.dumps({"claims": CLAIMS}), encoding="utf-8")
    fields = check(tmp_path / "target", tmp_path, reference_draft())
    assert fields["claims"] == CLAIMS and fields["format_md_changed"] is True and fields["cli"] == "missing", fields
    assert "src/changelog_format/__init__.py" in cast(list[str], fields["patch_files"]), fields


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


def _token_rule() -> Statistics:
    """Return the gate thresholds of the scenario flags, checked as the run checks them."""
    pairs = zip(TOKEN_RULE[::2], TOKEN_RULE[1::2], strict=True)
    declared = Statistics.declared({flag.removeprefix("--").replace("-", "_"): float(value) for flag, value in pairs})
    return cast(Callable[..., Statistics], Statistics)(**declared)


PROSE_TOKENS = (4000.0, 4200.0, 3900.0, 4100.0, 4050.0, 3950.0)


def _tokens(share: float) -> dict[str, tuple[float | None, float | None]]:
    """Pair the prose incumbent tokens of each case with a challenger that spends `share` of them, with jitter."""
    return {f"c{index}": (base, base * share * (1 + (-1) ** index * 0.02)) for index, base in enumerate(PROSE_TOKENS)}


@pytest.mark.ac("autoimprove-live-proof:AC-14")
def test_a_cli_that_ties_a_prose_fix_with_fewer_tokens_replaces_it_only_under_the_token_rule() -> None:
    ties = dict.fromkeys(_tokens(0.8), 0.0)
    assert gate_verdict(ties, thresholds=_token_rule(), tokens=_tokens(0.8)).verdict == "promote-cheaper"
    assert gate_verdict(ties, thresholds=DEFAULT_STATISTICS, tokens=_tokens(0.8)).verdict == "inconclusive"
    assert gate_verdict(ties, thresholds=_token_rule(), tokens=_tokens(0.95)).verdict == "inconclusive"


@pytest.mark.ac("autoimprove-live-proof:AC-14")
def test_a_fix_of_the_planted_rule_still_promotes_on_quality_against_the_seed() -> None:
    deltas = {name: 0.0 if name == "c3" else 1.0 for name in _tokens(0.8)}
    result = gate_verdict(deltas, thresholds=_token_rule(), tokens=_tokens(0.8))
    assert (result.verdict, result.reasons) == ("promote", ()), result
    unknown = _tokens(0.8) | {"c0": (4000.0, None)}
    assert gate_verdict(deltas, thresholds=_token_rule(), tokens=unknown).reasons == ("unknown-usage",)

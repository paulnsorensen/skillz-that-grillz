"""The `changelog-proof` live smoke scenario: the /skillz autoimprove journey fixes a planted rule and ships a CLI.

The fixture skill `changelog-entry` writes one changelog entry from a `change.json`. Rule 1 of its
`references/format.md` is wrong: it puts the ticket id at the end of the entry, and the check wants it first.
The journey stage runs `/skillz autoimprove` in `claude -p` or in `codex exec` until the agent writes the case draft.
The author prompt states the requirement as a user would. The case requests never state it.
The hybrid grader runs `changelog_check.py`: a broken rule scores 0 with a message that names the rule and the line.
`check` then rebuilds the exported CLI and scores its entry for every drafted case with the same check.
The scenario passes only when `claims.apply` is true and `cli` is `ok`.
"""
from __future__ import annotations

import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import cast

from changelog_check import LABELS, change_problem, entry_for, verdict
from fromargs_benefit import (AUTHOR_SECONDS, SKILLZ, build_project, build_winner, claude_author, invocations,
                              read_draft)

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures/changelog-entry"
NAME = "changelog-entry"
SEED_CLI = "change-check"
CHECK = (HERE / "changelog_check.py").read_text(encoding="utf-8")
"""The command check source. The grader runs it as `python3 -I -c CHECK`."""
RUBRIC = ("Score each criterion. entry.md under output/ must hold the expected entry line and nothing else, "
          "and the JSON answer must repeat that line.")
CONTRACT: dict[str, object] = {
    "schema_version": 1, "status": "approved", "skill": NAME, "invocation": f"${NAME}",
    "kinds": {"task": {"grader": "hybrid", "argv": ["/usr/bin/python3", "-I", "-c", CHECK], "rubric": RUBRIC}},
    "editable": []}
"""The contract declares no helper, so the seed and every candidate get helper credit."""
PYPROJECT = ('[project]\nname = "changelog-entry"\nversion = "0"\nrequires-python = ">=3.11"\n'
             'dependencies = ["cyclopts>=5,<6"]\n')
WEDGE_TOML = (f'project = "."\nrepo = "local/{NAME}"\nname = "{SEED_CLI}"\nentry = "change_check:main"\n'
              'source = "src/change_check"\ninclude = ["lib/fromargs"]\n')
"""One fromargs target, so the run offers `@new-cli` (experiments.md `New target`)."""
FAMILIES = ("fixes", "features", "breaking", "untidy-summaries", "other-types", "no-ticket")
CASES_PER_FAMILY = 3
MIN_FAMILIES = 6
MIN_DECIDED = 2
"""Each family needs at least this many cases with a ticket, which the planted rule decides."""
REQUEST = "Write the changelog entry for the change in change.json."
RULE_WORDS = re.compile(r"\[|bracket|prefix|ticket|parenthes|first|start|begin", re.IGNORECASE)
"""A request that holds one of these words can state the enforced rule, so the draft check refuses it."""
STRONG = {"claude": "opus", "codex": "gpt-6-astra"}
SIGIL = {"claude": "/", "codex": "$"}
"""Claude Code calls a skill as `/name`. Codex calls it as `$name`."""
PROMPT = """{sigil}skillz autoimprove {target}

Use the {harness} harness. The task model is {task_model}.
Use {out} as the run directory. It exists and is private.
Skip the Usage lens and session mining.
The skill has an approved contract with a hybrid grader. Do not change it.
The skill gets one thing wrong. Our changelog entries start with the ticket id in square brackets, as in
`- [ABC-123] Fixed in parser: Handle empty body`. A change with no ticket has no ticket id.
Every other rule of the skill is right.
Draft exactly {cases} task cases in {families} families of {per_family} cases.
Each task case gives one file, `change.json`, with the keys `ticket`, `type`, `scope`, `summary`, and `breaking`.
In each family, at least {decided} cases have a ticket.
Each request asks for the changelog entry of the change in plain words, such as "{request}".
A request never states a format rule.
Each `expected` holds `entry`, the one correct entry line, and `criteria` that check that `entry.md` holds it.
Stop at the case approval question. Do not approve the cases or the budget, and do not start the search.
"""
CODEX_HOME_FILES = ("auth.json",)


def strong_model(harness: str) -> str:
    """Return the model of the drafting agent, the reflection role, and the judge role on `harness`."""
    return os.environ.get("SKILLZ_LIVE_STRONG_MODEL") or STRONG[harness]


def build(work: Path) -> Path:
    """Write the `changelog-entry` skill into a fresh git repository and return the skill directory.

    The skill is its own uv project with a lock, a vendored fromargs include, and a built `change-check.pyz`.
    This step needs `uv`, `git`, and the network for the lock. It makes no model call.
    """
    return build_project(work, FIXTURE, CONTRACT, PYPROJECT, WEDGE_TOML, SEED_CLI)


SUMMARIES = ("handle empty body", "add export command", "cache config lookups", "explain retry flags",
             "split the parser module", "support yaml input", "close files on error", "drop the v1 endpoint",
             "rename the env variables", "keep the trailing newline", "accept api tokens", "stream large uploads")
SCOPES = ("parser", "cli", "api", "io", "config", "auth")


def _change(family: str, index: int, rng: random.Random) -> dict[str, object]:
    """Return the `change.json` object of one reference case. The family decides the rule that the case tests."""
    kinds = {"fixes": ("fix",) * 3, "features": ("feat",) * 3, "other-types": ("perf", "docs", "refactor"),
             "breaking": ("feat", "fix", "refactor")}.get(family)
    kind = kinds[index] if kinds else rng.choice(tuple(LABELS))
    summary = rng.choice(SUMMARIES)
    if family == "untidy-summaries":
        summary = (summary + ".") if index != 1 else summary.capitalize() + "."
    ticket = None if family == "no-ticket" and index == 0 else f"{rng.choice(('ABC', 'OPS', 'WEB'))}-{rng.randint(1, 999)}"
    return {"ticket": ticket, "type": kind, "scope": None if index == 2 else rng.choice(SCOPES), "summary": summary,
            "breaking": family == "breaking" and index != 1}


def _criteria(entry: str) -> list[dict[str, object]]:
    return [{"id": "entry", "must": True, "text": f"THE skill SHALL write `{entry}` as the only line of entry.md."},
            {"id": "answer", "text": "THE skill SHALL return the same entry line in its JSON answer."}]


def reference_draft() -> list[dict[str, object]]:
    """Return an 18-case draft in 6 families with oracle entries. A dry run uses it in place of the journey draft."""
    draft: list[dict[str, object]] = []
    for family in FAMILIES:
        for index in range(CASES_PER_FAMILY):
            change = _change(family, index, random.Random(f"{family}-{index}"))
            entry = entry_for(change)
            draft.append({"id": f"{family}-{index}", "family": family, "kind": "task", "source": "skill",
                          "request": REQUEST, "files": {"change.json": json.dumps(change) + "\n"},
                          "expected": {"entry": entry, "criteria": _criteria(entry)}})
    return draft


def _change_of(case: dict[str, object]) -> dict[str, object] | str:
    files = case.get("files")
    text = cast(dict[str, object], files).get("change.json") if isinstance(files, dict) else None
    if not isinstance(text, str):
        return "files lack change.json"
    try:
        change = cast(object, json.loads(text))
    except ValueError:
        return "change.json is not JSON"
    problem = change_problem(change)
    return problem if problem is not None else cast(dict[str, object], change)


def _case_problem(case: dict[str, object]) -> str | None:
    change = _change_of(case)
    if isinstance(change, str):
        return change
    request, ticket = case.get("request"), change.get("ticket")
    if not isinstance(request, str) or RULE_WORDS.search(request) or (ticket and str(ticket) in request):
        return "the request can state the enforced rule; it must ask for the entry in plain words"
    expected = case.get("expected")
    fields = cast(dict[str, object], expected) if isinstance(expected, dict) else {}
    criteria = fields.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return "the scored case has no criteria"
    want = entry_for(change)
    if fields.get("entry") != want:
        return f"expected entry {json.dumps(fields.get('entry'))}, the oracle says {json.dumps(want)}"
    return None


def check_draft(draft: object) -> list[str]:
    """Return one problem per fault of an agent-written draft. An empty list means the draft passes.

    The draft needs at least `MIN_FAMILIES` families of `CASES_PER_FAMILY` task cases, and each family at least
    `MIN_DECIDED` cases with a ticket. Each task case needs a valid `change.json`, a request that states no rule,
    criteria, and the oracle entry as `expected.entry`.
    """
    if not isinstance(draft, list) or not all(isinstance(case, dict) for case in cast(list[object], draft)):
        return ["the draft is not a list of case objects"]
    tasks = [case for case in cast(list[dict[str, object]], draft) if case.get("kind") == "task"]
    problems = [f"{case.get('id')}: {problem}" for case in tasks if (problem := _case_problem(case))]
    sizes = Counter(str(case.get("family")) for case in tasks)
    full = sorted(family for family, size in sizes.items() if size >= CASES_PER_FAMILY)
    if len(full) < MIN_FAMILIES:
        problems.append(f"{len(full)} families hold {CASES_PER_FAMILY} task cases; the scenario needs {MIN_FAMILIES}")
    decided = Counter(str(case.get("family")) for case in tasks
                      if isinstance(change := _change_of(case), dict) and change.get("ticket"))
    problems += [f"family {family} has {decided[family]} cases with a ticket; the scenario needs {MIN_DECIDED}"
                 for family in sorted(sizes) if decided[family] < MIN_DECIDED]
    return problems


def author(target: Path, work: Path, harness: str,
           task_model: str) -> tuple[list[dict[str, object]] | None, dict[str, object]]:
    """Run the /skillz autoimprove journey on `harness` until the agent writes the case draft.

    Return the draft and record fields. Both harnesses get the same prompt and the same draft check.
    """
    out = work / "author-run"
    prompt = PROMPT.format(sigil=SIGIL[harness], target=target, harness=harness, task_model=task_model, out=out,
                           cases=len(FAMILIES) * CASES_PER_FAMILY, families=len(FAMILIES),
                           per_family=CASES_PER_FAMILY, decided=MIN_DECIDED, request=REQUEST)
    if harness == "claude":
        return claude_author(target, work, prompt, strong_model(harness), check_draft)
    return codex_author(target, work, prompt, strong_model(harness))


def codex_argv(driver: Path, out: Path, home: Path, model: str) -> list[str]:
    """Return the `codex exec` argv of the author session.

    The session ignores the user config and rules. It may write only the run directory and its private home, and it
    has network access, because the runner can populate the uv site layer of the fixture.
    """
    return ["codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--json", "--skip-git-repo-check",
            "--sandbox", "workspace-write", "-c", "sandbox_workspace_write.network_access=true", "-C", str(driver),
            "--add-dir", str(out), "--add-dir", str(home), "--model", model, "-"]


def _codex_events(stdout: str) -> tuple[str, dict[str, object] | None]:
    """Return the last agent message and the last token usage of the `codex exec --json` event lines."""
    said, usage = "", None
    for line in stdout.splitlines():
        try:
            event = cast(object, json.loads(line))
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        fields = cast(dict[str, object], event)
        item = fields.get("item")
        if fields.get("type") == "item.completed" and isinstance(item, dict):
            entry = cast(dict[str, object], item)
            if entry.get("type") == "agent_message":
                said = str(entry.get("text", ""))
        if fields.get("type") == "turn.completed" and isinstance(fields.get("usage"), dict):
            usage = cast(dict[str, object], fields["usage"])
    return said, usage


def codex_author(target: Path, work: Path, prompt: str,
                 model: str) -> tuple[list[dict[str, object]] | None, dict[str, object]]:
    """Run `prompt` in `codex exec` with a copy of the `skillz` skill under `.agents/skills`.

    A private `HOME` and `CODEX_HOME` keep the user's skills, config, and rules out of the session. Only `auth.json`
    links to the host Codex home. Codex reports no turn count, so `author_turns` is None.
    """
    driver, out, home = work / "author", work / "author-run", work / "author-home"
    _ = shutil.copytree(SKILLZ, driver / ".agents/skills/skillz", ignore=shutil.ignore_patterns("__pycache__"))
    out.mkdir(mode=0o700)
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    host = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    for name in CODEX_HOME_FILES:
        (codex_home / name).symlink_to(host / name)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("CODEX_")} | {
        "HOME": str(home), "CODEX_HOME": str(codex_home), "UV_CACHE_DIR": str(home / ".cache/uv")}
    try:
        result = subprocess.run(codex_argv(driver, out, home, model), input=prompt, cwd=driver, env=environment,
                                capture_output=True, text=True, timeout=AUTHOR_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return None, {"author_harness": "codex", "code": "author-timeout",
                      "error": f"codex exec ran over {AUTHOR_SECONDS} seconds"}
    said, usage = _codex_events(result.stdout)
    fields: dict[str, object] = {"author_harness": "codex", "author_model": model, "author_exit": result.returncode,
                                 "author_turns": None, "author_usage": usage}
    return read_draft(target, out, fields, said or result.stderr, check_draft)


def patch_files(patch: Path) -> list[str]:
    """Return the paths that a git patch changes, in patch order."""
    text = patch.read_text(encoding="utf-8") if patch.is_file() else ""
    return cast(list[str], re.findall(r"^diff --git a/(\S+) b/", text, re.MULTILINE))


def check(target: Path, work: Path, draft: list[dict[str, object]]) -> dict[str, object]:
    """Copy the claims and the gate numbers of `export/report.json`, then rebuild and check the exported CLI.

    The runner keeps `edit_hint` only in the proposer feedback, so the record names whether the patch changed
    `references/format.md` in its place.
    """
    report = cast(dict[str, object], json.loads((work / "export/report.json").read_text(encoding="utf-8")))
    gate = cast(dict[str, object], report.get("gate")) if isinstance(report.get("gate"), dict) else {}
    changed = patch_files(work / "export/candidate.patch")
    fields: dict[str, object] = {"claims": report.get("claims"), "delta": gate.get("delta"), "se": gate.get("se"),
                                 "patch_files": changed, "format_md_changed": "references/format.md" in changed}
    built = build_winner(target, work)
    if isinstance(built, dict):
        return fields | built
    name, archive, winner = built
    tasks = [case for case in draft if case.get("kind") == "task"]
    guide = (winner / "SKILL.md").read_text(encoding="utf-8")
    return fields | match_entries(guide, archive, name, tasks, work / "cli-cases")


def _entry(argv: list[str], archive: Path, name: str, files: dict[str, str], workspace: Path) -> str | None:
    """Run one guide command in a workspace with the case files. Return the entry that it writes or prints."""
    workspace.mkdir(parents=True)
    for path, text in files.items():
        _ = (workspace / path).write_text(text, encoding="utf-8")
    command = [str(archive) if part.endswith(f"scripts/{name}.pyz") else part for part in argv]
    if command[0] in ("python", "python3"):
        command[0] = sys.executable
    elif command[0] == str(archive):
        command.insert(0, sys.executable)
    try:
        result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    if (workspace / "entry.md").is_file():
        return (workspace / "entry.md").read_text(encoding="utf-8")
    try:
        printed = cast(object, json.loads(result.stdout))
    except ValueError:
        return result.stdout or None
    if isinstance(printed, dict) and isinstance(cast(dict[str, object], printed).get("entry"), str):
        return str(cast(dict[str, object], printed)["entry"]).rstrip("\n") + "\n"
    return None


def match_entries(guide: str, archive: Path, name: str, tasks: list[dict[str, object]], work: Path) -> dict[str, object]:
    """Run `archive` with the first `guide` command that gives an entry, on every task case. Score each entry.

    The CLI writes `entry.md`, prints a JSON object with `entry`, or prints the entry line. Each entry goes through
    the scenario check. `cli` is `ok` only when every case keeps every rule. A mismatch names each failed case.
    """
    for number, argv in enumerate(invocations(guide, name)):
        entries = [_entry(argv, archive, name, cast(dict[str, str], case["files"]), work / str(number) / str(index))
                   for index, case in enumerate(tasks)]
        if all(entry is None for entry in entries):
            continue
        faults = {str(case.get("id")): message for case, entry in zip(tasks, entries, strict=True)
                  if (message := verdict(cast(dict[str, object], _change_of(case)), entry)) is not None}
        return {"cli": "mismatch" if faults else "ok", "cli_target": name, "cli_command": shlex.join(argv),
                "cli_cases": len(tasks), "cli_mismatches": sorted(faults)[:10],
                "cli_faults": [f"{case}: {faults[case]}" for case in sorted(faults)[:3]]}
    return {"cli": "no-invocation", "cli_target": name}


def _claim_text(name: str, part: object) -> str:
    if not isinstance(part, dict):
        return f"{name} missing"
    claim = cast(dict[str, object], part)
    checks = cast(dict[str, object], claim.get("checks")) if isinstance(claim.get("checks"), dict) else {}
    reasons = [str(reason) for reason in cast(list[object], claim.get("reasons") or [])]
    reasons += [f"{check_name}={value}" for check_name, value in checks.items() if value != "pass"]
    return f"{name} {claim.get('status')} ({', '.join(reasons) or 'no reason'})"


def proof_verdict(entry: dict[str, object]) -> tuple[bool, str]:
    """Return whether a `changelog-proof` record passes, and the reason.

    Only a completed run with `claims.apply` true and `cli` `ok` passes. A failure names the stage, the stop code,
    each claim status with its reasons, and the `cli` status.
    """
    claims = cast(dict[str, object], entry.get("claims")) if isinstance(entry.get("claims"), dict) else {}
    state = "; ".join(_claim_text(name, claims.get(name)) for name in ("runner", "export", "improvement"))
    facts = f"stage {entry.get('stage')}, code {entry.get('code')}, claims: {state}, cli={entry.get('cli')}"
    if entry.get("stage") != "complete" or entry.get("phase") != "complete":
        return False, f"the run did not complete: {facts}"
    if claims.get("apply") is not True:
        return False, f"claims.apply is {claims.get('apply')}, so the run promoted no supported improvement: {facts}"
    if entry.get("cli") != "ok":
        return False, f"the winner ships no CLI that keeps every rule on every case: {facts}"
    return True, (f"verdict {entry.get('verdict')}, delta {entry.get('delta')}, SE {entry.get('se')}, "
                  f"{entry.get('holdout_scored')} holdout cases, patch files {entry.get('patch_files')}, "
                  f"CLI {entry.get('cli_target')} ok on {entry.get('cli_cases')} cases, "
                  f"format.md changed: {entry.get('format_md_changed')}")

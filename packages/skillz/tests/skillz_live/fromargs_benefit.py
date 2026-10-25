"""The `fromargs-benefit` live smoke scenario: autoimprove writes its own eval, then the run must ship a fromargs CLI.

The fixture skill `semver-next` makes the agent apply nine version rules by hand on every run. A fromargs CLI holds
those rules once, so the search can beat the seed with `@new-cli`. Stage 1 runs `/skillz autoimprove` in `claude -p`
until the case approval question, and `check_draft` compares every drafted answer with `semver_oracle`. Stage 2 is the
shared runner path of `test_live_smoke.py`. `check_cli` then rebuilds the exported CLI and runs it on every drafted case.
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
from pathlib import Path
from typing import cast

from semver_oracle import commit_count, next_version

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FIXTURE = HERE / "fixtures/semver-next"
SKILLZ = ROOT / "skills/skillz"
WEDGE = SKILLZ / "scripts/wedge.pyz"
FROMARGS = ROOT / "packages/fromargs/src/fromargs"
NAME = "semver-next"
CASE_FILES = ("VERSION", "commits.txt")
ANSWER_KEYS = frozenset({"bump", "next"})
"""The `exact-json` grader compares the whole answer with `expected`, so `expected` holds these keys and no other."""
CONTRACT: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": NAME, "invocation": f"${NAME}",
            "kinds": {"task": {"grader": "exact-json"}}, "editable": []}
"""An `exact-json` evaluation costs one call, half of a judged one, so the budget buys more search."""
FAMILIES = ("plain-bumps", "breaking-markers", "reverts", "zero-and-rc")
CASES_PER_FAMILY = 3
DRAFT_FAMILIES = 8
"""Twice the four reference families: the search budget is 4 metric calls per train and validation case."""
MIN_FAMILIES = 3
MIN_COMMITS = 80
"""A long log costs the seed many tokens to apply the rules by hand. A CLI applies them for a fixed cost."""
TOKEN_RULE = ("--min-token-saving", "0.3", "--max-quality-loss", "0.05")
"""A sonnet seed answers every case exactly, so the CLI can only win on task tokens (experiments.md `The token rule`).
With `min_token_saving` set, the headroom pass records the seed scores and does not stop with `cases-too-easy`."""
AUTHOR_SECONDS = 1800
AUTHOR_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "Bash(python3:*)", "Bash(ls:*)", "Bash(mkdir:*)")
"""The drafting agent runs the bundled runner, reads the target, and writes the draft. It gets no other shell command."""
PYPROJECT = ('[project]\nname = "semver-next"\nversion = "0"\nrequires-python = ">=3.11"\n'
             'dependencies = ["cyclopts>=5,<6"]\n')
WEDGE_TOML = ('project = "."\nrepo = "local/semver-next"\nname = "version-parse"\nentry = "version_parse:main"\n'
              'source = "src/version_parse"\ninclude = ["lib/fromargs"]\n')
"""One fromargs target, so the run offers `@new-cli` (experiments.md `New target`)."""
PROMPT = """/skillz autoimprove {target}

Use the claude harness. The task model is {task_model}.
Use {out} as the run directory. It exists and is private.
Skip the Usage lens and session mining.
The skill has an approved contract with an exact-json grader. Do not change it.
Draft exactly {cases} task cases in {families} families of {per_family} cases.
Each task case gives the files `VERSION` and `commits.txt`.
Its `expected` is the exact JSON answer of the skill, with only the keys `bump` and `next`.
Draft hard cases. Each `commits.txt` holds at least {min_commits} commits.
Every case mixes revert chains, reverts of reverts, and near-miss headers with the other edge rules.
Stop at the case approval question. Do not approve the cases or the budget, and do not start the search.
"""

NOISE = ("chore: bump deps", "docs: fix typo in README", "ci: cache uv", "test: cover parser",
         "refactor(core): split module", "style: format", "build: pin uv", "chore(release): prepare notes")
DECOYS = ("Merge branch 'main' into topic", "WIP", "feat:missing space", "feat!(api): bang before scope",
          "feature: not a type", " fix: leading space", "fix(): empty scope", "Update README.md")
FEATS = ("feat: add export", "feat(cli): add --dry-run", "Feat(api): accept tokens", "FEAT: support yaml")
FIXES = ("fix: handle empty body", "fix(parser): keep trailing newline", "perf: cache lookups", "Fix(io): close files")
FOOTER_DECOYS = ("breaking change: lower case", "  BREAKING CHANGE: indented", "BREAKING CHANGE:no space")


def strong_model() -> str:
    """Return the model of the drafting agent, the reflection role, and the judge role.

    The default is opus: a sonnet reflector made only prose edits and never tried `@new-cli`.
    """
    return os.environ.get("SKILLZ_LIVE_STRONG_MODEL") or "opus"


def _checked(argv: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False, timeout=600)
    if result.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} {argv[1]} exited {result.returncode}: {result.stderr[-500:]}")
    return result.stdout


def build_wedge(skill: Path, destination: Path) -> dict[str, dict[str, str]]:
    """Build every wedge target of `skill` with the bundled `wedge.pyz`. Return the name-to-build map."""
    destination.mkdir()
    output = _checked([sys.executable, "-I", str(WEDGE), "build", str(skill), "--out", str(destination)])
    return cast(dict[str, dict[str, str]], json.loads(output))


def build_target(work: Path) -> Path:
    """Write the `semver-next` skill into a fresh git repository and return the skill directory.

    The skill is its own uv project with a lock, a vendored fromargs include, and a built `version-parse.pyz`.
    This step needs `uv`, `git`, and the network for the lock. It makes no model call.
    """
    repo = work / "repo"
    skill = repo / NAME
    junk = shutil.ignore_patterns("__pycache__")
    _ = shutil.copytree(FIXTURE, skill, ignore=junk)
    _ = (skill / "SKILL.md.fixture").rename(skill / "SKILL.md")
    _ = shutil.copytree(FROMARGS, skill / "lib/fromargs", ignore=junk)
    _ = (skill / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    _ = (skill / "wedge.toml").write_text(WEDGE_TOML, encoding="utf-8")
    (skill / "evals").mkdir()
    _ = (skill / "evals/autoimprove.json").write_text(json.dumps(CONTRACT, indent=2) + "\n", encoding="utf-8")
    _ = _checked(["uv", "lock", "--project", str(skill)])
    built = build_wedge(skill, work / "seed-build")
    (skill / "scripts").mkdir()
    _ = shutil.copy2(built["version-parse"]["path"], skill / "scripts/version-parse.pyz")
    _ = _checked(["git", "init", "-q", str(repo)])
    _ = _checked(["git", "-C", str(repo), "add", "-A"])
    _ = _checked(["git", "-C", str(repo), "-c", "user.name=live-smoke", "-c", "user.email=live-smoke@invalid",
                  "commit", "-qm", "semver-next fixture"])
    return skill


def _commits(rng: random.Random, planted: list[list[str]], size: int) -> str:
    blocks = [[rng.choice(NOISE)] for _ in range(size - len(planted))] + planted
    rng.shuffle(blocks)
    return "\n---\n".join("\n".join(block) for block in blocks) + "\n"


def _planted(family: str, index: int, rng: random.Random) -> tuple[str, list[list[str]]]:
    """Return the VERSION text and the commits that decide the answer of one reference case."""
    decoys = [[rng.choice(DECOYS)], [rng.choice(DECOYS), "", "BREAKING CHANGE: ignored with its commit"]]
    if family == "plain-bumps":
        planted = [[rng.choice(FIXES)], ["feat:missing space"]] + ([[rng.choice(FEATS)]] if index else [])
        return f"{index + 1}.{index + 3}.{index}", planted + decoys
    if family == "breaking-markers":
        markers = [["feat(api)!: drop v1"], ["chore: rename env", "", "BREAKING-CHANGE: env renamed"],
                   [rng.choice(FIXES), "", rng.choice(FOOTER_DECOYS)]]
        return f"2.{index}.7", [markers[index], [rng.choice(FIXES), "", rng.choice(FOOTER_DECOYS)]] + decoys
    if family == "reverts":
        feat = rng.choice(FEATS)
        chains = [[[feat], [f"revert: {feat}"], [f"revert: revert: {feat}"]], [[feat], [feat], [f"revert: {feat}"]],
                  [["feat!: drop flag"], ["revert: feat!: drop flag"], ["revert: feat: never existed"]]]
        return f"3.1.{index}", chains[index] + [[rng.choice(NOISE)]] + decoys
    versions = ("0.4.1", "0.9.0", "1.2.0-rc.3")
    planted = [["feat!: new config"], [rng.choice(FEATS)]] if index < 2 else [[rng.choice(FIXES)]]
    return versions[index], planted + decoys


def reference_draft() -> list[dict[str, object]]:
    """Return a 24-case draft in 8 families with oracle answers. A dry run uses it in place of the stage 1 draft.

    Each reference family appears twice, with its second copy drawn from another random stream.
    """
    draft: list[dict[str, object]] = []
    for family in (*FAMILIES, *(f"{name}-b" for name in FAMILIES)):
        for index in range(CASES_PER_FAMILY):
            rng = random.Random(f"{family}-{index}")
            version, planted = _planted(family.removesuffix("-b"), index, rng)
            files = {"VERSION": version + "\n", "commits.txt": _commits(rng, planted, MIN_COMMITS)}
            answer = next_version(files["VERSION"], files["commits.txt"])
            draft.append({"id": f"{family}-{index}", "family": family, "kind": "task", "source": "skill",
                          "request": "What is the next version of the project in this directory?",
                          "files": files, "expected": answer})
    return draft


def _case_problem(case: dict[str, object]) -> str | None:
    files, expected = case.get("files"), case.get("expected")
    if not isinstance(files, dict) or not all(isinstance(cast(dict[str, object], files).get(name), str)
                                              for name in CASE_FILES):
        return "files lack VERSION or commits.txt"
    texts = cast(dict[str, str], files)
    if (count := commit_count(texts["commits.txt"])) < MIN_COMMITS:
        return f"commits.txt holds {count} commits; the scenario needs {MIN_COMMITS}"
    keys = frozenset(cast(dict[str, object], expected)) if isinstance(expected, dict) else frozenset[str]()
    if keys != ANSWER_KEYS:
        return "expected must hold exactly bump and next"
    try:
        want = next_version(texts["VERSION"], texts["commits.txt"])
    except ValueError as error:
        return str(error)
    answer = {key: cast(dict[str, object], expected)[key] for key in sorted(ANSWER_KEYS)}
    return None if answer == want else f"expected {json.dumps(answer)}, the oracle says {json.dumps(want)}"


def check_draft(draft: object) -> list[str]:
    """Return one problem per fault of an agent-written draft. An empty list means the draft passes.

    Every task case must give `VERSION` and a `commits.txt` of at least `MIN_COMMITS` commits, hold the oracle answer
    as all of `expected`. The task cases must span at least `MIN_FAMILIES` families. The runner checks the split sizes
    itself.
    """
    if not isinstance(draft, list) or not all(isinstance(case, dict) for case in cast(list[object], draft)):
        return ["the draft is not a list of case objects"]
    tasks = [case for case in cast(list[dict[str, object]], draft) if case.get("kind") == "task"]
    problems = [f"{case.get('id')}: {problem}" for case in tasks if (problem := _case_problem(case))]
    families = {str(case.get("family")) for case in tasks}
    if len(families) < MIN_FAMILIES:
        problems.append(f"the task cases span {len(families)} families; the scenario needs {MIN_FAMILIES}")
    return problems


def target_edits(target: Path) -> str:
    """Return the `git status` lines of `target` that differ from its commit. An empty string means the target is clean."""
    return _checked(["git", "-C", str(target), "status", "--porcelain", "--", "."]).strip()


def author(target: Path, work: Path, task_model: str) -> tuple[list[dict[str, object]] | None, dict[str, object]]:
    """Run `/skillz autoimprove` in `claude -p` until it writes the case draft. Return the draft and record fields.

    The agent loads only the project settings and no MCP server, so the user's hooks and plugins stay out of the run.
    A missing or wrong draft returns None and fields with a `code`.
    """
    driver, out = work / "author", work / "author-run"
    (driver / ".claude/skills").mkdir(parents=True)
    (driver / ".claude/skills/skillz").symlink_to(SKILLZ, target_is_directory=True)
    out.mkdir(mode=0o700)
    argv = ["claude", "-p", "--model", strong_model(), "--output-format", "json", "--setting-sources", "project",
            "--strict-mcp-config", "--permission-mode", "acceptEdits", "--allowedTools", ",".join(AUTHOR_TOOLS),
            "--add-dir", str(target), str(out)]
    environment = {key: value for key, value in os.environ.items() if not key.startswith("CLAUDE_CODE_")
                   and key != "CLAUDECODE"}
    prompt = PROMPT.format(target=target, task_model=task_model, out=out, min_commits=MIN_COMMITS,
                           cases=DRAFT_FAMILIES * CASES_PER_FAMILY, families=DRAFT_FAMILIES, per_family=CASES_PER_FAMILY)
    try:
        result = subprocess.run(argv, input=prompt, cwd=driver, env=environment, capture_output=True, text=True,
                                timeout=AUTHOR_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return None, {"code": "author-timeout", "error": f"claude -p ran over {AUTHOR_SECONDS} seconds"}
    reply = cast(dict[str, object], json.loads(result.stdout)) if result.stdout.strip().startswith("{") else {}
    fields: dict[str, object] = {"author_model": strong_model(), "author_exit": result.returncode,
                                 "author_turns": reply.get("num_turns"), "author_cost_usd": reply.get("total_cost_usd")}
    try:
        draft = cast(object, json.loads((out / "cases.draft.json").read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        tail = str(reply.get("result") or result.stderr)[-300:]
        return None, fields | {"code": "draft-missing", "error": f"{error!r}; the agent said: {tail}"}
    if dirty := target_edits(target):
        return None, fields | {"code": "author-edited-target", "error": dirty[:300]}
    problems = check_draft(draft)
    fields["draft_cases"] = len(cast(list[object], draft)) if isinstance(draft, list) else None
    if problems:
        return None, fields | {"code": "draft-wrong", "error": "; ".join(problems)[:300], "draft_problems": problems}
    return cast(list[dict[str, object]], draft), fields


def _runs_archive(argv: list[str], name: str) -> bool:
    suffix = f"scripts/{name}.pyz"
    if argv and argv[0].endswith(suffix):
        return True
    return len(argv) > 1 and argv[0] in ("python", "python3") and argv[1].endswith(suffix)


def invocations(guide: str, name: str) -> list[list[str]]:
    """Return the commands of `guide` that run `scripts/<name>.pyz`, from fenced blocks and inline code spans.

    A command with a shell operator or a `<placeholder>` is skipped, because the check runs it without a shell.
    A command is kept only when the archive is its program, or the argument right after `python` or `python3`.
    """
    fences = cast(list[str], re.findall(r"```[^\n]*\n(.*?)```", guide, re.DOTALL))
    spans = cast(list[str], re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", guide, flags=re.DOTALL)))
    found: list[list[str]] = []
    for line in [line for fence in fences for line in fence.splitlines()] + spans:
        text = line.strip().removeprefix("$ ")
        if f"{name}.pyz" not in text or re.search(r"[|;&<>{}]", text):
            continue
        try:
            argv = shlex.split(text)
        except ValueError:
            continue
        if _runs_archive(argv, name) and argv not in found:
            found.append(argv)
    return found


def _answer(argv: list[str], archive: Path, name: str, files: dict[str, str], workspace: Path) -> dict[str, object] | None:
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
        value = cast(object, json.loads(result.stdout))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def check_cli(target: Path, work: Path, draft: list[dict[str, object]]) -> dict[str, object]:
    """Apply the exported patch to a copy of `target`, rebuild its new fromargs CLI, and run it on every task case.

    The command comes from the winner `SKILL.md`. Return record fields: `cli` is `ok` only when the CLI answers every
    task case as the oracle does.
    """
    report = cast(dict[str, object], json.loads((work / "export/report.json").read_text(encoding="utf-8")))
    new_cli = report.get("new_cli")
    if not isinstance(new_cli, dict):
        return {"cli": "missing"}
    name = str(cast(dict[str, object], new_cli).get("target"))
    winner = work / "winner"
    _ = shutil.copytree(target, winner, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    try:
        _ = _checked(["git", "init", "-q", str(winner)])
        _ = _checked(["git", "apply", str(work / "export/candidate.patch")], cwd=winner)
        archive = Path(build_wedge(winner, work / "winner-build")[name]["path"])
    except (RuntimeError, KeyError, ValueError) as error:
        return {"cli": "build-failed", "cli_target": name, "cli_error": repr(error)[:300]}
    tasks = [case for case in draft if case.get("kind") == "task"]
    return match_cli((winner / "SKILL.md").read_text(encoding="utf-8"), archive, name, tasks, work / "cli-cases")


def match_cli(guide: str, archive: Path, name: str, tasks: list[dict[str, object]], work: Path) -> dict[str, object]:
    """Run `archive` with the first `guide` command that answers, on every task case. Compare it with the oracle.

    A command answers when at least one case prints a JSON object with `bump` and `next`.
    """
    for number, argv in enumerate(invocations(guide, name)):
        answers = [_answer(argv, archive, name, cast(dict[str, str], case["files"]), work / str(number) / str(index))
                   for index, case in enumerate(tasks)]
        if not any(answer is not None and ANSWER_KEYS <= answer.keys() for answer in answers):
            continue
        wrong = [str(case.get("id")) for case, answer in zip(tasks, answers, strict=True)
                 if {key: (answer or {}).get(key) for key in ANSWER_KEYS} != _oracle(case)]
        return {"cli": "mismatch" if wrong else "ok", "cli_target": name, "cli_command": shlex.join(argv),
                "cli_cases": len(tasks), "cli_mismatches": wrong[:10]}
    return {"cli": "no-invocation", "cli_target": name}


def _oracle(case: dict[str, object]) -> dict[str, str]:
    files = cast(dict[str, str], case["files"])
    return next_version(files["VERSION"], files["commits.txt"])


def cli_verdict(entry: dict[str, object]) -> tuple[bool, str]:
    """Return whether a `fromargs-benefit` record passes, and the reason. Only a checked fromargs CLI passes.

    The gate verdict stays in the record. It does not decide the result, because no live run has promoted yet (#324).
    """
    stage = entry.get("stage")
    if stage != "complete":
        return False, (f"the run stopped at stage {stage} with code {entry.get('code')}; "
                       "the scenario needs a completed run that exports a fromargs CLI")
    if entry.get("phase") != "complete" or not Path(str(entry.get("patch"))).is_file():
        return False, "the run completed without phase complete and an exported candidate.patch"
    if entry.get("cli") != "ok":
        return False, f"the winner ships no fromargs CLI that matches the oracle: cli={entry.get('cli')}"
    return True, (f"the run exported the fromargs CLI {entry.get('cli_target')}, and it matched the oracle on "
                  f"{entry.get('cli_cases')} cases (gate verdict {entry.get('verdict')})")

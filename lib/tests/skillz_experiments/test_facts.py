"""Observable behavior of `skillz-experiment audit-facts` over fixture skill packages."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cli import main
from skillz_experiments._facts import ALLOWED_KEYS, SCHEMA_VERSION

LAYOUT = Path(__file__).resolve().parents[3] / "skills/skillz/references/harness-layout.md"
DESCRIPTION = "Does a demo thing. Use when asked. Do NOT use for other things."
BODY = """# demo

Run `python3 scripts/tool.py` for the facts.
Read the guide when a run needs it.

## References

- `references/guide.md` — read when a run needs the guide.
"""
FILES: dict[str, str | None] = {"references/guide.md": "# Guide\n", "scripts/tool.py": "print(1)\n"}
USER_ONLY = "disable-model-invocation: true\n"
SIDECAR = "policy:\n  allow_implicit_invocation: false\n"
Checks = list[dict[str, object]]


def skill(front: str = "model: sonnet\neffort: medium\n", body: str = BODY, name: str = "demo",
          description: str = DESCRIPTION) -> str:
    return f"---\nname: {name}\ndescription: {description}\n{front}---\n{body}"


def make(root: Path, text: str | None = None, files: dict[str, str | None] | None = None,
         directory: str = "demo") -> Path:
    package = root / directory
    package.mkdir(parents=True)
    _ = (package / "SKILL.md").write_text(skill() if text is None else text)
    for relative, content in {**FILES, **(files or {})}.items():
        if content is not None:
            target = package / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = target.write_text(content)
    return package


def run(package: Path, capsys: pytest.CaptureFixture[str]) -> Checks:
    assert main(["audit-facts", str(package)]) == 0
    result = cast(dict[str, object], json.loads(capsys.readouterr().out))
    assert result["schema_version"] == SCHEMA_VERSION and result["input"] == str(package)
    return cast(Checks, result["checks"])


def find(checks: Checks, check: str, path: str | None = None) -> dict[str, object]:
    hits = [c for c in checks if c["id"] == check and (path is None or c["path"] == path)]
    assert len(hits) == 1, (check, hits)
    return hits[0]


def line_of(text: str, needle: str) -> int:
    return next(number for number, line in enumerate(text.splitlines(), 1) if needle in line)


def test_passing_package_has_no_failed_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    checks = run(make(tmp_path), capsys)
    assert [c for c in checks if c["status"] == "fail"] == []
    assert {c["id"] for c in checks} >= {"name.matches-directory", "body.token-estimate", "references.nested"}
    assert find(checks, "model-policy.user-only")["status"] == "not-applicable"
    assert find(checks, "registration.readme-row")["status"] == "not-applicable"
    assert find(checks, "references.read-trigger", "references/guide.md")["line"] == 14
    assert find(checks, "scripts.invocation-line", "scripts/tool.py")["line"] == 9


USER_ONLY_SKILL = skill(USER_ONLY)
NESTED = "# Guide\n\nSee [other](other.md).\n"
CASES: list[tuple[str, str, str, int | None, dict[str, object]]] = [
    ("directory", "name.matches-directory", "SKILL.md", 2, {"directory": "other"}),
    ("name format", "name.format", "SKILL.md", 2, {"text": skill(name="Demo_Skill"), "directory": "Demo_Skill"}),
    ("long name", "name.format", "SKILL.md", 2, {"text": skill(name="a" * 65), "directory": "a" * 65}),
    ("long description", "description.length", "SKILL.md", 3, {"text": skill(description="d" * 1025)}),
    ("no description", "description.length", "SKILL.md", None,
     {"text": "---\nname: demo\nmodel: sonnet\neffort: medium\n---\n" + BODY}),
    ("unknown key", "frontmatter.known-keys", "SKILL.md", 6,
     {"text": skill("model: sonnet\neffort: medium\ninvented: yes\n")}),
    ("no sidecar", "sidecar.exists", "agents/openai.yaml", None, {"text": USER_ONLY_SKILL}),
    ("sidecar allows implicit", "sidecar.implicit-invocation-off", "agents/openai.yaml", None,
     {"text": USER_ONLY_SKILL, "files": {"agents/openai.yaml": "policy:\n  allow_implicit_invocation: true\n"}}),
    ("user-only model", "model-policy.user-only", "SKILL.md", 5,
     {"text": skill(USER_ONLY + "model: sonnet\n"), "files": {"agents/openai.yaml": SIDECAR}}),
    ("user-only effort", "model-policy.user-only", "SKILL.md", 5,
     {"text": skill(USER_ONLY + "effort: low\n"), "files": {"agents/openai.yaml": SIDECAR}}),
    ("model-invoked without effort", "model-policy.model-invoked", "SKILL.md", None,
     {"text": skill("model: sonnet\n")}),
    ("large body", "body.token-estimate", "SKILL.md", None, {"text": skill(body=BODY + "x" * 20004 + "\n")}),
    ("nested reference", "references.nested", "references/guide.md", 3,
     {"files": {"references/guide.md": NESTED, "references/other.md": "# Other\n"},
      "text": skill(body=BODY + "- `references/other.md` — read when needed.\n")}),
    ("orphan", "references.orphan", "references/extra.md", None, {"files": {"references/extra.md": "# Extra\n"}}),
    ("no trigger", "references.read-trigger", "references/guide.md", 14,
     {"text": skill(body=BODY.replace(" — read when a run needs the guide.", ""))}),
    ("arguments", "body.arguments-variable", "SKILL.md", 15, {"text": skill(body=BODY + "Use $ARGUMENTS here.\n")}),
    ("skill dir", "body.skill-dir-variable", "SKILL.md", 15,
     {"text": skill(body=BODY + "Run ${CLAUDE_SKILL_DIR}/scripts/tool.py.\n")}),
    ("file mention", "body.file-mention", "SKILL.md", 15, {"text": skill(body=BODY + "Load @notes.md now.\n")}),
    ("uninvoked script", "scripts.invocation-line", "scripts/extra.sh", None, {"files": {"scripts/extra.sh": "true\n"}}),
]


@pytest.mark.parametrize(("check", "path", "line", "setup"), [case[1:] for case in CASES], ids=[c[0] for c in CASES])
def test_each_check_fails_on_its_fixture(check: str, path: str, line: int | None, setup: dict[str, object],
                                         tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = make(tmp_path, cast(str | None, setup.get("text")), cast(dict[str, str | None] | None, setup.get("files")),
                   cast(str, setup.get("directory", "demo")))
    checks = run(package, capsys)
    hit = find(checks, check, path)
    assert (hit["status"], hit["line"]) == ("fail", line)
    assert [c["id"] for c in checks if c["status"] == "fail" and c["id"] != check] == []


def test_user_only_package_with_sidecar_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    checks = run(make(tmp_path, USER_ONLY_SKILL, {"agents/openai.yaml": SIDECAR}), capsys)
    assert [c for c in checks if c["status"] == "fail"] == []
    assert find(checks, "sidecar.implicit-invocation-off")["line"] == 2
    assert find(checks, "model-policy.model-invoked")["status"] == "not-applicable"


def test_empty_package_reports_the_missing_skill_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = tmp_path / "empty"
    package.mkdir()
    checks = run(package, capsys)
    assert [(c["id"], c["status"]) for c in checks] == [("package.skill-file", "fail")]


def test_missing_frontmatter_is_one_failed_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    checks = run(make(tmp_path, "# no frontmatter\n"), capsys)
    assert [(c["id"], c["status"]) for c in checks] == [("package.frontmatter", "fail")]


def test_output_is_sorted_and_byte_identical(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = make(tmp_path, files={"references/b.md": "b", "references/a.md": "a", "scripts/z.sh": "z"})
    assert main(["audit-facts", str(package)]) == 0
    first = capsys.readouterr().out
    assert main(["audit-facts", str(package)]) == 0
    assert capsys.readouterr().out == first
    checks = cast(Checks, json.loads(first)["checks"])
    keys = [(c["path"], c["id"], -1 if c["line"] is None else c["line"]) for c in checks]
    assert keys == sorted(keys)


def _fails(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    assert main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    return cast(str, json.loads(captured.err)["error"]).removeprefix("ValueError: ")


def test_invalid_inputs_exit_one_with_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = make(tmp_path)
    assert _fails(["audit-facts", str(tmp_path / "missing")], capsys) == "input must be a directory"
    assert _fails(["audit-facts", str(package / "SKILL.md")], capsys) == "input must be a directory"
    link = tmp_path / "link"
    link.symlink_to(package)
    assert _fails(["audit-facts", str(link)], capsys) == "input must not be a symlink"
    (package / "references/alias.md").symlink_to(package / "SKILL.md")
    assert _fails(["audit-facts", str(package)], capsys) == "package must not contain symlinks"


def _repository(root: Path, rows: str) -> None:
    (root / ".git").mkdir(parents=True)
    _ = (root / "README.md").write_text(f"# Repo\n\n## Skills\n\n| Skill path | Command |\n| --- | --- |\n{rows}\n\n## Other\n")


def test_registration_checks_the_readme_skills_row(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _repository(tmp_path, "| `skills/demo/SKILL.md` | `/demo` |")
    package = make(tmp_path / "skills", directory="demo")
    hit = find(run(package, capsys), "registration.readme-row")
    assert (hit["status"], hit["line"], hit["path"]) == ("pass", 7, "../../README.md")
    assert find(run(package, capsys), "repo-local.internal-metadata")["status"] == "not-applicable"
    _ = (tmp_path / "README.md").write_text("## Skills\n\n| Skill path |\n| --- |\n| `skills/other/SKILL.md` |\n")
    assert find(run(package, capsys), "registration.readme-row")["status"] == "fail"
    _ = (tmp_path / "README.md").write_text("# Repo\n")
    assert find(run(package, capsys), "registration.readme-row")["status"] == "not-applicable"


def test_repo_local_skill_needs_internal_metadata_and_a_resolving_symlink(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _repository(tmp_path, "")
    text = skill("metadata:\n  author: me\n  internal: true\n")
    package = make(tmp_path / ".agents/skills", text)
    checks = run(package, capsys)
    assert find(checks, "registration.readme-row")["status"] == "not-applicable"
    assert find(checks, "repo-local.internal-metadata")["line"] == 6
    assert find(checks, "repo-local.claude-symlink")["status"] == "fail"
    (tmp_path / ".claude/skills").mkdir(parents=True)
    link = tmp_path / ".claude/skills/demo"
    link.symlink_to("../../.agents/skills/demo")
    checks = run(package, capsys)
    assert find(checks, "repo-local.claude-symlink")["status"] == "pass"
    assert find(checks, "repo-local.internal-metadata")["status"] == "pass"
    link.unlink()
    link.symlink_to("../../.agents/skills/missing")
    assert find(run(package, capsys), "repo-local.claude-symlink")["status"] == "fail"
    _ = (package / "SKILL.md").write_text(skill())
    assert find(run(package, capsys), "repo-local.internal-metadata")["status"] == "fail"


def test_repository_checks_are_not_applicable_outside_a_repository(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    checks = run(make(tmp_path / ".agents/skills"), capsys)
    for check in ("registration.readme-row", "repo-local.internal-metadata", "repo-local.claude-symlink"):
        assert find(checks, check)["status"] == "not-applicable"


def test_allowed_keys_match_the_harness_layout_matrix() -> None:
    text = LAYOUT.read_text()
    matrix = re.search(r"^## Frontmatter matrix.*?(?=^## )", text, re.M | re.S)
    assert matrix is not None
    keys: set[str] = set()
    for row in matrix[0].splitlines()[4:]:
        cell = row.strip("|").split("|")[0]
        spans = cast(list[str], re.findall(r"`([^`]+)`", cell))
        keys |= {span.split(":")[0].split(" ")[0] for span in spans if not span.startswith("$")}
    assert keys == ALLOWED_KEYS


LONG = "d" * 1200
DESCRIPTION_VALUES = [
    ("plain continuation", f"Short first line\n  {LONG}", "fail"),
    ("quoted continuation", f"\"Short first line\n  {LONG}\"", "fail"),
    ("folded block", f">\n  Short first line\n  {LONG}", "fail"),
    ("literal block", f"|\n  Short first line\n  {LONG}", "fail"),
    ("short quoted continuation", "\"Does a demo thing.\n  Use when asked.\"", "pass"),
    ("short block", ">\n  Does a demo thing.\n  Use when asked.", "pass"),
    ("commented plain", f"Does a demo thing. # {LONG}", "pass"),
]


@pytest.mark.parametrize(("value", "status"), [case[1:] for case in DESCRIPTION_VALUES],
                         ids=[case[0] for case in DESCRIPTION_VALUES])
def test_description_length_counts_every_scalar_style(value: str, status: str, tmp_path: Path,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    checks = run(make(tmp_path, skill(description=value)), capsys)
    hit = find(checks, "description.length")
    assert (hit["status"], hit["line"]) == (status, 3)


def test_inline_comment_does_not_hide_the_user_only_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = skill("disable-model-invocation: true # user only\n")
    checks = run(make(tmp_path, text, {"agents/openai.yaml": SIDECAR}), capsys)
    assert find(checks, "model-policy.model-invoked")["status"] == "not-applicable"
    assert find(checks, "sidecar.exists")["status"] == "pass"


def test_backtick_mention_of_another_reference_is_nested(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files: dict[str, str | None] = {"references/guide.md": "# Guide\n\nSee `references/other.md`.\n",
                                    "references/other.md": "# Other\n"}
    text = skill(body=BODY + "- `references/other.md` — read when needed.\n")
    hit = find(run(make(tmp_path, text, files), capsys), "references.nested", "references/guide.md")
    assert (hit["status"], hit["line"]) == ("fail", 3)


VARIABLES = "body.arguments-variable", "body.skill-dir-variable", "body.file-mention"
CODE_CASES = [
    ("fenced decorator", "```python\n@app.command\n```\n", "pass pass pass"),
    ("fenced command", "```bash\npython3 ${CLAUDE_SKILL_DIR}/scripts/x.py $ARGUMENTS\n```\n", "fail fail pass"),
    ("fenced file mention", "```\nLoad @notes.md\n```\n", "pass pass pass"),
    ("span with other text", "Run `${CLAUDE_SKILL_DIR}/scripts/x.py` and `$ARGUMENTS now`.\n", "fail fail pass"),
    ("bare token spans", "Never use `$ARGUMENTS`, `${CLAUDE_SKILL_DIR}`, or `@notes.md`.\n", "pass pass pass"),
    ("decorator and email", "Decorate with @app.command, @org/team, @anthropic-ai/sdk, me@example.md.\n", "pass pass pass"),
    ("relative file mention", "Load @./notes and @~/x/y and @docs/a.md.\n", "pass pass fail"),
    ("unclosed fence", "```\n@notes.md\n$ARGUMENTS\n", "fail pass pass"),
]


@pytest.mark.parametrize(("addition", "statuses"), [case[1:] for case in CODE_CASES], ids=[c[0] for c in CODE_CASES])
def test_body_variable_checks_by_code_context(addition: str, statuses: str, tmp_path: Path,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    checks = run(make(tmp_path, skill(body=BODY + addition)), capsys)
    assert [find(checks, check)["status"] for check in VARIABLES] == statuses.split()


def test_file_mention_after_a_closed_fence_still_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = skill(body=BODY + "```\ncode\n```\nLoad @docs/notes.md now.\n")
    assert find(run(make(tmp_path, text), capsys), "body.file-mention")["line"] == 18


def test_only_top_level_visible_files_are_scripts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files: dict[str, str | None] = {"scripts/.gitkeep": "", "scripts/lib/util.py": "x = 1\n"}
    checks = run(make(tmp_path, files=files), capsys)
    assert [c["id"] for c in checks if c["status"] == "fail"] == []
    assert [c["path"] for c in checks if c["id"] == "scripts.invocation-line"] == ["scripts/tool.py"]


def test_orphan_check_matches_the_path_as_a_token(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = "# demo\n\nRun `python3 scripts/tool.py`.\nSee ../other/references/guide.md.\n"
    assert find(run(make(tmp_path, skill(body=body)), capsys), "references.orphan")["status"] == "fail"
    body = "# demo\n\nRun `python3 scripts/tool.py`.\nSee ./references/guide.md.\n"
    assert find(run(make(tmp_path / "again", skill(body=body)), capsys), "references.orphan")["status"] == "pass"


@pytest.mark.parametrize(("entry", "status"), [
    ("- `references/guide.md` — the guide.", "fail"),
    ("- `references/guide.md` — read when a run needs it.", "pass"),
    ("- `references/guide.md`: Portability lens fires.", "pass"),
    ("- `references/guide.md`", "fail"),
])
def test_read_trigger_needs_a_trigger_word(entry: str, status: str, tmp_path: Path,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    body = BODY.split("- `references")[0] + entry + "\n"
    hit = find(run(make(tmp_path, skill(body=body)), capsys), "references.read-trigger")
    assert hit["status"] == status


def test_dot_dot_input_uses_the_resolved_directory_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = make(tmp_path)
    (package / "agents").mkdir()
    assert main(["audit-facts", str(package / "agents" / "..")]) == 0
    checks = cast(Checks, json.loads(capsys.readouterr().out)["checks"])
    assert find(checks, "name.matches-directory")["status"] == "pass"


def test_flow_style_metadata_sets_internal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _repository(tmp_path, "")
    package = make(tmp_path / ".agents/skills", skill("metadata: {author: me, internal: true}\n"))
    hit = find(run(package, capsys), "repo-local.internal-metadata")
    assert (hit["status"], hit["line"]) == ("pass", 4)


def test_commented_flags_still_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _repository(tmp_path, "")
    text = skill("disable-model-invocation: true\nmetadata:\n  internal: true  # repo only\n")
    sidecar = "policy:\n  allow_implicit_invocation: false # user only\n"
    checks = run(make(tmp_path / ".agents/skills", text, {"agents/openai.yaml": sidecar}), capsys)
    assert find(checks, "sidecar.implicit-invocation-off")["status"] == "pass"
    assert find(checks, "repo-local.internal-metadata")["status"] == "pass"


@pytest.mark.parametrize("mention", ["skills/demo/references/guide.md", "${CLAUDE_SKILL_DIR}/references/guide.md"])
def test_orphan_check_accepts_the_skill_prefixes(mention: str, tmp_path: Path,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    _repository(tmp_path, "")
    body = f"# demo\n\nRun `python3 scripts/tool.py`.\nSee {mention}.\n"
    package = make(tmp_path / "skills", skill(body=body))
    assert find(run(package, capsys), "references.orphan")["status"] == "pass"


def test_nested_check_ignores_another_skills_reference(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files: dict[str, str | None] = {"references/guide.md": "See `skills/age/references/other.md`.\n",
                                    "references/other.md": "# Other\n"}
    text = skill(body=BODY + "- `references/other.md` — read when needed.\n")
    assert find(run(make(tmp_path, text, files), capsys), "references.nested", "references/guide.md")["status"] == "pass"


def test_comment_line_in_a_plain_description_is_not_counted(tmp_path: Path,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    text = skill(description=f"Does a demo thing.\n  # {LONG}\n  Use when asked.")
    assert find(run(make(tmp_path, text), capsys), "description.length")["status"] == "pass"


@pytest.mark.parametrize(("entry", "status"), [
    ("- `references/guide.md` — When a run needs depth, read `references/guide.md`.", "pass"),
    ("- `references/guide.md` — the run book.", "fail"),
    ("- `references/guide.md` — `audit`; use the guide.", "pass"),
    ("- `references/guide.md` — the confidence kernel.", "fail"),
])
def test_read_trigger_needs_a_condition_or_clause_start_verb(entry: str, status: str, tmp_path: Path,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    body = BODY.split("- `references")[0] + entry + "\n"
    hit = find(run(make(tmp_path, skill(body=body)), capsys), "references.read-trigger")
    assert hit["status"] == status

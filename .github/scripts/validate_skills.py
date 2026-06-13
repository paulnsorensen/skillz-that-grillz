#!/usr/bin/env python3
"""Validate every SKILL.md in the repository.

Per-file checks:
- Lives at exactly skills/<name>/SKILL.md (no scope, no nested sub-skills).
- Begins with a YAML frontmatter block (--- ... ---). CRLF and missing
  trailing newline are tolerated.
- Frontmatter parses as a YAML mapping.
- Required keys present and non-empty: name, description.
- Only spec-allowed keys (plus Claude Code extensions) are present.
- name is kebab-case, 1-64 chars, no leading/trailing/consecutive hyphens.
- name matches the parent directory name.
- description is at most 1024 characters (Codex enforces this limit).
- Body carries no Claude-only tool names (AskUserQuestion, TodoWrite) or
  harness-coupled CLI fragments (`claude mcp add`, `/plugin`) outside an
  explicitly Claude-Code-scoped section — skill bodies are portable.

Exit 0 on success, 1 on any failure.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ALLOWED_KEYS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
    "version",
    "argument-hint",
    "disable-model-invocation",
    "user-invocable",
    # model and context are Claude-Code-only frontmatter keys; tolerated here
    # because frontmatter is harness-scoped metadata, not portable body logic.
    "model",
    "context",
    "agent",
    "hooks",
}

NAME_RE = re.compile(r"^(?!-)(?!.*--)[a-z0-9-]{1,64}(?<!-)$")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\s*(\r?\n|\Z)", re.DOTALL)
DESCRIPTION_MAX_LEN = 1024

# Claude-only tool names and harness-coupled fragments that break portability
# when baked into a skill BODY as instruction logic. They are allowed only
# inside an explicitly per-harness ("Claude Code"-scoped) section, where the
# coupling is deliberate and labelled. Each entry maps a compiled pattern to a
# human label for the error message.
#   - AskUserQuestion / TodoWrite: Claude-Code-only tools; other harnesses
#     have no such tool, so body logic that names them silently no-ops.
#   - `claude mcp add`: a Claude-Code CLI invocation.
#   - `/plugin`: a Claude-Code slash command. The negative lookahead keeps
#     documentation paths like `/reference/plugins/` and `/plugin-dev` from
#     matching — only the bare command does.
HARNESS_COUPLED_PATTERNS = [
    (re.compile(r"\bAskUserQuestion\b"), "AskUserQuestion (Claude-only tool)"),
    (re.compile(r"\bTodoWrite\b"), "TodoWrite (Claude-only tool)"),
    (re.compile(r"claude\s+mcp\s+add"), "`claude mcp add` (Claude Code CLI)"),
    (re.compile(r"/plugin(?![\w/-])"), "`/plugin` (Claude Code slash command)"),
]

# A markdown heading is treated as explicitly per-harness when its TEXT carries
# one of these labels — e.g. "## Claude Code only", "### Claude-only fallback".
# A bare mention of "Claude" is deliberately NOT enough: the scope must be
# unambiguous so the coupling reads as intentional. The heading match stays
# loose because a heading naming Claude Code is, by construction, a scope label.
CLAUDE_SCOPE_HEADING_RE = re.compile(r"claude[ -]code|claude[ -]only", re.IGNORECASE)

# The per-LINE exemption (a coupled token allowed because the line itself scopes
# to Claude Code) is anchored to a lead-in phrase: the scope label must OPEN the
# line — "On Claude Code, ...", "Claude Code only: ...", "Claude-only fallback,".
# An optional leading list marker / blockquote (`- `, `* `, `> `) is tolerated so
# a bulleted per-harness clause still qualifies. The anchor matters: without it a
# bare `claude-code` substring appearing for an UNRELATED reason — e.g. the Serena
# context NAME `claude-code` in a config table — would silently exempt that line
# from the body gate, the exact whitewash class this gate exists to stop.
CLAUDE_SCOPE_LINE_RE = re.compile(
    r"(?im)^\s{0,3}(?:[-*>]\s+)?(?:on\s+)?claude[ -](?:code|only)\b[ ,:-]"
)
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")


def validate_path_shape(path: Path) -> str | None:
    parts = path.parts
    if len(parts) != 3 or parts[0] != "skills" or parts[2] != "SKILL.md":
        return (
            f"{path}: file is not at the documented path skills/<name>/SKILL.md "
            f"(nested sub-skills are not supported)"
        )
    return None


def validate_frontmatter(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return [f"{path}: missing or malformed YAML frontmatter (expected leading --- ... ---)"]

    try:
        fm = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return [f"{path}: invalid YAML frontmatter: {exc}"]

    if not isinstance(fm, dict):
        return [f"{path}: frontmatter must be a YAML mapping"]

    errors: list[str] = []
    name = fm.get("name")
    description = fm.get("description")

    if not name:
        errors.append(f"{path}: missing required key 'name'")
    elif not isinstance(name, str):
        errors.append(f"{path}: 'name' must be a string")
    else:
        if not NAME_RE.match(name):
            errors.append(
                f"{path}: name '{name}' is not kebab-case "
                f"(1-64 chars, lowercase a-z 0-9, no leading/trailing/consecutive hyphens)"
            )
        if name != path.parent.name:
            errors.append(
                f"{path}: name '{name}' does not match parent directory '{path.parent.name}'"
            )

    if not description:
        errors.append(f"{path}: missing required key 'description'")
    elif not isinstance(description, str) or not description.strip():
        errors.append(f"{path}: 'description' must be a non-empty string")
    elif len(description) > DESCRIPTION_MAX_LEN:
        errors.append(
            f"{path}: 'description' is {len(description)} characters; "
            f"max is {DESCRIPTION_MAX_LEN} (Codex limit)"
        )

    extra = set(fm) - ALLOWED_KEYS
    if extra:
        errors.append(f"{path}: disallowed frontmatter keys: {sorted(extra)}")

    return errors


def validate_body(path: Path) -> list[str]:
    """Reject Claude-only tools and harness-coupled CLI fragments in the body.

    A skill body is portable instruction text consumed by every harness
    (Claude Code, Codex, opencode). Naming a Claude-only tool or CLI there
    silently breaks on the others. Such tokens are allowed only inside a
    section whose heading explicitly scopes to Claude Code, or on a line whose
    leading text scopes to Claude Code (a "On Claude Code, ..." / "Claude Code
    only: ..." lead-in — not a bare `claude-code` substring elsewhere on the
    line, which may be an unrelated context/config name).
    """
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    body = text[match.end():] if match else text

    errors: list[str] = []
    section_is_scoped = False
    for line in body.splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            section_is_scoped = bool(CLAUDE_SCOPE_HEADING_RE.search(heading.group(2)))
            continue
        if section_is_scoped or CLAUDE_SCOPE_LINE_RE.search(line):
            continue
        for pattern, label in HARNESS_COUPLED_PATTERNS:
            if pattern.search(line):
                errors.append(
                    f"{path}: body uses {label} outside a Claude-Code-scoped "
                    f"section — skill bodies must be harness-neutral"
                )
    return errors


def validate(path: Path) -> list[str]:
    shape_error = validate_path_shape(path)
    if shape_error:
        return [shape_error]
    return validate_frontmatter(path) + validate_body(path)


def main() -> int:
    if not Path("skills").is_dir():
        print("ERROR: skills/ directory not found", file=sys.stderr)
        return 1

    skill_files = sorted(
        p for p in Path(".").rglob("SKILL.md")
        if not any(part.startswith(".") for part in p.parts)
    )
    if not skill_files:
        print("ERROR: no SKILL.md files found in repository", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    for sf in skill_files:
        all_errors.extend(validate(sf))

    if all_errors:
        for e in all_errors:
            print(f"ERROR: {e}", file=sys.stderr)
        print(
            f"\nFAIL: {len(all_errors)} error(s) across {len(skill_files)} SKILL.md file(s)",
            file=sys.stderr,
        )
        return 1

    print(f"OK: validated {len(skill_files)} SKILL.md file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

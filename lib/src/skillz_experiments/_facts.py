"""Report fixed rubric checks for a skill directory as facts, without grading it."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

SCHEMA_VERSION = 1
MAX_BYTES = 262144
TOKEN_BUDGET = 5000
NAME_LIMIT = 64
DESCRIPTION_LIMIT = 1024
LOCAL_ROOT = ".agents/skills"
# Spec fields plus the Claude extensions; a test pins this set to the matrix in harness-layout.md.
ALLOWED_KEYS = frozenset({
    "name", "description", "license", "compatibility", "metadata", "allowed-tools", "disallowed-tools",
    "disable-model-invocation", "user-invocable", "argument-hint", "arguments", "model", "effort", "context",
    "agent", "background", "hooks", "paths", "shell", "when_to_use",
})
TOP = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)$")
KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK = re.compile(r"\[[^\]]*\]\(([^\s)]+)\)")
SEPARATOR = re.compile(r"—|–|--|\s-\s|:(?=\s)")
CONDITION = re.compile(
    r"\b(?:when|whenever|if|once|until|while|before|after|only|fires?|needs?|absent|selects?|flags?|opts?)\b", re.I)
START_VERB = re.compile(r"(?:^|[;.])[\s`]*(?:read|load|use|run|open|consult)\b", re.I)
COMMENT = re.compile(r"\s+#.*$")
QUOTED = re.compile(r"^([\"'])(.*)\1\s*(?:#.*)?$", re.S)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
SPAN = re.compile(r"(`{1,16}).+?\1")
FLOW_INTERNAL = re.compile(r"[{,]\s*internal:\s*[\"']?true[\"']?\s*[,}]")
ARGUMENTS = re.compile(r"\$ARGUMENTS\b")
SKILL_DIR = re.compile(r"\$\{CLAUDE_SKILL_DIR\}")
FILE_MENTION = re.compile(
    r"(?<![\w@])@(?:file\b|(?:\.{1,2}|~)/[\w./-]+|[\w-]+(?:/[\w.-]+)*\.(?:md|txt|json|ya?ml|toml|py|sh|js|ts|csv)\b)"
)
IMPLICIT_OFF = re.compile(r"^\s*allow_implicit_invocation:\s*false\s*(?:#.*)?$")
INTERNAL = re.compile(r"^internal:\s*[\"']?true[\"']?\s*(?:#.*)?$")
BARE_VARIABLE = re.compile(r"\s*(?:\$ARGUMENTS|\$\{CLAUDE_SKILL_DIR\})\s*")

PASS, FAIL, NOT_APPLICABLE = "pass", "fail", "not-applicable"


@dataclass
class _Field:
    line: int
    value: str
    block: list[tuple[int, str]] = field(default_factory=lambda: [])

    def text(self) -> str:
        if self.value[:1] in (">", "|"):
            return ("\n" if self.value[0] == "|" else " ").join(text for _, text in self.block)
        parts = [self.value, *(text for _, text in self.block if self.value[:1] in ("'", '"') or not text.startswith("#"))]
        if self.value[:1] in ("'", '"'):
            joined = " ".join(parts)
            quoted = QUOTED.match(joined)
            return quoted[2] if quoted else joined
        return " ".join(part for part in (COMMENT.sub("", part) for part in parts) if part)


@dataclass
class _Context:
    checks: list[dict[str, object]] = field(default_factory=lambda: [])

    def add(self, check: str, rule: str, path: str, line: int | None, status: str, detail: str) -> None:
        self.checks.append({"id": check, "rule": rule, "path": path, "line": line, "status": status,
                            "detail": detail})


def _read(path: Path) -> list[str]:
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{path} must be a regular file of at most {MAX_BYTES} bytes")
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        raise ValueError(f"{path} must be UTF-8 text") from None


def _frontmatter(lines: list[str], end: int) -> dict[str, _Field]:
    fields: dict[str, _Field] = {}
    current: _Field | None = None
    for number, line in enumerate(lines[1:end], 2):
        match = TOP.match(line)
        if match:
            current = _Field(number, match[2].strip())
            fields[match[1]] = current
        elif line.strip() and line[0] in " \t" and current is not None:
            current.block.append((number, line.strip()))
    return fields


def _first(lines: list[str], offset: int, *patterns: re.Pattern[str]) -> tuple[int, str] | None:
    for number, line in enumerate(lines, offset + 1):
        for pattern in patterns:
            hit = pattern.search(line)
            if hit:
                return number, hit[0]
    return None


def _identity(context: _Context, fields: dict[str, _Field], directory: str) -> None:
    name = fields.get("name")
    value = name.text() if name else ""
    line = name.line if name else None
    context.add("name.matches-directory", "layout.name", "SKILL.md", line,
                PASS if value == directory else FAIL, f"name `{value}`, directory `{directory}`")
    valid = bool(KEBAB.match(value)) and len(value) <= NAME_LIMIT
    context.add("name.format", "layout.name", "SKILL.md", line, PASS if valid else FAIL,
                f"kebab-case, at most {NAME_LIMIT} characters; got {len(value)}")
    description = fields.get("description")
    size = len(description.text()) if description else 0
    context.add("description.length", "invocation", "SKILL.md", description.line if description else None,
                PASS if 0 < size <= DESCRIPTION_LIMIT else FAIL, f"{size} of at most {DESCRIPTION_LIMIT} characters")
    unknown = sorted(set(fields) - ALLOWED_KEYS)
    first = min((fields[key].line for key in unknown), default=None)
    context.add("frontmatter.known-keys", "portability.1", "SKILL.md", first, FAIL if unknown else PASS,
                "unknown keys: " + ", ".join(unknown) if unknown else "all keys are spec or Claude fields")


def _policy(context: _Context, fields: dict[str, _Field], package: Path, user_only: bool) -> None:
    flag = fields.get("disable-model-invocation")
    sidecar = package / "agents/openai.yaml"
    exists = sidecar.is_file()
    context.add("sidecar.exists", "portability.3", "agents/openai.yaml", None,
                NOT_APPLICABLE if not user_only else PASS if exists else FAIL,
                "skill is model-invoked" if not user_only else "sidecar present" if exists else "sidecar missing")
    off = _first(_read(sidecar), 0, IMPLICIT_OFF) if user_only and exists else None
    context.add("sidecar.implicit-invocation-off", "portability.3", "agents/openai.yaml", off[0] if off else None,
                NOT_APPLICABLE if not (user_only and exists) else PASS if off else FAIL,
                "needs a user-only skill with a sidecar" if not (user_only and exists)
                else "allow_implicit_invocation is false" if off else "allow_implicit_invocation: false not found")
    extras = sorted(key for key in ("model", "effort") if key in fields)
    context.add("model-policy.user-only", "portability.10", "SKILL.md",
                min((fields[key].line for key in extras), default=flag.line if flag else None),
                NOT_APPLICABLE if not user_only else FAIL if extras else PASS,
                "skill is model-invoked" if not user_only
                else "user-only skill sets " + ", ".join(extras) if extras else "user-only skill sets neither")
    missing = sorted(key for key in ("model", "effort") if key not in fields)
    context.add("model-policy.model-invoked", "portability.10", "SKILL.md", None,
                NOT_APPLICABLE if user_only else FAIL if missing else PASS,
                "skill is user-only" if user_only
                else "model-invoked skill lacks " + ", ".join(missing) if missing else "model and effort are set")


def _code_free(lines: list[str]) -> list[str]:
    """Blank fenced blocks and backtick spans, so a mention inside code is not a use."""
    masked: list[str] = []
    fence = ""
    for line in lines:
        opener = FENCE.match(line)
        if fence:
            masked.append("")
            if opener and opener[1][0] == fence[0] and len(opener[1]) >= len(fence) and not opener[2].strip():
                fence = ""
        elif opener:
            fence = opener[1]
            masked.append("")
        else:
            masked.append(SPAN.sub(lambda hit: " " * len(hit[0]), line))
    return masked


def _names(text: str, relative: str, prefix: str = "") -> re.Match[str] | None:
    prefixes = "|".join([r"\./", r"\$\{CLAUDE_SKILL_DIR\}/", *([re.escape(prefix)] if prefix else [])])
    return re.search(rf"(?<![\w./-])(?:{prefixes})?{re.escape(relative)}(?![\w-]|\.\w)", text)


def _blank_bare_variable(hit: re.Match[str]) -> str:
    ticks = len(hit[1])
    return " " * len(hit[0]) if BARE_VARIABLE.fullmatch(hit[0][ticks:-ticks]) else hit[0]


def _body(context: _Context, body: list[str], offset: int) -> None:
    size = len("\n".join(body).encode("utf-8")) // 4
    context.add("body.token-estimate", "information-hierarchy.9", "SKILL.md", None,
                PASS if size <= TOKEN_BUDGET else FAIL, f"~{size} tok of {TOKEN_BUDGET}")
    plain = [SPAN.sub(_blank_bare_variable, line) for line in body]
    masked = _code_free(body)
    for check, rule, pattern, lines in (("body.arguments-variable", "portability.4", ARGUMENTS, plain),
                                        ("body.skill-dir-variable", "portability.5", SKILL_DIR, plain),
                                        ("body.file-mention", "portability.6", FILE_MENTION, masked)):
        hit = _first(lines, offset, pattern)
        context.add(check, rule, "SKILL.md", hit[0] if hit else None, FAIL if hit else PASS,
                    f"found `{hit[1]}`" if hit else "none found")


def _references(context: _Context, package: Path, body: list[str], offset: int, prefix: str) -> None:
    files = sorted(path.relative_to(package).as_posix() for path in (package / "references").rglob("*.md")
                   if path.is_file())
    if not files:
        for check in ("references.nested", "references.orphan", "references.read-trigger"):
            context.add(check, "information-hierarchy.9", "references", None, NOT_APPLICABLE, "no references")
        return
    text = "\n".join(body)
    items = _reference_items(body, offset)
    for relative in files:
        nested = None
        for number, line in enumerate(_read(package / relative), 1):
            for link in LINK.finditer(line):
                parsed = urlsplit(link[1])
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                target = os.path.normpath(os.path.join(os.path.dirname(relative), unquote(parsed.path)))
                if target in files and target != relative and nested is None:
                    nested = (number, target)
            for other in files:
                if other != relative and nested is None and _names(line, other, prefix):
                    nested = (number, other)
        context.add("references.nested", "information-hierarchy.9", relative, nested[0] if nested else None,
                    FAIL if nested else PASS, f"names `{nested[1]}`" if nested else "names no other reference")
        linked = _names(text, relative, prefix) is not None
        context.add("references.orphan", "information-hierarchy", relative, None, PASS if linked else FAIL,
                    "SKILL.md names it" if linked else "SKILL.md does not name it")
        item = next(((number, item) for number, item in items if _names(item, relative, prefix)), None)
        head = _names(item[1], relative, prefix) if item else None
        separated = SEPARATOR.search(item[1], head.end()) if item and head else None
        clause = item[1][separated.end():] if item and separated else ""
        triggered = bool(CONDITION.search(clause) or START_VERB.search(clause))
        context.add("references.read-trigger", "information-hierarchy.9", "SKILL.md", item[0] if item else None,
                    NOT_APPLICABLE if item is None else PASS if triggered else FAIL,
                    f"`{relative}` is not listed in ## References" if item is None
                    else f"`{relative}` entry names a read trigger" if triggered
                    else f"`{relative}` entry has no read trigger")


def _reference_items(body: list[str], offset: int) -> list[tuple[int, str]]:
    items: list[tuple[int, str]] = []
    inside = False
    for number, line in enumerate(body, offset + 1):
        if line.startswith("## "):
            inside = line.strip() == "## References"
        elif inside and line.startswith(("- ", "* ")):
            items.append((number, line))
        elif inside and items and line.strip() and line[0] in " \t":
            items[-1] = (items[-1][0], items[-1][1] + " " + line.strip())
    return items


def _scripts(context: _Context, package: Path, body: list[str], offset: int, prefix: str) -> None:
    directory = package / "scripts"
    files = sorted(path.name for path in directory.iterdir() if path.is_file() and not path.name.startswith(".")) \
        if directory.is_dir() else []
    if not files:
        context.add("scripts.invocation-line", "deterministic-offload", "scripts", None, NOT_APPLICABLE, "no scripts")
    for relative in files:
        hit = next((number for number, line in enumerate(body, offset + 1)
                    if _names(line, f"scripts/{relative}", prefix)), None)
        context.add("scripts.invocation-line", "deterministic-offload", "SKILL.md", hit,
                    PASS if hit else FAIL,
                    f"SKILL.md invokes `scripts/{relative}`" if hit else f"no SKILL.md line names `scripts/{relative}`")


def _repository(context: _Context, package: Path, fields: dict[str, _Field], root: Path | None) -> None:
    relative = package.relative_to(root).as_posix() if root else ""
    local = relative.startswith(f"{LOCAL_ROOT}/")
    where = os.path.relpath(root / "README.md", package).replace(os.sep, "/") if root else "README.md"
    readme = _read(root / "README.md") if root and (root / "README.md").is_file() else []
    section = False
    row = None
    found_section = False
    for number, line in enumerate(readme, 1):
        if line.startswith("## "):
            section = line.strip() == "## Skills"
            found_section = found_section or section
        elif section and line.startswith("|") and f"{relative}/SKILL.md" in line:
            row = number
            break
    reason = ("not inside a repository" if root is None else "repo-local skill" if local
              else "README has no ## Skills section" if not found_section else "")
    context.add("registration.readme-row", "registration", where, row,
                NOT_APPLICABLE if reason else PASS if row else FAIL,
                reason or ("README row present" if row else f"no row names `{relative}/SKILL.md`"))
    reason = "not inside a repository" if root is None else "" if local else "not a repo-local skill"
    internal = _internal(fields)
    context.add("repo-local.internal-metadata", "repo-local", "SKILL.md",
                internal[0] if internal else fields["metadata"].line if "metadata" in fields else None,
                NOT_APPLICABLE if reason else PASS if internal else FAIL,
                reason or ("metadata.internal is true" if internal else "metadata.internal: true not found"))
    link = root / ".claude/skills" / package.name if root else None
    status, detail = NOT_APPLICABLE, reason
    if link is not None and not reason:
        if not link.is_symlink():
            status, detail = FAIL, "no symlink at .claude/skills/" + package.name
        elif link.resolve() != package:
            status, detail = FAIL, "symlink does not resolve to the skill"
        else:
            status, detail = PASS, "symlink resolves to the skill"
    context.add("repo-local.claude-symlink", "repo-local",
                os.path.relpath(link, package).replace(os.sep, "/") if link else ".claude/skills", None, status, detail)


def _internal(fields: dict[str, _Field]) -> tuple[int, str] | None:
    metadata = fields.get("metadata")
    if metadata is None:
        return None
    if FLOW_INTERNAL.search(metadata.value):
        return metadata.line, metadata.value
    return next(((number, text) for number, text in metadata.block if INTERNAL.match(text)), None)


def repo_root(start: Path) -> Path | None:
    """Return the nearest ancestor of `start` (or `start`) that holds `.git`, or None."""
    here = start.resolve()
    return next((path for path in (here, *here.parents) if (path / ".git").exists()), None)


def audit_facts(directory: Path) -> dict[str, object]:
    """Check one skill directory against the fixed rubric rows. Report facts only."""
    if directory.is_symlink():
        raise ValueError("input must not be a symlink")
    package = directory.resolve()
    if not package.is_dir():
        raise ValueError("input must be a directory")
    if any(path.is_symlink() for path in package.rglob("*")):
        raise ValueError("package must not contain symlinks")
    context = _Context()
    skill = package / "SKILL.md"
    if skill.is_file():
        lines = _read(skill)
        if not lines or lines[0] != "---" or "---" not in lines[1:]:
            context.add("package.frontmatter", "layout.skill-file", "SKILL.md", None, FAIL,
                        "SKILL.md needs frontmatter delimiters")
        else:
            end = lines.index("---", 1)
            fields = _frontmatter(lines, end)
            user_only = "disable-model-invocation" in fields and \
                fields["disable-model-invocation"].text().lower() == "true"
            body = lines[end + 1:]
            _identity(context, fields, package.name)
            _policy(context, fields, package, user_only)
            _body(context, body, end + 1)
            root = repo_root(package)
            prefix = package.relative_to(root).as_posix() + "/" if root else ""
            _references(context, package, body, end + 1, prefix)
            _scripts(context, package, body, end + 1, prefix)
            _repository(context, package, fields, root)
    else:
        context.add("package.skill-file", "layout.skill-file", "SKILL.md", None, FAIL, "SKILL.md is missing")
    context.checks.sort(key=lambda c: (str(c["path"]), str(c["id"]), _line_key(c["line"])))
    return {"schema_version": SCHEMA_VERSION, "input": str(directory), "checks": context.checks}


def _line_key(line: object) -> int:
    return line if isinstance(line, int) else -1

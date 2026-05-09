#!/usr/bin/env python3
"""bash-shorten — apply high-confidence rewrites from the bash-shortening skill.

Default engine is pure-local regex with conservatively-matched inputs.
When a rule could plausibly misfire, it skips — better to miss than to
corrupt quoting.

Two opt-in extensions:

- --engine sg : route the structural patterns (basename, dirname) to
  ast-grep first using the rule pack at scripts/sg-rules/, then run the
  remaining regex rules. Requires `sg` (ast-grep) on PATH.
- --include modernize : enable the off-by-default modernize rule group
  that suggests sd/rg in place of sed/grep for the most literal cases.
  Requires the target binary to be installed for the rewritten code to
  actually run.

Defaults to dry-run (prints a unified diff). Pass --apply to write.

  bash-shorten script.sh                            # dry-run, prints diff
  bash-shorten --apply script.sh                    # rewrite in place (atomic)
  bash-shorten --rules basename,dirname script.sh
  bash-shorten --skip backticks script.sh
  bash-shorten --include modernize --apply file.sh  # also rewrite sed→sd, grep→rg
  bash-shorten --engine sg script.sh                # ast-grep first, then regex
  bash-shorten --list                               # list all rules with examples
  bash-shorten --explain test-numeric               # show one rule in detail
  bash-shorten --self-test                          # run the embedded fixtures
  cat script.sh | bash-shorten -                    # stdin → stdout (no diff)

Always run `shellcheck` on the output. These rules are conservative but
not infallible; quoting edge cases at the boundary of regex matches can
still slip through. The script does not invoke shellcheck for you.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# --- Building blocks -------------------------------------------------------

_VAR = r"[A-Za-z_][A-Za-z0-9_]*"  # bash identifier
_LITERAL = r"[A-Za-z0-9_./@:+-]"  # safe literal (no regex/glob metachars)

_TEST_TO_ARITH = {
    "-eq": "==", "-ne": "!=",
    "-lt": "<",  "-le": "<=",
    "-gt": ">",  "-ge": ">=",
}


@dataclass(frozen=True)
class Rule:
    id: str
    description: str
    pattern: re.Pattern
    replace: Callable[[re.Match], str]
    source_example: str  # which article example this implements ("N/A" for bonus)
    shellcheck_id: str = ""
    notes: str = ""
    examples: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # "core" rules are enabled by default. "modernize" rules require an
    # explicit --include modernize because they assume binaries (sd, rg)
    # the user may not have installed.
    group: str = "core"
    # When --engine sg is selected and an ast-grep rule with this id
    # exists in scripts/sg-rules/, the Python rule is skipped (sg already
    # handled it). Empty means no sg equivalent — Python rule always runs.
    sg_rule_id: str = ""


# Rules that have an ast-grep equivalent in scripts/sg-rules/. When the
# sg engine handles these, the Python regex must NOT re-fire (it would
# either no-op or rewrite a partial match).
_SG_HANDLED_IDS = frozenset({"basename", "dirname"})


def _expr_op(escaped: str) -> str:
    """Strip leading backslash from `\\*` so it becomes `*` in arithmetic."""
    return escaped[1:] if escaped.startswith("\\") else escaped


# --- Tier A: rewrites of source-article examples 8, 11-15, 18, 32, 35, 37 -

RULES: list[Rule] = [
    Rule(
        id="basename",
        description='$(basename "$VAR") → ${VAR##*/}',
        pattern=re.compile(rf'\$\(\s*basename\s+"\$({_VAR})"\s*\)'),
        replace=lambda m: f"${{{m.group(1)}##*/}}",
        source_example="11",
        examples=(
            ('FILENAME=$(basename "$FULLPATH")', 'FILENAME=${FULLPATH##*/}'),
        ),
    ),
    Rule(
        id="dirname",
        description='$(dirname "$VAR") → ${VAR%/*}',
        pattern=re.compile(rf'\$\(\s*dirname\s+"\$({_VAR})"\s*\)'),
        replace=lambda m: f"${{{m.group(1)}%/*}}",
        source_example="12",
        examples=(
            ('DIR=$(dirname "$PATH_VAR")', 'DIR=${PATH_VAR%/*}'),
        ),
        notes='dirname returns "." for paths with no slash; ${VAR%/*} returns the original. Equivalent for absolute paths.',
    ),
    Rule(
        id="sed-replace-first",
        description='$(echo "$VAR" | sed \'s/PAT/REP/\') → ${VAR/PAT/REP}  (literal-only patterns)',
        pattern=re.compile(
            rf'\$\(\s*echo\s+"\$({_VAR})"\s*\|\s*'
            rf"sed\s+'s/({_LITERAL}+)/({_LITERAL}*)/'\s*\)"
        ),
        replace=lambda m: f"${{{m.group(1)}/{m.group(2)}/{m.group(3)}}}",
        source_example="13",
        notes="Skips when sed pattern contains regex metachars (bash glob ≠ sed regex).",
        examples=(
            ("X=$(echo \"$S\" | sed 's/foo/bar/')", "X=${S/foo/bar}"),
            ("X=$(echo \"$S\" | sed 's/.*/x/')", "X=$(echo \"$S\" | sed 's/.*/x/')"),  # unchanged: regex metachars
        ),
    ),
    Rule(
        id="sed-replace-all",
        description='$(echo "$VAR" | sed \'s/PAT/REP/g\') → ${VAR//PAT/REP}  (literal-only)',
        pattern=re.compile(
            rf'\$\(\s*echo\s+"\$({_VAR})"\s*\|\s*'
            rf"sed\s+'s/({_LITERAL}+)/({_LITERAL}*)/g'\s*\)"
        ),
        replace=lambda m: f"${{{m.group(1)}//{m.group(2)}/{m.group(3)}}}",
        source_example="14",
        examples=(
            ("X=$(echo \"$S\" | sed 's/old/new/g')", "X=${S//old/new}"),
        ),
    ),
    Rule(
        id="echo-wc-c",
        description='$(echo -n "$VAR" | wc -c) → ${#VAR}',
        pattern=re.compile(
            rf'\$\(\s*echo\s+-n\s+"\$({_VAR})"\s*\|\s*wc\s+-c\s*\)'
        ),
        replace=lambda m: f"${{#{m.group(1)}}}",
        source_example="15",
        notes="${#var} counts characters in your locale, not bytes. Use LANG=C wc -c for true byte count.",
        examples=(
            ('LEN=$(echo -n "$S" | wc -c)', 'LEN=${#S}'),
        ),
    ),
    Rule(
        id="expr-arith-vars",
        description='$(expr $A OP $B) → $((A OP B))  (numeric ops)',
        pattern=re.compile(
            rf'\$\(\s*expr\s+\$({_VAR})\s+(\\\*|[-+/%])\s+\$({_VAR})\s*\)'
        ),
        replace=lambda m: f"$(({m.group(1)} {_expr_op(m.group(2))} {m.group(3)}))",
        source_example="32",
        shellcheck_id="SC2003",
        examples=(
            ("R=$(expr $A + $B)", "R=$((A + B))"),
            ("R=$(expr $A \\* $B)", "R=$((A * B))"),
        ),
    ),
    Rule(
        id="expr-arith-literal",
        description='$(expr $A OP N) → $((A OP N))',
        pattern=re.compile(
            rf'\$\(\s*expr\s+\$({_VAR})\s+(\\\*|[-+/%])\s+(\d+)\s*\)'
        ),
        replace=lambda m: f"$(({m.group(1)} {_expr_op(m.group(2))} {m.group(3)}))",
        source_example="32",
        shellcheck_id="SC2003",
        examples=(
            ("C=$(expr $C + 1)", "C=$((C + 1))"),
        ),
    ),
    # combined-tests must run BEFORE test-numeric so that
    # [ $A -ge 18 ] && [ $B -eq 1 ] fuses to [[ ... && ... ]] in one pass
    # rather than each side rewriting independently to (( )).
    Rule(
        id="combined-tests",
        description='[ A ] && [ B ] → [[ A && B ]]  (single-bracket, no nesting)',
        pattern=re.compile(
            r'\[\s+([^\[\]\n]+?)\s+\]\s*&&\s*\[\s+([^\[\]\n]+?)\s+\]'
        ),
        replace=lambda m: f"[[ {m.group(1)} && {m.group(2)} ]]",
        source_example="37",
        notes="Conservative — only matches when neither bracket contains nested brackets or newlines.",
        examples=(
            ('if [ -f "$F" ] && [ -r "$F" ]; then', 'if [[ -f "$F" && -r "$F" ]]; then'),
            ('[ $A -ge 18 ] && [ $B -eq 1 ]', '[[ $A -ge 18 && $B -eq 1 ]]'),
        ),
    ),
    Rule(
        id="test-numeric",
        description='[ $V -OP N ] → (( V OP N ))  (numeric comparison in if/while)',
        pattern=re.compile(
            rf'\[\s+\$({_VAR})\s+(-eq|-ne|-lt|-le|-gt|-ge)\s+(\d+)\s+\]'
        ),
        replace=lambda m: f"(( {m.group(1)} {_TEST_TO_ARITH[m.group(2)]} {m.group(3)} ))",
        source_example="35",
        examples=(
            ("if [ $X -gt 100 ]; then", "if (( X > 100 )); then"),
            ("while [ $i -lt 10 ]; do", "while (( i < 10 )); do"),
        ),
    ),
    Rule(
        id="empty-default",
        description='if [ -z "$V" ]; then V=DEFAULT; fi → V=${V:-DEFAULT}',
        pattern=re.compile(
            rf'if\s+\[\s+-z\s+"\$({_VAR})"\s+\]\s*;\s*then\s+'
            rf'\1=([^\n;]+?)\s*;?\s*fi',
            re.MULTILINE,
        ),
        replace=lambda m: f"{m.group(1)}=${{{m.group(1)}:-{m.group(2).strip()}}}",
        source_example="8",
        notes="Only matches single-line if/then/fi where the assigned variable matches the tested one.",
        examples=(
            (
                'if [ -z "$ENV" ]; then ENV="dev"; fi',
                'ENV=${ENV:-"dev"}',
            ),
        ),
    ),
    Rule(
        id="mkdir-guard",
        description='if [ ! -d "$D" ]; then mkdir -p "$D"; fi → mkdir -p "$D"',
        pattern=re.compile(
            rf'if\s+\[\s+!\s+-d\s+"\$({_VAR})"\s+\]\s*;\s*then\s+'
            rf'mkdir\s+-p\s+"\$\1"\s*;?\s*fi',
            re.MULTILINE,
        ),
        replace=lambda m: f'mkdir -p "${{{m.group(1)}}}"',
        source_example="18",
        notes="mkdir -p is idempotent — the existence check is dead weight.",
        examples=(
            (
                'if [ ! -d "$DIR" ]; then mkdir -p "$DIR"; fi',
                'mkdir -p "${DIR}"',
            ),
        ),
    ),

    # --- Bonus patterns: not in the source article, but obvious wins ------

    Rule(
        id="backticks",
        description='`cmd` → $(cmd)  (POSIX-portable, nestable)',
        pattern=re.compile(r"`([^`\n]+)`"),
        replace=lambda m: f"$({m.group(1)})",
        source_example="N/A",
        shellcheck_id="SC2006",
        notes="Does NOT detect backticks inside single-quoted strings — review the diff.",
        examples=(
            ("VER=`git rev-parse HEAD`", "VER=$(git rev-parse HEAD)"),
        ),
    ),
    Rule(
        id="legacy-null-check",
        description='[ "x$V" = "x" ] → [ -z "$V" ]  (and "x$V" != "x" → -n)',
        pattern=re.compile(
            rf'\[\s+"x\$({_VAR})"\s+(=|!=)\s+"x"\s+\]'
        ),
        replace=lambda m: (
            f'[ -z "${{{m.group(1)}}}" ]' if m.group(2) == "="
            else f'[ -n "${{{m.group(1)}}}" ]'
        ),
        source_example="N/A",
        notes="Pre-POSIX null-check idiom from when older shells choked on bare empty comparisons. Modern bash does not need the x-prefix.",
        examples=(
            ('[ "x$VAR" = "x" ]', '[ -z "${VAR}" ]'),
            ('[ "x$VAR" != "x" ]', '[ -n "${VAR}" ]'),
        ),
    ),
    Rule(
        id="empty-string-eq",
        description='[ "$V" = "" ] → [ -z "$V" ]  (and "$V" != "" → -n)',
        pattern=re.compile(
            rf'\[\s+"\$({_VAR})"\s+(=|!=)\s+""\s+\]'
        ),
        replace=lambda m: (
            f'[ -z "${{{m.group(1)}}}" ]' if m.group(2) == "="
            else f'[ -n "${{{m.group(1)}}}" ]'
        ),
        source_example="N/A",
        examples=(
            ('[ "$X" = "" ]', '[ -z "${X}" ]'),
            ('[ "$X" != "" ]', '[ -n "${X}" ]'),
        ),
    ),
    Rule(
        id="find-exec-rm-delete",
        description='find ... -exec rm {} \\; → find ... -delete  (faster, no shell re-entry)',
        pattern=re.compile(
            r'(find\s+[^\n]*?)-exec\s+rm\s+(?:-[rf]+\s+)?\{\}\s+\\;'
        ),
        replace=lambda m: f"{m.group(1)}-delete",
        source_example="N/A",
        notes='Use only when find paths are files or empty dirs. find -delete refuses non-empty dirs without -depth.',
        examples=(
            (
                'find /tmp -name "*.bak" -exec rm {} \\;',
                'find /tmp -name "*.bak" -delete',
            ),
            (
                'find . -mtime +30 -exec rm -f {} \\;',
                'find . -mtime +30 -delete',
            ),
        ),
    ),
    Rule(
        id="cat-file-pipe-grep",
        description='cat FILE | grep PAT → grep PAT FILE  (drop the useless cat)',
        # Pattern + replace are placeholders — the real work is done by
        # _apply_cat_file_grep below, because re-attaching the file as a
        # trailing arg of grep is too awkward for a single re.sub.
        pattern=re.compile(r"(?!x)x"),  # never matches
        replace=lambda m: m.group(0),    # identity — never invoked anyway
        source_example="N/A",
        shellcheck_id="SC2002",
        notes="Triggers only when the cat target is a single quoted/bare path. See _apply_cat_file_grep.",
        examples=(),  # custom logic — see _apply_cat_file_grep + _CAT_GREP_CASES below
    ),

    # --- Modernize group: opt-in via --include modernize ------------------
    #
    # These rewrite to non-coreutils binaries (sd, rg, fd) the user may
    # not have installed. They are off by default and the rewriter never
    # checks whether the target binary is on PATH — that's the user's
    # responsibility when they opt in.

    Rule(
        id="sed-replace-to-sd",
        description="echo \"$V\" | sed 's/X/Y/g' → sd 'X' 'Y' <<< \"$V\"  (literal-only; needs `sd`)",
        # Literal patterns only — sd uses regex by default but our guard
        # restricts to alphanumeric + safe chars so the conservative
        # mapping holds. Mirrors sed-replace-all's safety stance.
        pattern=re.compile(
            rf'echo\s+"\$({_VAR})"\s*\|\s*'
            rf"sed\s+'s/({_LITERAL}+)/({_LITERAL}*)/g'"
        ),
        replace=lambda m: f"sd '{m.group(2)}' '{m.group(3)}' <<< \"${m.group(1)}\"",
        source_example="N/A",
        notes='Requires `sd` (https://github.com/chmln/sd). Skips when the sed pattern contains regex metachars to avoid sd-vs-sed semantic drift.',
        group="modernize",
        examples=(
            (
                'echo "$LINE" | sed \'s/foo/bar/g\'',
                'sd \'foo\' \'bar\' <<< "$LINE"',
            ),
        ),
    ),
    Rule(
        id="grep-fixed-to-rg",
        description="grep -F PAT FILE → rg -F PAT FILE  (literal-string grep; needs `rg`)",
        # Only -F (fixed-string) grep maps cleanly. Plain `grep PAT` uses
        # BRE which differs from rg's regex flavor enough to risk silent
        # behavior changes — skip those.
        pattern=re.compile(
            r'\bgrep\s+-F\s+([^\s|;&]+)\s+([^\s|;&]+)'
        ),
        replace=lambda m: f"rg -F {m.group(1)} {m.group(2)}",
        source_example="N/A",
        notes='Requires `rg` (https://github.com/BurntSushi/ripgrep). Only -F (fixed-string) grep maps cleanly because regex flavors differ.',
        group="modernize",
        examples=(
            ('grep -F localhost /etc/hosts', 'rg -F localhost /etc/hosts'),
        ),
    ),
    Rule(
        id="find-name-to-fd",
        description='find . -type f -name "GLOB" → fd -t f "GLOB"  (gitignore-aware; needs `fd`)',
        # WARNING in description: fd respects .gitignore by default and
        # find does not. The rewrite changes behavior in repos with
        # gitignored matches. Opt-in only.
        pattern=re.compile(
            r'\bfind\s+\.\s+-type\s+f\s+-name\s+"([^"]+)"(?!\s*-)'
        ),
        replace=lambda m: f'fd -t f "{m.group(1)}"',
        source_example="N/A",
        notes='Requires `fd` (https://github.com/sharkdp/fd). BEHAVIOR DIFFERENCE: fd respects .gitignore by default; find does not. Negative lookahead skips finds that have additional flags (-mtime, -exec, etc.).',
        group="modernize",
        examples=(
            ('find . -type f -name "*.py"', 'fd -t f "*.py"'),
        ),
    ),
]

RULES_BY_ID = {r.id: r for r in RULES}


# --- Custom multi-stage rewrite for cat-file-pipe-grep --------------------
# The cat → grep rewrite needs to re-attach the file as a trailing arg of
# grep, which is awkward as a single re.sub. Handle it as a dedicated
# function and disable the simple `replace` for that rule.

_CAT_FILE_GREP = re.compile(
    r'\bcat\s+("(?:[^"\\]|\\.)*"|\'[^\']*\'|[\w./~$-]+)\s*\|\s*'
    r'grep\s+([^\n|]+?)(?=\s*$|\s*\||\s*;|\s*&&|\s*\|\|)',
    re.MULTILINE,
)


def _apply_cat_file_grep(text: str) -> tuple[str, int]:
    def repl(m: re.Match) -> str:
        return f"grep {m.group(2).rstrip()} {m.group(1)}"
    new_text, count = _CAT_FILE_GREP.subn(repl, text)
    return new_text, count


# --- Engine ---------------------------------------------------------------

# Path to the sgconfig.yml shipped alongside this script. Resolved once at
# import time so the dispatch is deterministic regardless of cwd.
_SG_CONFIG = Path(__file__).resolve().parent / "sgconfig.yml"


def _sg_available() -> bool:
    return shutil.which("sg") is not None and _SG_CONFIG.is_file()


def _apply_sg(text: str) -> tuple[str, dict[str, int]]:
    """Run the ast-grep rule pack against `text`. Returns (new_text, counts).

    Counts are keyed by the Python rule id (basename, dirname) so the
    upstream caller can present them uniformly. The sg rule ids
    (bash-shorten-basename, bash-shorten-dirname) are mapped back here.
    """
    if not _sg_available():
        return text, {}

    # sg --update-all needs a real file. Write to a temp file in the
    # current dir so atomicity holds, run sg, read back.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".sh", delete=False
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)

    try:
        # We don't capture sg's diff output — we just compare before/after
        # to count rule fires. sg's exit code is 0 even when rewrites
        # apply, so we can't use it to detect changes.
        subprocess.run(
            ["sg", "scan", "--config", str(_SG_CONFIG), "--update-all", str(tmp_path)],
            check=True,
            capture_output=True,
        )
        new_text = tmp_path.read_text(encoding="utf-8")
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass

    counts: dict[str, int] = {}
    if new_text != text:
        # Map sg-applied changes back to the Python rule ids so callers
        # see a unified report. Counting occurrences of the AFTER form
        # in `new_text` minus the BEFORE form in `text` gives the per-rule
        # delta without re-parsing sg's diff output.
        for py_id, before, after in (
            ("basename", r"\$\(\s*basename\s+\"\$" + _VAR + r"\"\s*\)", r"\$\{" + _VAR + r"##\*/\}"),
            ("dirname",  r"\$\(\s*dirname\s+\"\$"  + _VAR + r"\"\s*\)", r"\$\{" + _VAR + r"%/\*\}"),
        ):
            before_n = len(re.findall(before, text))
            after_n_old = len(re.findall(after, text))
            after_n_new = len(re.findall(after, new_text))
            delta = after_n_new - after_n_old
            if delta > 0:
                counts[py_id] = min(delta, before_n)
    return new_text, counts


def apply_rules(
    text: str,
    enabled: set[str],
    *,
    use_sg: bool = False,
) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}

    if use_sg:
        sg_text, sg_counts = _apply_sg(text)
        text = sg_text
        counts.update(sg_counts)

    for rule in RULES:
        if rule.id not in enabled:
            continue
        # Skip Python rules that ast-grep already handled, otherwise the
        # regex might misfire on the rewritten output.
        if use_sg and rule.id in _SG_HANDLED_IDS:
            continue
        if rule.id == "cat-file-pipe-grep":
            new_text, n = _apply_cat_file_grep(text)
        else:
            new_text, n = rule.pattern.subn(rule.replace, text)
        if n:
            counts[rule.id] = n
            text = new_text
    return text, counts


def _diff(before: str, after: str, label: str) -> str:
    if before == after:
        return ""
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{label} (before)",
        tofile=f"{label} (after)",
        lineterm="",
    )
    return "".join(diff)


# --- Self-test ------------------------------------------------------------

# Lines marked NOT_IN_EXAMPLES are negative cases the rule must skip.
_NEGATIVE_CASES: tuple[str, ...] = (
    # sed with regex metachars must not be touched
    "X=$(echo \"$S\" | sed 's/.*/x/')",
    "X=$(echo \"$S\" | sed 's/[abc]/x/')",
    # combined-tests with nested brackets in arg must not be touched
    '[ "${arr[0]}" = "x" ] && [ "$Y" = "y" ]',
    # mkdir-guard with a different var inside must not be touched
    'if [ ! -d "$A" ]; then mkdir -p "$B"; fi',
    # backticks inside single-quotes — current rule does NOT skip these
    # (documented limitation in the rule's notes)
)

_NEGATIVE_EXPECTED: dict[str, str] = {
    "X=$(echo \"$S\" | sed 's/.*/x/')": "X=$(echo \"$S\" | sed 's/.*/x/')",
    "X=$(echo \"$S\" | sed 's/[abc]/x/')": "X=$(echo \"$S\" | sed 's/[abc]/x/')",
    '[ "${arr[0]}" = "x" ] && [ "$Y" = "y" ]': '[ "${arr[0]}" = "x" ] && [ "$Y" = "y" ]',
    'if [ ! -d "$A" ]; then mkdir -p "$B"; fi': 'if [ ! -d "$A" ]; then mkdir -p "$B"; fi',
}

# Custom positive tests for cat-file-pipe-grep (which has no static .examples)
_CAT_GREP_CASES: tuple[tuple[str, str], ...] = (
    ("cat /etc/hosts | grep localhost", "grep localhost /etc/hosts"),
    ("cat \"$LOG\" | grep -i error", "grep -i error \"$LOG\""),
)


def self_test() -> int:
    failures: list[str] = []
    # All rules across all groups — fixtures cover both core and modernize.
    all_ids = {r.id for r in RULES}

    # Positive cases from each rule's .examples
    for rule in RULES:
        for src, want in rule.examples:
            got, _ = apply_rules(src, all_ids)
            if got != want:
                failures.append(
                    f"[{rule.id}] positive case failed\n"
                    f"  input:    {src!r}\n"
                    f"  expected: {want!r}\n"
                    f"  got:      {got!r}"
                )

    # Negative cases (must not change)
    for src in _NEGATIVE_CASES:
        want = _NEGATIVE_EXPECTED[src]
        got, _ = apply_rules(src, all_ids)
        if got != want:
            failures.append(
                f"[negative] case unexpectedly rewrote\n"
                f"  input:    {src!r}\n"
                f"  expected: {want!r}\n"
                f"  got:      {got!r}"
            )

    # cat-file-pipe-grep custom cases
    for src, want in _CAT_GREP_CASES:
        got, _ = apply_rules(src, all_ids)
        if got != want:
            failures.append(
                f"[cat-file-pipe-grep] case failed\n"
                f"  input:    {src!r}\n"
                f"  expected: {want!r}\n"
                f"  got:      {got!r}"
            )

    total = (
        sum(len(r.examples) for r in RULES)
        + len(_NEGATIVE_CASES)
        + len(_CAT_GREP_CASES)
    )
    passed = total - len(failures)
    print(f"self-test: {passed}/{total} passed")
    for f in failures:
        print(f"\nFAIL {f}", file=sys.stderr)
    return 0 if not failures else 1


# --- CLI ------------------------------------------------------------------

_KNOWN_GROUPS = frozenset({r.group for r in RULES})


def _resolve_enabled(
    only: str | None,
    skip: str | None,
    include: str | None,
) -> set[str]:
    if only:
        ids = {s.strip() for s in only.split(",") if s.strip()}
        unknown = ids - set(RULES_BY_ID)
        if unknown:
            sys.exit(f"unknown rules: {', '.join(sorted(unknown))}")
        return ids

    # Default: enable only the "core" group. --include adds opt-in groups.
    active_groups = {"core"}
    if include:
        extra = {g.strip() for g in include.split(",") if g.strip()}
        unknown_groups = extra - _KNOWN_GROUPS
        if unknown_groups:
            sys.exit(
                f"unknown groups: {', '.join(sorted(unknown_groups))} "
                f"(known: {', '.join(sorted(_KNOWN_GROUPS))})"
            )
        active_groups |= extra

    enabled = {r.id for r in RULES if r.group in active_groups}
    if skip:
        skipped = {s.strip() for s in skip.split(",") if s.strip()}
        unknown = skipped - set(RULES_BY_ID)
        if unknown:
            sys.exit(f"unknown rules: {', '.join(sorted(unknown))}")
        enabled -= skipped
    return enabled


def _list_rules() -> int:
    width = max(len(r.id) for r in RULES)
    for r in RULES:
        sc = f" [{r.shellcheck_id}]" if r.shellcheck_id else ""
        group_tag = "" if r.group == "core" else f" ({r.group})"
        print(
            f"  {r.id:<{width}}  ex {r.source_example:<3}  "
            f"{r.description}{sc}{group_tag}"
        )
    print()
    print(
        "Groups: "
        + ", ".join(
            f"{g} ({sum(1 for r in RULES if r.group == g)} rules)"
            for g in sorted(_KNOWN_GROUPS)
        )
    )
    print(
        "Default group is 'core'. Enable others with --include "
        "(e.g. --include modernize)."
    )
    return 0


def _explain(rule_id: str) -> int:
    r = RULES_BY_ID.get(rule_id)
    if r is None:
        print(f"unknown rule: {rule_id}", file=sys.stderr)
        return 1
    print(f"id:             {r.id}")
    print(f"source example: {r.source_example}")
    if r.shellcheck_id:
        print(f"shellcheck:     {r.shellcheck_id}")
    print(f"description:    {r.description}")
    print(f"pattern:        {r.pattern.pattern}")
    if r.notes:
        print(f"notes:          {r.notes}")
    if r.examples:
        print("examples:")
        for src, want in r.examples:
            print(f"  - {src}")
            print(f"      → {want}")
    return 0


def _atomic_write(path: Path, content: str) -> None:
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bash-shorten",
        description="Apply high-confidence rewrites from the bash-shortening skill.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Defaults to dry-run (prints unified diff). Pass --apply to write back.\n"
            "Always run shellcheck on the output."
        ),
    )
    ap.add_argument("file", nargs="?", help="bash script to rewrite (or '-' for stdin)")
    ap.add_argument("--apply", action="store_true", help="write the rewritten file in place")
    ap.add_argument("--rules", help="comma-separated rule IDs to apply (default: all in active groups)")
    ap.add_argument("--skip", help="comma-separated rule IDs to disable")
    ap.add_argument(
        "--include",
        help="comma-separated opt-in rule groups to enable (default: core only). "
             "Known: core, modernize.",
    )
    ap.add_argument(
        "--engine",
        choices=("regex", "sg"),
        default="regex",
        help="rewrite engine. 'regex' (default) is pure-Python; 'sg' routes "
             "the structural patterns (basename, dirname) through ast-grep "
             "first when `sg` is on PATH, then runs the regex rules.",
    )
    ap.add_argument("--list", action="store_true", help="list all rules and exit")
    ap.add_argument("--explain", metavar="ID", help="describe one rule and exit")
    ap.add_argument("--self-test", action="store_true", help="run embedded fixtures and exit")
    args = ap.parse_args(argv)

    if args.list:
        return _list_rules()
    if args.explain:
        return _explain(args.explain)
    if args.self_test:
        return self_test()

    if not args.file:
        ap.error("a file is required (or '-' for stdin)")

    enabled = _resolve_enabled(args.rules, args.skip, args.include)

    use_sg = args.engine == "sg"
    if use_sg and not _sg_available():
        print(
            "# warning: --engine sg requested but `sg` (ast-grep) is not on "
            "PATH or sgconfig.yml is missing; falling back to regex engine.",
            file=sys.stderr,
        )
        use_sg = False

    if args.file == "-":
        if args.apply:
            ap.error("--apply is incompatible with stdin")
        text = sys.stdin.read()
        new_text, counts = apply_rules(text, enabled, use_sg=use_sg)
        sys.stdout.write(new_text)
        for rid, n in counts.items():
            print(f"# applied {rid}: {n}", file=sys.stderr)
        return 0

    path = Path(args.file)
    if not path.is_file():
        sys.exit(f"not a file: {path}")
    text = path.read_text(encoding="utf-8")
    new_text, counts = apply_rules(text, enabled, use_sg=use_sg)

    if not counts:
        print(f"{path}: no rewrites applicable", file=sys.stderr)
        return 0

    if args.apply:
        _atomic_write(path, new_text)
        for rid, n in counts.items():
            print(f"{path}: applied {rid} ×{n}", file=sys.stderr)
        return 0

    print(_diff(text, new_text, str(path)))
    print(file=sys.stderr)
    for rid, n in counts.items():
        print(f"# would apply {rid}: ×{n}", file=sys.stderr)
    print("# (dry-run — pass --apply to write)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

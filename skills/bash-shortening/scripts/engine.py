"""Rewrite engine for bash-shorten.

Internal split of bash-shorten.py — not a stable API. Holds the rewrite
dispatch (`apply_rules`), the ast-grep bridge (`_apply_sg`), and the I/O
helpers (`_diff`, `_atomic_write`) that the CLI module composes. Sole
consumer is the sibling `bash-shorten.py`; tests reach the engine through
the CLI.
"""
from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rules import RULES, SG_HANDLED_IDS  # type: ignore[import-not-found]

# Used by _apply_sg's count-back regex; mirrors the constant in rules.py.
_VAR = r"[A-Za-z_][A-Za-z0-9_]*"

# Path to the sgconfig.yml shipped alongside this script. Resolved once at
# import time so the dispatch is deterministic regardless of cwd.
_SG_CONFIG = Path(__file__).resolve().parent / "sgconfig.yml"


def _sg_available() -> bool:
    return shutil.which("sg") is not None and _SG_CONFIG.is_file()


def _apply_sg(text: str) -> tuple[str, dict[str, int]]:
    """Run the ast-grep rule pack against `text`. Returns (new_text, counts).

    Counts are keyed by the Python rule id (basename, dirname) so the
    upstream caller can present them uniformly. The count "after" patterns
    below MUST mirror the `fix:` strings in scripts/sg-rules/*.yml exactly —
    if the YAML output formatting changes, the count regex will silently
    miss matches even though the rewrite still applies.

    On sg failure (parse error, rule error), emits a warning and returns
    the original text with empty counts. Non-sg-handled regex rules still
    run afterwards on the unmodified input.
    """
    if not _sg_available():
        return text, {}

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".sh", delete=False
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)

    try:
        # sg's exit code is 0 even when rewrites apply, so we compare
        # before/after byte counts to detect per-rule fires.
        try:
            subprocess.run(
                ["sg", "scan", "--config", str(_SG_CONFIG), "--update-all", str(tmp_path)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or b"").decode(errors="replace").strip()
            print(
                f"# warning: sg scan failed ({stderr or 'no stderr'}); "
                "sg-handled rules will be skipped this run.",
                file=sys.stderr,
            )
            return text, {}
        new_text = tmp_path.read_text(encoding="utf-8")
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass

    counts: dict[str, int] = {}
    if new_text != text:
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
        # backticks rewrites are counted by tracking removed backtick pairs:
        # each `\`cmd\`` → `$(cmd)` removes exactly two backticks. The
        # after-pattern `$(...)` is too generic to count directly without
        # conflating with basename/dirname output.
        btick_delta = (text.count("`") - new_text.count("`")) // 2
        if btick_delta > 0:
            counts["backticks"] = btick_delta
    return new_text, counts


def apply_rules(text: str, enabled: set[str]) -> tuple[str, dict[str, int]]:
    """Run sg-handled rules through ast-grep, then run remaining Python rules.

    sg is a hard requirement for this engine — the CLI verifies presence at
    startup. The dispatch here trusts that and skips rules whose ids are in
    SG_HANDLED_IDS so the Python regex doesn't double-fire on sg's output.
    """
    counts: dict[str, int] = {}

    sg_text, sg_counts = _apply_sg(text)
    text = sg_text
    counts.update(sg_counts)

    for rule in RULES:
        if rule.id not in enabled:
            continue
        if rule.id in SG_HANDLED_IDS:
            continue
        if rule.apply_fn is not None:
            new_text, n = rule.apply_fn(text)
        else:
            new_text, n = rule.pattern.subn(rule.replace, text)
        if n:
            counts[rule.id] = n
            text = new_text
    return text, counts


def diff(before: str, after: str, label: str) -> str:
    if before == after:
        return ""
    diff_lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{label} (before)",
        tofile=f"{label} (after)",
        lineterm="",
    )
    return "".join(diff_lines)


def atomic_write(path: Path, content: str) -> None:
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

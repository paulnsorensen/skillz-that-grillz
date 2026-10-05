#!/usr/bin/env python3
"""Fail when a /skillz reference names another reference file.

The one-level-deep rule (progressive-disclosure.md) forbids a reference that
sends the reader to another reference. This check catches Markdown links and
bare or backtick mentions of a sibling reference file name. Mentions of
`engine/references/` files are allowed: the engine is an internal data tree.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE_LINK = re.compile(r"\]\([^)]*engine/references/[^)]*\)")
ENGINE_PATH = re.compile(r"[\w./-]*engine/references/[\w./-]*\.md")


def reference_dirs(root: Path = ROOT) -> list[Path]:
    patterns = ("skills/*/references", ".agents/skills/*/references")
    return sorted(path for pattern in patterns for path in root.glob(pattern) if path.is_dir())


def find_nested(references: Path) -> list[str]:
    files = sorted(references.glob("*.md"))
    patterns = {
        target.name: re.compile(rf"(?<![\w-]){re.escape(target.name)}(?![\w-])")
        for target in files
    }
    findings = []
    for source in files:
        for number, line in enumerate(source.read_text().splitlines(), start=1):
            line = ENGINE_PATH.sub("", ENGINE_LINK.sub("", line))
            for name, pattern in patterns.items():
                if name != source.name and pattern.search(line):
                    findings.append(f"{source.name}:{number}: names {name}")
    return findings


def main(references: Path | None = None) -> int:
    if references is None:
        directories = reference_dirs()
    elif references.is_dir():
        directories = [references]
    else:
        print(f"error: references directory not found: {references}", file=sys.stderr)
        return 2
    findings = []
    for directory in directories:
        label = directory.relative_to(ROOT) if directory.is_relative_to(ROOT) else directory
        findings.extend(f"{label}/{finding}" for finding in find_nested(directory))
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

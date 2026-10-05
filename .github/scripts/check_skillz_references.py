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

REFERENCES = Path(__file__).resolve().parents[2] / "skills" / "skillz" / "references"


def find_nested(references: Path = REFERENCES) -> list[str]:
    files = sorted(references.glob("*.md"))
    findings = []
    for source in files:
        for number, line in enumerate(source.read_text().splitlines(), start=1):
            for target in files:
                if target == source:
                    continue
                pattern = rf"(?<![\w-]){re.escape(target.name)}(?![\w-])"
                if re.search(pattern, line):
                    findings.append(f"{source.name}:{number}: names {target.name}")
    return findings


def main() -> int:
    findings = find_nested()
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

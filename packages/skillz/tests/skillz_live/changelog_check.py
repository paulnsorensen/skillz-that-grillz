"""The command check of the `changelog-proof` scenario, and the oracle entry that it enforces.

The hybrid grader runs this file as `python3 -I -c <source>` in a workspace that holds the case file `change.json` at
the root and the task output under `output/`. It prints `{"score": 1}` when `output/entry.md` keeps every format rule.
Otherwise it prints `{"score": 0}` and writes one linter-style message to stderr. The message names the rule and the line.
It never repeats the summary or the scope, so it holds no long part of the expected entry.
The file uses only the standard library, because the grader runs it with no site packages.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import cast

LABELS = {"feat": "Added", "fix": "Fixed", "perf": "Improved", "docs": "Documented", "refactor": "Changed"}
TICKET = re.compile(r"[A-Z]{2,5}-[0-9]{1,5}")
SCOPE = re.compile(r"[a-z][a-z0-9-]{0,19}")
BREAKING = " **BREAKING**"
RULES = {1: "ticket id", 2: "label", 3: "scope", 4: "summary", 5: "breaking marker", 6: "one line"}
"""The rule numbers and names of the corrected `references/format.md`. The fixture plants rule 1 wrong."""


def change_problem(change: object) -> str | None:
    """Return why `change` is not a valid `change.json` object, or None when it is valid."""
    if not isinstance(change, dict):
        return "change.json is not a JSON object"
    fields = cast(dict[str, object], change)
    ticket, kind, scope = fields.get("ticket"), fields.get("type"), fields.get("scope")
    summary, breaking = fields.get("summary"), fields.get("breaking")
    if ticket is not None and not (isinstance(ticket, str) and TICKET.fullmatch(ticket)):
        return "ticket must be null or an id such as ABC-123"
    if kind not in LABELS:
        return f"type must be one of {', '.join(LABELS)}"
    if scope is not None and not (isinstance(scope, str) and SCOPE.fullmatch(scope)):
        return "scope must be null or one lowercase word"
    if not isinstance(summary, str) or not summary.strip() or "\n" in summary:
        return "summary must be one nonblank line"
    if not isinstance(breaking, bool):
        return "breaking must be true or false"
    return None


def _summary(change: dict[str, object]) -> str:
    text = str(change["summary"]).strip().rstrip(".").rstrip()
    return text[:1].upper() + text[1:]


def _head(change: dict[str, object]) -> str:
    scope = change.get("scope")
    return LABELS[str(change["type"])] + (f" in {scope}" if scope else "") + ": "


def _prefix(change: dict[str, object]) -> str:
    ticket = change.get("ticket")
    return "- " + (f"[{ticket}] " if ticket else "")


def entry_for(change: dict[str, object]) -> str:
    """Return the one correct entry line for a valid `change`, with no newline."""
    return _prefix(change) + _head(change) + _summary(change) + (BREAKING if change.get("breaking") else "")


def _fault(number: int, line: int, detail: str) -> str:
    return f"entry.md:{line}: rule {number} ({RULES[number]}): {detail}"


def verdict(change: dict[str, object], text: str | None) -> str | None:
    """Return the message of the first broken rule of the entry `text`, or None when the entry keeps every rule."""
    if text is None:
        return _fault(6, 1, "the skill wrote no output/entry.md")
    lines = text.split("\n")
    if len(lines) != 2 or lines[1] or not lines[0].strip():
        return _fault(6, min(len(lines), 2), "entry.md holds exactly one nonblank line and one trailing newline")
    line, prefix = lines[0], _prefix(change)
    if change.get("ticket") and not line.startswith(prefix):
        return _fault(1, 1, f'the entry starts with "{prefix}": the ticket id in square brackets comes first')
    if not change.get("ticket") and (not line.startswith(prefix) or TICKET.search(line)):
        return _fault(1, 1, 'a change with no ticket gives an entry that starts with "- " and holds no ticket id')
    rest, label = line[len(prefix):], LABELS[str(change["type"])]
    if not re.match(re.escape(label) + "[ :]", rest):
        return _fault(2, 1, f'the label of type {change["type"]} is "{label}", right after the ticket id')
    if not rest.startswith(_head(change)):
        return _fault(3, 1, 'the scope follows the label as "in <scope>", then a colon and a space; no scope gives no "in"')
    body = rest[len(_head(change)):]
    breaking = bool(change.get("breaking"))
    if body.endswith(BREAKING) != breaking or body.count("BREAKING") > int(breaking):
        want = f'end with "{BREAKING.strip()}"' if breaking else "hold no breaking marker"
        return _fault(5, 1, f"this entry must {want}")
    if (body.removesuffix(BREAKING) if breaking else body) != _summary(change):
        return _fault(4, 1, "the summary is the change.json summary with its first letter in upper case and no trailing period")
    return None


def main() -> None:
    change = cast(dict[str, object], json.loads(Path("change.json").read_text(encoding="utf-8")))
    entry = Path("output/entry.md")
    message = verdict(change, entry.read_text(encoding="utf-8") if entry.is_file() else None)
    if message is not None:
        print(message, file=sys.stderr)
    print(json.dumps({"score": 0 if message else 1}))


if __name__ == "__main__":
    main()

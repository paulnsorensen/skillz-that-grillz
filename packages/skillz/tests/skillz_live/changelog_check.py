"""The command check of the `changelog-proof` scenario, and the oracle text that it enforces.

The grader runs this file as `python3 -I -c <source>`. Its workspace holds the case file `changes.json` at the root
and the task output under `output/`. The check prints `{"score": 1}` only when `output/entries.md` is the oracle text:
one entry line for each change, in order. Otherwise it prints `{"score": 0}` and writes one linter-style message to
stderr. The message names the rule and the line. It never repeats the summary or the scope, so it holds no long part
of an expected entry.
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
RULES = {1: "ticket id", 2: "label", 3: "scope", 4: "summary", 5: "breaking marker", 6: "one line per change"}
"""The rule numbers and names of the corrected `references/format.md`. The fixture plants rule 1 wrong."""


def change_problem(change: object) -> str | None:
    """Return why `change` is not a valid `changes.json` object, or None when it is valid."""
    if not isinstance(change, dict):
        return "change is not a JSON object"
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


def entries_for(changes: list[dict[str, object]]) -> str:
    """Return the oracle text of `entries.md` for valid `changes`: each entry line and one newline."""
    return "".join(entry_for(change) + "\n" for change in changes)


def changes_problem(changes: object) -> str | None:
    """Return why `changes` is not a nonempty list of valid `changes.json` objects, or None when it is valid."""
    if not isinstance(changes, list) or not changes:
        return "changes.json is not a nonempty JSON list"
    for index, change in enumerate(cast(list[object], changes)):
        problem = change_problem(change)
        if problem is not None:
            return f"change {index}: {problem}"
    return None


def _fault(number: int, line: int, detail: str) -> str:
    return f"entries.md:{line}: rule {number} ({RULES[number]}): {detail}"


def _entry_fault(change: dict[str, object], line: str, at: int) -> str:
    """Return the message of the first broken rule of one entry `line` that is not the oracle entry of `change`."""
    prefix = _prefix(change)
    if change.get("ticket") and not line.startswith(prefix):
        return _fault(1, at, f'the entry starts with "{prefix}": the ticket id in square brackets comes first')
    if not change.get("ticket") and (not line.startswith(prefix) or line.startswith("- [")):
        return _fault(1, at, 'a change with no ticket gives an entry that starts with "- " and holds no ticket id')
    rest, label = line[len(prefix):], LABELS[str(change["type"])]
    if not re.match(re.escape(label) + "[ :]", rest):
        return _fault(2, at, f'the label of type {change["type"]} is "{label}", right after the ticket id')
    if not rest.startswith(_head(change)):
        return _fault(3, at, 'the scope follows the label as "in <scope>", then a colon and a space; no scope gives no "in"')
    body = rest[len(_head(change)):]
    breaking = bool(change.get("breaking"))
    if body.endswith(BREAKING) != breaking:
        want = f'end with "{BREAKING.strip()}"' if breaking else "hold no breaking marker"
        return _fault(5, at, f"this entry must {want}")
    return _fault(4, at, "the summary is the changes.json summary with its first letter in upper case and no trailing period")


def verdict(changes: list[dict[str, object]], text: str | None) -> str | None:
    """Return None when `text` is `entries_for(changes)`. Otherwise return the message of the first fault.

    The score depends only on equality with the oracle. The walk only picks the message: rule 6 for a missing file,
    a blank line, or a wrong line count, then the first differing line by rules 1, 2, 3, 5, and 4.
    """
    if text == entries_for(changes):
        return None
    if text is None:
        return _fault(6, 1, "the skill wrote no entries.md in its working directory")
    lines = text.split("\n")
    if text.endswith("\n"):
        _ = lines.pop()
    blank = next((index for index, line in enumerate(lines, 1) if not line.strip()), None)
    if blank is not None:
        return _fault(6, blank, "entries.md holds one nonblank line for each change and no other line")
    if len(lines) != len(changes):
        return _fault(6, min(len(lines), len(changes)) + 1,
                      f"entries.md holds one line for each of the {len(changes)} changes, in order")
    for index, (change, line) in enumerate(zip(changes, lines, strict=True), 1):
        if line != entry_for(change):
            return _entry_fault(change, line, index)
    return _fault(6, len(lines), "entries.md ends with one newline")


def main() -> None:
    changes = cast(object, json.loads(Path("changes.json").read_text(encoding="utf-8")))
    entries = Path("output/entries.md")
    problem = changes_problem(changes)
    message = (f"changes.json: {problem}" if problem is not None else
               verdict(cast(list[dict[str, object]], changes),
                       entries.read_text(encoding="utf-8") if entries.is_file() else None))
    if message is not None:
        print(message, file=sys.stderr)
    print(json.dumps({"score": 0 if message else 1}))


if __name__ == "__main__":
    main()

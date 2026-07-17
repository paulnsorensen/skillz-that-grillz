#!/usr/bin/env python3
"""Validate every skill eval file against one shared schema.

All skills that ship behavioural evals keep them at exactly
`skills/<name>/evals/evals.json`. This script finds every such file and
checks it against one canonical contract:

Top-level (a JSON object):
- `skill_name`: non-empty string.
- `evals`: a list of at least one entry.
- Any other top-level key (e.g. `notes`) is allowed and ignored.

Each entry in `evals` (a JSON object):
- `id`: int.
- `name`: non-empty string.
- `prompt`: non-empty string.
- `expected_output`: non-empty string.
- `files`: list.
- Any other entry key (e.g. `assertions`) is allowed and ignored.

`id` values must be unique within a file. Extra keys are tolerated so a
skill can carry richer per-eval metadata (ralphify-spec's `assertions`)
without forking the contract.

Pass `--self-test` to run the embedded accept/reject fixtures instead of
scanning the tree (mirrors `bash-shorten.py --self-test`).

Exit 0 on success, 1 on any failure.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REQUIRED_ENTRY_FIELDS: dict[str, type | tuple[type, ...]] = {
    "id": int,
    "name": str,
    "prompt": str,
    "expected_output": str,
    "files": list,
}
NON_EMPTY_STRING_FIELDS = {"name", "prompt", "expected_output"}


def validate_file(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON: {exc}"]

    if not isinstance(data, dict):
        return [f"{path}: top-level value must be a JSON object with 'skill_name' and 'evals'"]

    errors: list[str] = []

    skill_name = data.get("skill_name")
    if not isinstance(skill_name, str) or not skill_name.strip():
        errors.append(f"{path}: 'skill_name' must be a non-empty string")

    evals = data.get("evals")
    if not isinstance(evals, list):
        errors.append(f"{path}: 'evals' must be a list")
        return errors
    if not evals:
        errors.append(f"{path}: 'evals' must contain at least one entry")
        return errors

    seen_ids: set[int] = set()
    for i, entry in enumerate(evals):
        loc = f"{path}: evals[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{loc}: must be a JSON object")
            continue

        for field, expected_type in REQUIRED_ENTRY_FIELDS.items():
            if field not in entry:
                errors.append(f"{loc}: missing required field '{field}'")
                continue
            value = entry[field]
            # bool is a subclass of int; reject it for the int `id` field.
            if expected_type is int and isinstance(value, bool):
                errors.append(f"{loc}: '{field}' must be an int, not a bool")
                continue
            if not isinstance(value, expected_type):
                type_name = getattr(expected_type, "__name__", str(expected_type))
                errors.append(f"{loc}: '{field}' must be {type_name}")
                continue
            if field in NON_EMPTY_STRING_FIELDS and not value.strip():
                errors.append(f"{loc}: '{field}' must be a non-empty string")

        entry_id = entry.get("id")
        if isinstance(entry_id, int) and not isinstance(entry_id, bool):
            if entry_id in seen_ids:
                errors.append(f"{loc}: duplicate id {entry_id}")
            seen_ids.add(entry_id)

    return errors


def _canonical_entry(**overrides: object) -> dict:
    entry = {"id": 0, "name": "a", "prompt": "p", "expected_output": "e", "files": []}
    entry.update(overrides)
    return entry


# (label, payload, expect_errors). Each case pins one accept/reject decision
# the reshape relied on — most critically that the pre-rename bare-array shape
# `[{query, should_trigger}]` is now rejected, so the contract can't silently
# regress back to two incompatible schemas.
_SELF_TEST_CASES: list[tuple[str, object, bool]] = [
    ("canonical evals shape", {"skill_name": "s", "evals": [_canonical_entry()]}, False),
    (
        "extras tolerated (ralphify notes + assertions)",
        {
            "skill_name": "s",
            "notes": "n",
            "evals": [_canonical_entry(assertions=[{"id": "x", "text": "t"}])],
        },
        False,
    ),
    ("old bare-array shape", [{"query": "q", "should_trigger": True}], True),
    ("missing skill_name", {"evals": [_canonical_entry()]}, True),
    ("empty skill_name", {"skill_name": "  ", "evals": [_canonical_entry()]}, True),
    ("evals not a list", {"skill_name": "s", "evals": {}}, True),
    ("empty evals", {"skill_name": "s", "evals": []}, True),
    ("entry not an object", {"skill_name": "s", "evals": ["nope"]}, True),
    (
        "missing expected_output",
        {"skill_name": "s", "evals": [{"id": 0, "name": "a", "prompt": "p", "files": []}]},
        True,
    ),
    ("empty name", {"skill_name": "s", "evals": [_canonical_entry(name="")]}, True),
    ("files not a list", {"skill_name": "s", "evals": [_canonical_entry(files={})]}, True),
    ("id not an int", {"skill_name": "s", "evals": [_canonical_entry(id="0")]}, True),
    ("bool id rejected", {"skill_name": "s", "evals": [_canonical_entry(id=True)]}, True),
    (
        "duplicate id",
        {"skill_name": "s", "evals": [_canonical_entry(id=1), _canonical_entry(id=1, name="b")]},
        True,
    ),
]


def self_test() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "evals.json"
        for label, payload, expect_errors in _SELF_TEST_CASES:
            path.write_text(json.dumps(payload), encoding="utf-8")
            errors = validate_file(path)
            if bool(errors) != expect_errors:
                verb = "expected errors but got none" if expect_errors else f"unexpected errors: {errors}"
                failures.append(f"{label}: {verb}")

    print(f"self-test: {len(_SELF_TEST_CASES) - len(failures)}/{len(_SELF_TEST_CASES)} passed")
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    return 0 if not failures else 1


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()

    if not Path("skills").is_dir():
        print("ERROR: skills/ directory not found", file=sys.stderr)
        return 1

    eval_files = sorted(Path("skills").glob("*/evals/evals.json"))
    if not eval_files:
        print("ERROR: no skills/*/evals/evals.json files found", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    for ef in eval_files:
        all_errors.extend(validate_file(ef))

    if all_errors:
        for e in all_errors:
            print(f"ERROR: {e}", file=sys.stderr)
        print(
            f"\nFAIL: {len(all_errors)} error(s) across {len(eval_files)} eval file(s)",
            file=sys.stderr,
        )
        return 1

    print(f"OK: validated {len(eval_files)} eval file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

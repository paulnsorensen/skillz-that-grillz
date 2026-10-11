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
- `assertions` (optional): list of non-empty strings, as in the
  agentskills.io shape.
- `expected_skill` (optional): non-empty string, or null for a case that
  must not load the skill (NVIDIA SkillEvaluator's routing field).
- Any other entry key is allowed and ignored.

`id` values must be unique within a file. Extra keys are tolerated so a
skill can carry richer per-eval metadata without forking the contract.

Pass `--self-test` to run the embedded accept/reject fixtures instead of
scanning the tree.

Exit 0 on success, 1 on any failure.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import cast

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
        data = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON: {exc}"]

    if not isinstance(data, dict):
        return [f"{path}: top-level value must be a JSON object with 'skill_name' and 'evals'"]

    record = cast(dict[str, object], data)
    errors: list[str] = []

    skill_name = record.get("skill_name")
    if not isinstance(skill_name, str) or not skill_name.strip():
        errors.append(f"{path}: 'skill_name' must be a non-empty string")

    evals = record.get("evals")
    if not isinstance(evals, list):
        errors.append(f"{path}: 'evals' must be a list")
        return errors
    if not evals:
        errors.append(f"{path}: 'evals' must contain at least one entry")
        return errors

    entries = cast(list[object], evals)
    seen_ids: set[int] = set()
    for i, entry in enumerate(entries):
        loc = f"{path}: evals[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{loc}: must be a JSON object")
            continue

        item = cast(dict[str, object], entry)
        for field, expected_type in REQUIRED_ENTRY_FIELDS.items():
            if field not in item:
                errors.append(f"{loc}: missing required field '{field}'")
                continue
            value = item[field]
            # bool is a subclass of int; reject it for the int `id` field.
            if expected_type is int and isinstance(value, bool):
                errors.append(f"{loc}: '{field}' must be an int, not a bool")
                continue
            if not isinstance(value, expected_type):
                type_name = getattr(expected_type, "__name__", str(expected_type))
                errors.append(f"{loc}: '{field}' must be {type_name}")
                continue
            if field in NON_EMPTY_STRING_FIELDS and isinstance(value, str) and not value.strip():
                errors.append(f"{loc}: '{field}' must be a non-empty string")

        assertions = item.get("assertions", [])
        if not isinstance(assertions, list) or not all(
            isinstance(a, str) and a.strip() for a in cast(list[object], assertions)
        ):
            errors.append(f"{loc}: 'assertions' must be a list of non-empty strings")
        expected_skill = item.get("expected_skill")
        if expected_skill is not None and (not isinstance(expected_skill, str) or not expected_skill.strip()):
            errors.append(f"{loc}: 'expected_skill' must be a non-empty string or null")

        entry_id = item.get("id")
        if isinstance(entry_id, int) and not isinstance(entry_id, bool):
            if entry_id in seen_ids:
                errors.append(f"{loc}: duplicate id {entry_id}")
            seen_ids.add(entry_id)

    return errors


def _canonical_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {"id": 0, "name": "a", "prompt": "p", "expected_output": "e", "files": []}
    entry.update(overrides)
    return entry


# (label, payload, expect_errors). Each case pins one accept/reject decision
# the reshape relied on — most critically that the pre-rename bare-array shape
# `[{query, should_trigger}]` is now rejected, so the contract can't silently
# regress back to two incompatible schemas.
_SELF_TEST_CASES: list[tuple[str, object, bool]] = [
    ("canonical evals shape", {"skill_name": "s", "evals": [_canonical_entry()]}, False),
    (
        "extras tolerated (notes + unknown entry key)",
        {"skill_name": "s", "notes": "n", "evals": [_canonical_entry(extra={"k": "v"})]},
        False,
    ),
    (
        "agentskills.io assertions and routing fields",
        {
            "skill_name": "s",
            "evals": [
                _canonical_entry(assertions=["The output names X"], expected_skill="s"),
                _canonical_entry(id=1, name="near-miss", assertions=[], expected_skill=None),
            ],
        },
        False,
    ),
    ("assertions not a list", {"skill_name": "s", "evals": [_canonical_entry(assertions="x")]}, True),
    ("blank assertion", {"skill_name": "s", "evals": [_canonical_entry(assertions=[" "])]}, True),
    ("object assertion", {"skill_name": "s", "evals": [_canonical_entry(assertions=[{"text": "t"}])]}, True),
    ("empty expected_skill", {"skill_name": "s", "evals": [_canonical_entry(expected_skill="")]}, True),
    ("non-string expected_skill", {"skill_name": "s", "evals": [_canonical_entry(expected_skill=1)]}, True),
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
            _ = path.write_text(json.dumps(payload), encoding="utf-8")
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

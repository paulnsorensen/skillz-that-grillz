#!/usr/bin/env python3
"""Validate every skill eval file against one shared schema.

All skills that ship behavioural evals keep them at exactly
`skills/<name>/evals/evals.json`. This script finds every such file and
checks it against one canonical contract:

The reader is `skillz_experiments._evals_format.parse_evals`, which the
autoimprove seed also uses. This script applies its strict profile.

Top-level (a JSON object):
- `skill_name`: non-empty string.
- `evals`: a list of at least one entry.
- Any other top-level key (e.g. `notes`) is allowed and ignored.

Each entry in `evals` (a JSON object):
- `id`: int.
- `name`: non-empty string.
- `kind`: `trigger`, `near-miss`, or `task`. A contradiction is an error:
  null `expected_skill` with a kind other than `near-miss`, `assertions` on
  a trigger or near-miss, or a task with no `expected_output` and no
  `assertions`.
- `prompt`: non-empty string.
- `expected_output`: non-empty string.
- `files`: list of fixture paths under `evals/files/`. Each file must exist.
- `family` (optional): non-empty string.
- `assertions` (optional): list of non-empty strings, as in the
  agentskills.io shape.
- `expected_skill` (optional): non-empty string, or null for a case that
  must not load the skill (NVIDIA SkillEvaluator's routing field).
- Any other entry key is allowed and ignored.

`id` and `name` values must each be unique within a file. Extra keys are tolerated so a
skill can carry richer per-eval metadata without forking the contract.

Pass `--self-test` to run the embedded accept/reject fixtures instead of
scanning the tree.

Exit 0 on success, 1 on any failure.
"""
import json
import sys
import tempfile
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "skillz" / "src"))

from skillz_experiments._evals_format import parse_evals  # noqa: E402


def validate_file(path: Path) -> list[str]:
    try:
        data = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON: {exc}"]
    parsed = parse_evals(data, strict=True)
    errors = [f"{path}: {error}" for error in parsed.errors]
    skill = path.parent.parent
    for entry in parsed.entries:
        errors.extend(f"{path}: evals[{entry.index}]: files: {name} does not exist"
                      for name in entry.files if not (skill / name).is_file())
    return errors


def _canonical_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {"id": 0, "name": "a", "kind": "task", "prompt": "p", "expected_output": "e", "files": []}
    entry.update(overrides)
    return entry


def _doc(*entries: dict[str, object]) -> dict[str, object]:
    return {"skill_name": "s", "evals": list(entries) or [_canonical_entry()]}


# (label, payload, expect_errors). Each case pins one accept/reject decision
# the reshape relied on — most critically that the pre-rename bare-array shape
# `[{query, should_trigger}]` is now rejected, so the contract can't silently
# regress back to two incompatible schemas.
_SELF_TEST_CASES: list[tuple[str, object, bool]] = [
    ("canonical evals shape", _doc(), False),
    (
        "extras tolerated (notes + unknown entry key)",
        {"skill_name": "s", "notes": "n", "evals": [_canonical_entry(extra={"k": "v"})]},
        False,
    ),
    (
        "agentskills.io assertions and routing fields",
        _doc(
            _canonical_entry(assertions=["The output names X"], expected_skill="s"),
            _canonical_entry(id=1, name="near-miss", kind="near-miss", assertions=[], expected_skill=None),
            _canonical_entry(id=2, name="trigger", kind="trigger", expected_skill="s"),
        ),
        False,
    ),
    ("missing kind", _doc({k: v for k, v in _canonical_entry().items() if k != "kind"}), True),
    ("unknown kind", _doc(_canonical_entry(kind="bogus")), True),
    ("null expected_skill on a task", _doc(_canonical_entry(expected_skill=None)), True),
    ("assertions on a trigger", _doc(_canonical_entry(kind="trigger", assertions=["x"])), True),
    ("fixture missing on disk", _doc(_canonical_entry(files=["evals/files/absent.md"])), True),
    ("fixture outside evals/files", _doc(_canonical_entry(files=["SKILL.md"])), True),
    ("assertions not a list", _doc(_canonical_entry(assertions="x")), True),
    ("blank assertion", _doc(_canonical_entry(assertions=[" "])), True),
    ("object assertion", _doc(_canonical_entry(assertions=[{"text": "t"}])), True),
    ("empty expected_skill", _doc(_canonical_entry(expected_skill="")), True),
    ("non-string expected_skill", _doc(_canonical_entry(expected_skill=1)), True),
    ("old bare-array shape", [{"query": "q", "should_trigger": True}], True),
    ("missing skill_name", {"evals": [_canonical_entry()]}, True),
    ("empty skill_name", {"skill_name": "  ", "evals": [_canonical_entry()]}, True),
    ("evals not a list", {"skill_name": "s", "evals": {}}, True),
    ("empty evals", {"skill_name": "s", "evals": []}, True),
    ("entry not an object", {"skill_name": "s", "evals": ["nope"]}, True),
    (
        "missing expected_output",
        _doc({"id": 0, "name": "a", "kind": "task", "prompt": "p", "files": []}),
        True,
    ),
    ("empty name", _doc(_canonical_entry(name="")), True),
    ("files not a list", _doc(_canonical_entry(files={})), True),
    ("id not an int", _doc(_canonical_entry(id="0")), True),
    ("bool id rejected", _doc(_canonical_entry(id=True)), True),
    ("family kept", _doc(_canonical_entry(family="billing")), False),
    ("empty family", _doc(_canonical_entry(family=" ")), True),
    ("non-string family", _doc(_canonical_entry(family=3)), True),
    ("duplicate name", _doc(_canonical_entry(id=1), _canonical_entry(id=2)), True),
    ("duplicate id", _doc(_canonical_entry(id=1), _canonical_entry(id=1, name="b")), True),
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

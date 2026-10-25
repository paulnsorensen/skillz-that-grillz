"""Shared builders of the `CheckMemo` tests: an approved echo contract and three train cases."""
from __future__ import annotations

import json
from pathlib import Path

from skillz_experiments._cases import Case, load_cases
from skillz_experiments._contract import Contract, parse

SKILL = "echo-skill"
HELPER = {"path": "scripts/echo.py", "input": "input.md",
          "fixtures": [{"input": "hi", "returncode": 0, "output": {"ok": True}}]}


def contract(*, helper: bool = False) -> Contract:
    data: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": SKILL,
                               "invocation": f"${SKILL} run", "kinds": {"echo": {"grader": "exact-json"}}}
    if helper:
        data["helper"] = HELPER
    return parse(data, "skill")


def cases(tmp_path: Path, rules: Contract) -> list[Case]:
    items = [{"id": name, "family": name, "split": "train", "kind": "echo", "request": "Echo.", "files": {},
              "expected": {"echo": "hello"}, "provenance": "test", "provider_approved": True} for name in ("a", "b", "c")]
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": items}))
    return load_cases(path, rules.grader_types())

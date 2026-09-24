"""Mutation-testing runner for the fromargs end-to-end acceptance suite.

Applies one exact-string mutation to a throwaway copy of ``src/fromargs`` at a
time, then runs only the acceptance tests marked for that mutation's
acceptance criterion (AC) against the mutated copy. A healthy suite must
*kill* every mutation -- fail at least one of that AC's tests -- because a
mutation that survives means the suite would not catch that kind of real
regression.

Usage (from the repository root, or anywhere)::

    uv run --project lib/fromargs python lib/fromargs/tests/e2e/mutate.py

Never writes to the real ``src/`` tree: every mutation is applied to a fresh
``tempfile.mkdtemp()`` copy that is deleted once that mutation's tests run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

FROMARGS_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = FROMARGS_DIR / "src"
E2E_ARG = "tests/e2e"
VENV_PYTHON = FROMARGS_DIR / ".venv" / "bin" / "python"


@dataclass(frozen=True)
class Mutation:
    """One exact-string source mutation tied to one acceptance criterion."""

    ac_id: str
    label: str
    rel_file: str | None = None
    old: str | None = None
    new: str | None = None
    na_reason: str | None = None

    @property
    def is_na(self) -> bool:
        return self.rel_file is None


MUTATIONS: list[Mutation] = [
    Mutation(
        ac_id="AC-1",
        label="None-result exit code",
        rel_file="_run.py",
        old="        return 0",
        new="        return 1",
    ),
    Mutation(
        ac_id="AC-2",
        label="plain error prefix",
        rel_file="_run.py",
        old='        line = f"ERROR: {\' \'.join(message.splitlines())}"',
        new='        line = f"WARN: {\' \'.join(message.splitlines())}"',
    ),
    Mutation(
        ac_id="AC-3",
        label="JSON error envelope key",
        rel_file="_run.py",
        old='        line = json.dumps({"error": message, "exit_code": exit_code})',
        new='        line = json.dumps({"error": message, "code": exit_code})',
    ),
    Mutation(
        ac_id="AC-4",
        label="unresolved-parse exit code",
        rel_file="_run.py",
        old="            return _report(str(exc), 2, json_mode=json_mode)",
        new="            return _report(str(exc), 1, json_mode=json_mode)",
    ),
    Mutation(
        ac_id="AC-5",
        label="underscore flag binding (--max_count -> max_count)",
        na_reason=(
            "Underscore/dash flag normalization is native Cyclopts behavior "
            "(cyclopts.core, name-collapsing on '-'/'_'); fromargs has no code "
            "path that participates in it, so there is nothing in src/fromargs "
            "meaningful to mutate for this AC."
        ),
    ),
    Mutation(
        ac_id="AC-6",
        label="hoist note wording",
        rel_file="_argv.py",
        old='            f"note: moved {\' \'.join(leading)} after {\' \'.join(command)!r}",',
        new='            f"note: relocated {\' \'.join(leading)} after {\' \'.join(command)!r}",',
    ),
    Mutation(
        ac_id="AC-7",
        label="quote-split note wording",
        rel_file="_argv.py",
        old='    print(f"note: split quoted argument {token!r} into {pieces!r}", file=sys.stderr)',
        new='    print(f"note: split quoted arg {token!r} into {pieces!r}", file=sys.stderr)',
    ),
    Mutation(
        ac_id="AC-8",
        label="json_mode list no longer forces whole-value dump",
        rel_file="_output.py",
        old="    if json_mode or isinstance(value, Mapping):",
        new="    if json_mode and isinstance(value, Mapping):",
    ),
    Mutation(
        ac_id="AC-9",
        label="text truncation footer wording",
        rel_file="_output.py",
        old='            f"... showing {limit} of {total}; pass --full for the rest (limit={limit})",',
        new='            f"... showing {limit} of {total}; pass --all for the rest (limit={limit})",',
    ),
    Mutation(
        ac_id="AC-10",
        label="public surface grows an extra name",
        rel_file="__init__.py",
        old='__all__ = ["CliError", "contract_error", "emit", "repair_argv", "run"]',
        new='__all__ = ["CliError", "contract_error", "emit", "repair_argv", "run", "extra"]',
    ),
]


def collect_node_ids() -> dict[str, list[str]]:
    """Run a collect-only pass and group node IDs by their ``ac`` marker."""
    env = dict(os.environ, FROMARGS_AC_DUMP="1")
    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "pytest", E2E_ARG, "--collect-only", "-q"],
        cwd=FROMARGS_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    by_ac: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        node_id, ac_id = line.split("\t", 1)
        by_ac.setdefault(ac_id, []).append(node_id)
    return by_ac


def run_mutation(mutation: Mutation, node_ids: list[str]) -> str:
    """Apply ``mutation`` to a throwaway ``src`` copy and run its AC's tests.

    Returns ``"KILLED"``, ``"SURVIVED"``, or ``"STALE"``.
    """
    if not node_ids:
        print(f"  (no tests collected for {mutation.ac_id})", file=sys.stderr)
        return "STALE"
    tmp_root = Path(tempfile.mkdtemp(prefix="fromargs-mutate-"))
    try:
        tmp_src = tmp_root / "src"
        shutil.copytree(SRC_DIR, tmp_src)
        target = tmp_src / "fromargs" / mutation.rel_file
        original = target.read_text(encoding="utf-8")
        if mutation.old not in original:
            print(
                f"  pattern not found in {mutation.rel_file}: {mutation.old!r}",
                file=sys.stderr,
            )
            return "STALE"
        target.write_text(original.replace(mutation.old, mutation.new, 1), encoding="utf-8")

        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{tmp_src}{os.pathsep}{existing}" if existing else str(tmp_src)
        )
        result = subprocess.run(
            [str(VENV_PYTHON), "-m", "pytest", *node_ids, "-q"],
            cwd=FROMARGS_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            print(result.stdout, file=sys.stderr)
            return "SURVIVED"
        return "KILLED"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def main() -> int:
    node_ids_by_ac = collect_node_ids()
    failed = False
    for mutation in MUTATIONS:
        if mutation.is_na:
            print(f"{mutation.ac_id} {mutation.label}: N/A ({mutation.na_reason})")
            continue
        outcome = run_mutation(mutation, node_ids_by_ac.get(mutation.ac_id, []))
        print(f"{mutation.ac_id} {mutation.label}: {outcome}")
        if outcome != "KILLED":
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

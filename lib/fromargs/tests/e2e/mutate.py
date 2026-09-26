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
``tempfile.TemporaryDirectory()`` copy that is deleted once that mutation's
tests run.
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
PYTHON = sys.executable


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
        label="command registration renames every command",
        rel_file="_app.py",
        old='        _ = self._cyclopts.command(obj, name=name, help=help, **kwargs)',
        new='        _ = self._cyclopts.command(obj, name=f"broken-{name or \'\'}", help=help, **kwargs)',
    ),
    Mutation(
        ac_id="AC-2",
        label="JSON result payload is discarded",
        rel_file="_output.py",
        old="    print(serialized, file=stream)",
        new='    print(json.dumps({"broken": True}), file=stream)',
    ),
    Mutation(
        ac_id="AC-3",
        label="None-result exit code",
        rel_file="_run.py",
        old="    if status is None:\n        return 0",
        new="    if status is None:\n        return 1",
    ),
    Mutation(
        ac_id="AC-4",
        label="JSON error envelope key",
        rel_file="_run.py",
        old='    print(json.dumps({"error": message, "exit_code": exit_code}), file=sys.stderr)',
        new='    print(json.dumps({"error": message, "code": exit_code}), file=sys.stderr)',
    ),
    Mutation(
        ac_id="AC-5",
        label="--json stops being a no-op",
        rel_file="_argv.py",
        old='GLOBAL_FLAGS = frozenset({"--json", "--full"})',
        new='GLOBAL_FLAGS = frozenset({"--full"})',
    ),
    Mutation(
        ac_id="AC-6",
        label="--full never turns off truncation",
        rel_file="_argv.py",
        old='    return kept + after, "--full" in before',
        new="    return kept + after, False",
    ),
    Mutation(
        ac_id="AC-7",
        label="limit truncation ignores --full",
        rel_file="_output.py",
        old="        if not full and total > limit:",
        new="        if total > limit:",
    ),
    Mutation(
        ac_id="AC-8",
        label="full handler parameter no longer reserved",
        rel_file="_app.py",
        old="    reserved = sorted(GLOBAL_FLAGS.intersection(names))",
        new='    reserved = sorted(frozenset({"--json"}).intersection(names))',
    ),
    Mutation(
        ac_id="AC-9",
        label="public surface grows an extra name",
        rel_file="__init__.py",
        old='__all__ = ["App", "CliError", "Group", "Parameter", "contract_error"]',
        new='__all__ = ["App", "CliError", "Group", "Parameter", "contract_error", "extra"]',
    ),
    Mutation(
        ac_id="AC-10",
        label="no JSON-input flag or stdin reader",
        na_reason=(
            "fromargs never reads sys.stdin and defines no JSON-input flag "
            "anywhere in src/fromargs; the AC is an absence claim with no "
            "existing line whose mutation would introduce that behavior."
        ),
    ),
    Mutation(
        ac_id="AC-11",
        label="ambiguous quote split accepts the first candidate",
        rel_file="_argv.py",
        old="        if found is not None:\n            return None",
        new="        if found is not None:\n            return found[0]",
    ),
    Mutation(
        ac_id="AC-12",
        label="probe parse invokes the handler",
        rel_file="_argv.py",
        old=(
            "        _ = parse_once(app, argv)\n"
            "    except (CycloptsError, CliError):"
        ),
        new=(
            "        handler, bound = parse_once(app, argv)\n"
            "        handler(*bound.args, **bound.kwargs)\n"
            "    except (CycloptsError, CliError):"
        ),
    ),
    Mutation(
        ac_id="AC-13",
        label='"Did you mean" suggestion wording',
        na_reason=(
            "Did-you-mean suggestions are native Cyclopts error-message "
            "formatting; fromargs only relays str(exc) verbatim in _report "
            "(_run.py), so there is no fromargs-owned line whose mutation "
            "would selectively break the suggestion wording without also "
            "breaking AC-4's envelope tests, which already cover _report."
        ),
    ),
    Mutation(
        ac_id="AC-15",
        label="resolved version falls back before checking the caller module",
        rel_file="_app.py",
        old="            caller = frame.f_back if frame is not None else None",
        new="            caller = frame",
    ),
    Mutation(
        ac_id="AC-16",
        label="default handler is never registered with Cyclopts",
        rel_file="_app.py",
        old="            _ = self._cyclopts.default(obj, validator=validator)",
        new="            pass",
    ),
    Mutation(
        ac_id="AC-17",
        label="group kwargs are dropped from the nested Cyclopts app",
        rel_file="_app.py",
        old="        sub = cyclopts.App(name=name, help=help, **cyclopts_kwargs)",
        new="        sub = cyclopts.App(name=name, help=help)",
    ),
    Mutation(
        ac_id="AC-14",
        label="underscore flag binding (--max_count -> max_count)",
        na_reason=(
            "Underscore/dash flag normalization is native Cyclopts behavior "
            "(cyclopts.core, name-collapsing on '-'/'_'); fromargs has no "
            "code path that participates in it, so there is nothing in "
            "src/fromargs meaningful to mutate for this AC."
        ),
    ),
]


def collect_node_ids() -> dict[str, list[str]]:
    """Run a collect-only pass and group node IDs by their ``ac`` marker."""
    env = dict(os.environ, FROMARGS_AC_DUMP="1")
    result = subprocess.run(
        [PYTHON, "-m", "pytest", E2E_ARG, "--collect-only", "-q"],
        cwd=FROMARGS_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pytest --collect-only failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
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

    Returns ``"KILLED"``, ``"SURVIVED"``, ``"STALE"``, or ``"ERROR"``.
    ``"ERROR"`` means pytest itself could not run the tests (any nonzero exit
    other than the "some tests failed" exit 1), so the outcome says nothing
    about whether the mutation was caught.
    """
    if not node_ids:
        print(f"  (no tests collected for {mutation.ac_id})", file=sys.stderr)
        return "STALE"
    assert mutation.rel_file is not None
    assert mutation.old is not None
    assert mutation.new is not None
    with tempfile.TemporaryDirectory(prefix="fromargs-mutate-") as tmp_root_name:
        tmp_root = Path(tmp_root_name)
        tmp_src = tmp_root / "src"
        _ = shutil.copytree(SRC_DIR, tmp_src)
        target = tmp_src / "fromargs" / mutation.rel_file
        original = target.read_text(encoding="utf-8")
        if mutation.old not in original:
            print(
                f"  pattern not found in {mutation.rel_file}: {mutation.old!r}",
                file=sys.stderr,
            )
            return "STALE"
        _ = target.write_text(original.replace(mutation.old, mutation.new, 1), encoding="utf-8")

        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{tmp_src}{os.pathsep}{existing}" if existing else str(tmp_src)
        )
        result = subprocess.run(
            [PYTHON, "-m", "pytest", *node_ids, "-q"],
            cwd=FROMARGS_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 1:
            return "KILLED"
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        if result.returncode == 0:
            return "SURVIVED"
        return "ERROR"


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

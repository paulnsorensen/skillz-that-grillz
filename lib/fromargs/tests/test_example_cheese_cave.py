"""Self-healing showcase: the cheese-cave example CLI under malformed agent argv.

Each healed call must match its canonical call exactly: same exit status,
same stdout, and same cave state. The handler runs once, and stderr holds
only the ``note:`` lines that explain the repairs. Where a guess is unsafe,
fromargs refuses to repair, runs no handler, and names the fix instead.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from cheese_cave import Wheel, build_app, starter_cave

import fromargs

JsonLine = Callable[[str], dict[str, object]]
Outcome = tuple[int, str, str, dict[str, Wheel]]

EXAMPLE = Path(__file__).parents[1] / "examples" / "cheese_cave.py"


def _call(capsys: pytest.CaptureFixture[str], argv: list[str]) -> Outcome:
    """Run ``argv`` against a new starter cave; return status, out, err, and cave."""
    cave = starter_cave()
    status = fromargs.run(build_app(cave), argv=argv)
    captured = capsys.readouterr()
    return status, captured.out, captured.err, cave


HEALS = {
    "leading --json before a nested command": (
        ["--json", "wheels", "list"],
        ["wheels", "list", "--json"],
        ["note: moved --json after 'wheels list'"],
    ),
    "leading --full before a nested command": (
        ["--full", "wheels", "list"],
        ["wheels", "list", "--full"],
        ["note: moved --full after 'wheels list'"],
    ),
    "flag merged into an int value": (
        ["age", "brie", "--weeks", "2 --dry-run"],
        ["age", "brie", "--weeks", "2", "--dry-run"],
        ["note: split quoted argument '2 --dry-run' into ['2', '--dry-run']"],
    ),
    "whole tail merged into one token": (
        ["age", "brie", "--weeks", "3 --dry-run --json"],
        ["age", "brie", "--weeks", "3", "--dry-run", "--json"],
        [
            "note: split quoted argument '3 --dry-run --json' "
            "into ['3', '--dry-run', '--json']"
        ],
    ),
    "equals form that changes state": (
        ["age", "comte", "--weeks=8 --json"],
        ["age", "comte", "--weeks=8", "--json"],
        ["note: split quoted argument '--weeks=8 --json' into ['--weeks=8', '--json']"],
    ),
    "leading flag and merged value in one call": (
        ["--json", "age", "gouda", "--weeks", "2 --dry-run"],
        ["age", "gouda", "--json", "--weeks", "2", "--dry-run"],
        [
            "note: moved --json after 'age'",
            "note: split quoted argument '2 --dry-run' into ['2', '--dry-run']",
        ],
    ),
    "snake_case flag (native Cyclopts)": (
        ["age", "brie", "--weeks", "2", "--dry_run"],
        ["age", "brie", "--weeks", "2", "--dry-run"],
        [],
    ),
}


@pytest.mark.parametrize(
    ("mangled", "canonical", "notes"), HEALS.values(), ids=HEALS.keys()
)
def test_mangled_argv_heals_to_the_canonical_call(
    capsys: pytest.CaptureFixture[str],
    mangled: list[str],
    canonical: list[str],
    notes: list[str],
) -> None:
    expected_status, expected_out, expected_err, expected_cave = _call(
        capsys, canonical
    )
    assert (expected_status, expected_err) == (0, "")

    status, out, err, cave = _call(capsys, mangled)

    assert status == expected_status
    assert out == expected_out
    assert cave == expected_cave
    assert err.splitlines() == notes


def test_state_change_runs_once_after_repair(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, out, _, cave = _call(capsys, ["age", "comte", "--weeks=8 --json"])

    assert json.loads(out) == {"name": "comte", "weeks": 60, "dry_run": False}
    assert cave["comte"].weeks == 60


def _did_you_mean(err: str) -> str:
    match = re.search(r'Did you mean "([^"]+)"\?', err)
    assert match is not None, err
    return match.group(1)


def test_agent_retries_a_typo_from_the_json_error(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    argv = ["--json", "wheels", "lst"]

    status, out, err, _ = _call(capsys, argv)

    assert (status, out) == (2, "")
    envelope = single_json_line(err)
    assert envelope["exit_code"] == 2
    suggestion = _did_you_mean(str(envelope["error"]))
    assert suggestion == "list"

    retry = [suggestion if token == "lst" else token for token in argv]
    status, out, err, _ = _call(capsys, retry)

    assert status == 0
    assert err == "note: moved --json after 'wheels list'\n"
    assert [wheel["name"] for wheel in json.loads(out)] == [
        "comte",
        "gouda",
        "stilton",
        "brie",
    ]


def test_agent_follows_the_truncation_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status, out, _, _ = _call(capsys, ["wheels", "list"])

    assert status == 0
    assert out.splitlines()[-1] == (
        "... showing 3 of 4; pass --full for the rest (limit=3)"
    )
    assert "brie" not in out

    status, out, _, _ = _call(capsys, ["wheels", "list", "--full"])

    assert status == 0
    assert "brie" in out
    assert out.splitlines()[-1] == "... showing 4 of 4 (--full; default limit=3)"


REFUSALS = {
    "free-text str value is never split": (
        ["wheels", "show", "brie --json"],
        "unknown wheel 'brie --json'; known wheels: brie, comte, gouda, stilton",
    ),
    "no split parses": (
        ["age", "brie", "--weeks", "two --dry-run"],
        "Invalid value",
    ),
    "leading value flag is not moved": (
        ["--weeks", "2", "age", "brie"],
        "Unknown command",
    ),
}


@pytest.mark.parametrize(
    ("argv", "message"), REFUSALS.values(), ids=REFUSALS.keys()
)
def test_unsafe_guess_is_refused_with_an_actionable_error(
    capsys: pytest.CaptureFixture[str], argv: list[str], message: str
) -> None:
    status, out, err, cave = _call(capsys, argv)

    assert status == 2
    assert out == ""
    assert "note:" not in err
    assert err.startswith("ERROR: ")
    assert message in err
    assert cave == starter_cave()


def test_contract_violation_exits_3_as_json(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    status, out, err, cave = _call(capsys, ["load", "brie:bloomy", "--json"])

    assert (status, out) == (3, "")
    assert single_json_line(err) == {
        "error": "record 'brie:bloomy': not enough values to unpack "
        "(expected 3, got 2)",
        "exit_code": 3,
    }
    assert cave == starter_cave()


def test_script_heals_argv_in_a_real_process() -> None:
    result = subprocess.run(
        [sys.executable, str(EXAMPLE), "--json", "age", "brie", "--weeks", "2 --dry-run"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr.splitlines() == [
        "note: moved --json after 'age'",
        "note: split quoted argument '2 --dry-run' into ['2', '--dry-run']",
    ]
    assert json.loads(result.stdout) == {"name": "brie", "weeks": 6, "dry_run": True}

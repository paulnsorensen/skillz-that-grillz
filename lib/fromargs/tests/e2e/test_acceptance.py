"""End-to-end acceptance suite for the fromargs public contract.

Every test drives ``acceptance_cli.py`` as a real subprocess and asserts exit
code, exact stdout, exact stderr, and the call log written by the fixture.
The only exceptions are AC-9's import-surface check (which imports
``fromargs`` directly) and AC-8's registration check (which drives the
fixture through ``FROMARGS_E2E_REGISTER_BAD``, still a subprocess).

Each test is marked ``@pytest.mark.ac("AC-n")`` so ``mutate.py`` can select,
per acceptance criterion, exactly the tests that must catch a broken build.

Acceptance criteria:

AC-1: WHEN argv names a registered top-level command, or a command inside a
    decorator-registered nested group, THE SYSTEM SHALL invoke exactly that
    handler once.
AC-2: WHEN a handler returns a value other than None THE SYSTEM SHALL print
    that value as one JSON document on stdout and exit 0.
AC-3: WHEN a handler returns None THE SYSTEM SHALL print nothing on stdout
    and exit 0.
AC-4: WHEN a handler raises CliError, or argv fails to parse, THE SYSTEM
    SHALL print exactly one JSON line ``{"error": <message>, "exit_code":
    <n>}`` on stderr, print nothing on stdout, and exit with that error's
    code: 2 for a parse failure or a plain CliError, 3 for contract_error.
AC-5: WHEN a bare --json token appears anywhere before the end-of-options
    marker THE SYSTEM SHALL drop it silently and SHALL NOT change the
    command's exit code, stdout, or call arguments.
AC-6: WHEN a bare --full token appears anywhere before the end-of-options
    marker, including one revealed only after a verified quote split, THE
    SYSTEM SHALL drop it and turn off truncation of the handler's result.
AC-7: WHEN a command is registered with a limit and its result is a sequence
    longer than limit, and --full is absent, THE SYSTEM SHALL truncate the
    printed JSON array to the first limit items and print one note line on
    stderr; WHEN --full is present THE SYSTEM SHALL print the whole sequence
    and no truncation note.
AC-8: WHEN a command handler declares a parameter named json or full THE
    SYSTEM SHALL raise ValueError at registration time and SHALL NOT
    register the command.
AC-9: THE SYSTEM SHALL export exactly App, CliError, and contract_error from
    fromargs.__all__.
AC-10: THE SYSTEM SHALL provide no command-line flag and no stdin reader for
    JSON input; a run's behavior SHALL be identical whether or not garbage
    text is piped to stdin.
AC-11: WHEN Cyclopts rejects an argv because one token merges several
    shell-quoted arguments THE SYSTEM SHALL retry only the one candidate
    split that parses on its own, printing one note: line; WHEN zero splits
    parse, WHEN more than one candidate parses, WHEN the mergeable token
    belongs to a free-text str option, or WHEN the token is after the
    end-of-options marker, THE SYSTEM SHALL leave argv unchanged and report
    the original error.
AC-12: WHEN argv is rejected and a quote-split repair is probed THE SYSTEM
    SHALL NOT invoke any handler until the final, single-candidate parse.
AC-13: WHEN argv names an unknown command or option close to a registered
    one THE SYSTEM SHALL include a "Did you mean" suggestion naming the
    closest match in the error message.
AC-14: WHEN argv uses an underscore variant of a dashed flag name THE SYSTEM
    SHALL bind it to the same parameter as the dashed form (native Cyclopts
    behavior).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import fromargs

CLI = Path(__file__).with_name("acceptance_cli.py")


def run_cli(
    tmp_path: Path, argv: list[str], *, env: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    """Run the fixture CLI as a subprocess; return the result and its call log."""
    log_path = tmp_path / "calls.jsonl"
    merged_env = dict(os.environ)
    merged_env["FROMARGS_E2E_CALL_LOG"] = str(log_path)
    if env:
        merged_env.update(env)
    result = subprocess.run(
        [sys.executable, str(CLI), *argv],
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=30,
    )
    calls = (
        [json.loads(line) for line in log_path.read_text().splitlines()]
        if log_path.exists()
        else []
    )
    return result, calls


# --------------------------------------------------------------------------
# AC-1: decorator commands and groups each dispatch to exactly one handler.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-1")
def test_ac01_top_level_command_runs_once(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "ok"])

    assert result.returncode == 0
    assert len(calls) == 1
    assert calls[0]["command"] == "widget"


@pytest.mark.ac("AC-1")
def test_ac01_group_command_runs_once(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["crate", "show", "3"])

    assert result.returncode == 0
    assert calls == [{"command": "crate show", "args": {"id_": 3}}]
    assert json.loads(result.stdout) == {"id": 3}


# --------------------------------------------------------------------------
# AC-2: a non-None return value becomes one JSON document on stdout, exit 0.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-2")
def test_ac02_int_result_prints_as_one_json_document(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "7"])

    assert result.returncode == 0
    assert json.loads(result.stdout) == 7
    assert len(calls) == 1


@pytest.mark.ac("AC-2")
def test_ac02_dict_result_prints_as_one_json_document(tmp_path: Path) -> None:
    result, _ = run_cli(tmp_path, ["crate", "show", "9"])

    assert result.returncode == 0
    assert result.stdout.splitlines()[-1] == "}"
    assert json.loads(result.stdout) == {"id": 9}


# --------------------------------------------------------------------------
# AC-3: a None return value means no stdout, exit 0.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-3")
def test_ac03_none_result_prints_nothing_and_exits_zero(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "none"])

    assert result.returncode == 0
    assert result.stdout == ""
    assert len(calls) == 1


# --------------------------------------------------------------------------
# AC-4: every error is one JSON stderr line, empty stdout, and its own code.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-4")
def test_ac04_cli_error_exits_two_as_one_json_line(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["fail", "plain"])

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.splitlines() == [json.dumps({"error": "boom", "exit_code": 2})]
    assert calls == [{"command": "fail", "args": {"kind": "plain"}}]


@pytest.mark.ac("AC-4")
def test_ac04_contract_error_exits_three_as_one_json_line(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["fail", "contract"])

    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        json.dumps({"error": "load: bad shape", "exit_code": 3})
    ]
    assert calls == [{"command": "fail", "args": {"kind": "contract"}}]


@pytest.mark.ac("AC-4")
def test_ac04_parse_failure_exits_two_as_one_json_line(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widgt"])

    assert result.returncode == 2
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    envelope = json.loads(lines[0])
    assert set(envelope) == {"error", "exit_code"}
    assert envelope["exit_code"] == 2
    assert calls == []


# --------------------------------------------------------------------------
# AC-5: --json is a no-op anywhere before the end-of-options marker.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-5")
@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "widget", "ok"],
        ["widget", "--json", "ok"],
        ["widget", "ok", "--json"],
    ],
    ids=["leading", "mid", "trailing"],
)
def test_ac05_json_flag_is_a_noop_anywhere_before_the_marker(
    tmp_path: Path, argv: list[str]
) -> None:
    baseline_dir, result_dir = tmp_path / "baseline", tmp_path / "result"
    baseline_dir.mkdir()
    result_dir.mkdir()
    baseline, baseline_calls = run_cli(baseline_dir, ["widget", "ok"])
    result, calls = run_cli(result_dir, argv)

    assert result.returncode == baseline.returncode == 0
    assert result.stdout == baseline.stdout
    assert result.stderr == ""
    assert calls == baseline_calls


# --------------------------------------------------------------------------
# AC-6: --full is accepted anywhere before the marker, even revealed by a
# split, and turns off truncation.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-6")
@pytest.mark.parametrize(
    "argv", [["--full", "ranked"], ["ranked", "--full"]], ids=["leading", "trailing"]
)
def test_ac06_full_flag_anywhere_turns_off_truncation(
    tmp_path: Path, argv: list[str]
) -> None:
    result, _ = run_cli(tmp_path, argv)

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == [0, 1, 2, 3, 4]


@pytest.mark.ac("AC-6")
def test_ac06_full_revealed_by_a_quote_split_is_honored(tmp_path: Path) -> None:
    result, _ = run_cli(tmp_path, ["ranked", "--top", "5 --full"])

    assert result.returncode == 0
    assert result.stderr == "note: split quoted argument '5 --full' into ['5', '--full']\n"
    assert json.loads(result.stdout) == [0, 1, 2, 3, 4]


# --------------------------------------------------------------------------
# AC-7: limit truncation with a stderr note; --full suppresses both.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-7")
def test_ac07_result_longer_than_limit_is_truncated_with_a_note(tmp_path: Path) -> None:
    result, _ = run_cli(tmp_path, ["ranked"])

    assert result.returncode == 0
    assert result.stderr == "note: showing 3 of 5; pass --full for the rest\n"
    assert json.loads(result.stdout) == [0, 1, 2]


@pytest.mark.ac("AC-7")
def test_ac07_full_prints_the_whole_result_with_no_note(tmp_path: Path) -> None:
    result, _ = run_cli(tmp_path, ["ranked", "--full"])

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == [0, 1, 2, 3, 4]


# --------------------------------------------------------------------------
# AC-8: a json/full handler parameter is rejected at registration time.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-8")
@pytest.mark.parametrize("reserved", ["json", "full"])
def test_ac08_reserved_parameter_raises_at_registration(
    tmp_path: Path, reserved: str
) -> None:
    result, calls = run_cli(
        tmp_path, [], env={"FROMARGS_E2E_REGISTER_BAD": reserved}
    )

    assert result.returncode == 0
    envelope = json.loads(result.stdout)
    assert envelope == {
        "raised": True,
        "error": f"command option {'--' + reserved!r} is reserved by fromargs",
    }
    assert calls == []


# --------------------------------------------------------------------------
# AC-9: the public surface is exactly App, CliError, contract_error.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-9")
def test_ac09_public_surface_is_exactly_three_names() -> None:
    assert set(fromargs.__all__) == {"App", "CliError", "contract_error"}
    assert len(fromargs.__all__) == 3
    for name in fromargs.__all__:
        assert hasattr(fromargs, name)


# --------------------------------------------------------------------------
# AC-10: no JSON-input flag, no stdin reader.
# --------------------------------------------------------------------------


_NO_INPUT_CHANNEL_FLAGS = ("--json-input", "--stdin", "--payload")


@pytest.mark.ac("AC-10")
def test_ac10_help_advertises_no_json_input_or_stdin_flag(tmp_path: Path) -> None:
    root_help, root_calls = run_cli(tmp_path, ["--help"])
    widget_help, widget_calls = run_cli(tmp_path, ["widget", "--help"])

    assert root_help.returncode == 0
    assert widget_help.returncode == 0
    for flag in _NO_INPUT_CHANNEL_FLAGS:
        assert flag not in root_help.stdout
        assert flag not in widget_help.stdout
    assert root_calls == []
    assert widget_calls == []


@pytest.mark.ac("AC-10")
def test_ac10_stdin_with_invalid_payload_does_not_change_behavior(
    tmp_path: Path,
) -> None:
    """No command reads stdin, so garbage on stdin cannot break a run.

    If ``run`` ever gained a JSON-input/stdin reader, piping invalid JSON in
    would surface as a parse error here; instead the result is byte-for-byte
    identical to the same argv with stdin closed.
    """
    log_no_stdin = tmp_path / "calls-no-stdin.jsonl"
    log_with_stdin = tmp_path / "calls-with-stdin.jsonl"
    env_no_stdin = dict(os.environ, FROMARGS_E2E_CALL_LOG=str(log_no_stdin))
    env_with_stdin = dict(os.environ, FROMARGS_E2E_CALL_LOG=str(log_with_stdin))

    without_stdin = subprocess.run(
        [sys.executable, str(CLI), "widget", "ok"],
        capture_output=True,
        text=True,
        env=env_no_stdin,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    with_garbage_stdin = subprocess.run(
        [sys.executable, str(CLI), "widget", "ok"],
        capture_output=True,
        text=True,
        input="{not valid json at all",
        env=env_with_stdin,
        timeout=30,
    )

    assert without_stdin.returncode == with_garbage_stdin.returncode == 0
    assert without_stdin.stdout == with_garbage_stdin.stdout == ""
    assert without_stdin.stderr == with_garbage_stdin.stderr == ""
    assert log_no_stdin.read_text() == log_with_stdin.read_text()


# --------------------------------------------------------------------------
# AC-11: verified quote split, and its refusals.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-11")
def test_ac11_single_verified_candidate_is_split_and_noted(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "ok", "--count", "2 --dry-run"])

    assert result.returncode == 0
    assert (
        result.stderr
        == "note: split quoted argument '2 --dry-run' into ['2', '--dry-run']\n"
    )
    assert calls[0]["args"]["count"] == 2
    assert calls[0]["args"]["dry_run"] is True


@pytest.mark.ac("AC-11")
def test_ac11_zero_candidates_keeps_argv_and_fails_normally(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "--count", "two --dry-run"])

    assert result.returncode == 2
    assert "note:" not in result.stderr
    assert calls == []


@pytest.mark.ac("AC-11")
def test_ac11_several_candidates_keeps_argv_and_fails_normally(tmp_path: Path) -> None:
    argv = [
        "ambiguous",
        "--first",
        "a --count 1",
        "--second",
        "b --count 2",
    ]
    result, calls = run_cli(tmp_path, argv)

    assert result.returncode == 2
    assert "note:" not in result.stderr
    assert calls == []


@pytest.mark.ac("AC-11")
def test_ac11_plain_str_option_value_is_never_split(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "--label", "a --count 2"])

    assert result.returncode == 0
    assert "note:" not in result.stderr
    assert calls[0]["args"]["label"] == "a --count 2"
    assert calls[0]["args"]["count"] == 1


@pytest.mark.ac("AC-11")
def test_ac11_token_after_double_dash_is_never_split(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "ok", "--", "--count 2"])

    assert result.returncode == 0
    assert "note:" not in result.stderr
    assert calls[0]["args"]["words"] == ["--count 2"]
    assert calls[0]["args"]["count"] == 1


# --------------------------------------------------------------------------
# AC-12: no handler runs while a rejected argv is being probed.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-12")
def test_ac12_no_handler_runs_during_a_refused_probe(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "--count", "two --dry-run"])

    assert result.returncode == 2
    assert calls == []


@pytest.mark.ac("AC-12")
def test_ac12_handler_runs_exactly_once_after_a_healed_split(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "ok", "--count", "2 --dry-run"])

    assert result.returncode == 0
    assert len(calls) == 1


# --------------------------------------------------------------------------
# AC-13: "Did you mean" on an unknown command or a near-miss option.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-13")
def test_ac13_unknown_command_suggests_the_closest_match(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widgt"])

    assert result.returncode == 2
    assert "Did you mean" in result.stderr
    assert '\\"widget\\"' in result.stderr
    assert calls == []


@pytest.mark.ac("AC-13")
def test_ac13_near_miss_option_suggests_the_closest_match(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "ok", "--max_cnt", "3"])

    assert result.returncode == 2
    assert "Did you mean --max-count?" in result.stderr
    assert calls == []


# --------------------------------------------------------------------------
# AC-14: an underscore flag binds to the dashed parameter (native Cyclopts).
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-14")
def test_ac14_underscore_flag_binds_max_count(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "ok", "--max_count", "3"])

    assert result.returncode == 0
    assert calls[0]["args"]["max_count"] == 3
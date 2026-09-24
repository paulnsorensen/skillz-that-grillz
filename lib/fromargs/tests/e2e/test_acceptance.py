"""End-to-end acceptance suite for the fromargs public contract.

Every test drives ``acceptance_cli.py`` as a real subprocess and asserts exit
code, exact stdout, exact stderr, and the call log written by the fixture.
The only exceptions are AC-10's import-surface check (which imports
``fromargs`` directly) and the AC-8/AC-9 ``emit`` checks (which go through a
fixture command that calls ``emit``, still exercised as a subprocess).

Each test is marked ``@pytest.mark.ac("AC-n")`` so ``mutate.py`` can select,
per acceptance criterion, exactly the tests that must catch a broken build.
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
# AC-1: a handler runs exactly once; None -> 0, int -> that int.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-1")
def test_ac01_none_result_runs_once_and_exits_zero(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "none"])

    assert result.returncode == 0
    assert result.stdout == ""
    assert len(calls) == 1
    assert calls[0]["command"] == "widget"


@pytest.mark.ac("AC-1")
def test_ac01_int_result_runs_once_and_is_the_exit_code(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "7"])

    assert result.returncode == 7
    assert len(calls) == 1


# --------------------------------------------------------------------------
# AC-2: CliError / contract_error without --json: one stderr line, empty stdout.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-2")
def test_ac02_cli_error_reports_message_and_its_own_exit_code(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["fail", "plain"])

    assert result.returncode == 5
    assert result.stderr == "ERROR: boom\n"
    assert result.stdout == ""
    assert calls == [{"command": "fail", "args": {"kind": "plain"}}]


@pytest.mark.ac("AC-2")
def test_ac02_contract_error_reports_context_and_exits_three(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["fail", "contract"])

    assert result.returncode == 3
    assert result.stderr == "ERROR: load: bad shape\n"
    assert result.stdout == ""
    assert calls == [{"command": "fail", "args": {"kind": "contract"}}]


# --------------------------------------------------------------------------
# AC-3: --json present: exactly one stderr JSON line, empty stdout.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-3")
def test_ac03_json_parse_failure_is_one_json_stderr_line(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["--json", "widgt"])

    assert result.returncode == 2
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    envelope = json.loads(lines[0])
    assert set(envelope) == {"error", "exit_code"}
    assert envelope["exit_code"] == 2
    assert calls == []


@pytest.mark.ac("AC-3")
def test_ac03_json_handler_error_is_one_json_stderr_line(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["fail", "contract", "--json"])

    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        json.dumps({"error": "load: bad shape", "exit_code": 3})
    ]
    assert calls == [{"command": "fail", "args": {"kind": "contract"}}]


# --------------------------------------------------------------------------
# AC-4: unknown command / near-miss option -> exit 2, "Did you mean", no handler.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-4")
def test_ac04_unknown_command_suggests_the_closest_match(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widgt"])

    assert result.returncode == 2
    assert "Did you mean" in result.stderr
    assert '"widget"' in result.stderr
    assert calls == []


@pytest.mark.ac("AC-4")
def test_ac04_near_miss_option_suggests_the_closest_match(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "ok", "--max_cnt", "3"])

    assert result.returncode == 2
    assert "Did you mean --max-count?" in result.stderr
    assert calls == []


# --------------------------------------------------------------------------
# AC-5: --max_count binds to max_count.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-5")
def test_ac05_underscore_flag_binds_max_count(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "ok", "--max_count", "3"])

    assert result.returncode == 0
    assert calls[0]["args"]["max_count"] == 3


# --------------------------------------------------------------------------
# AC-6: leading --json/--full hoist after the command; other flags pass through.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-6")
def test_ac06_leading_json_hoists_after_the_command(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["--json", "widget", "ok"])

    assert result.returncode == 0
    assert result.stderr == "note: moved --json after 'widget'\n"
    assert calls[0]["args"]["json"] is True


@pytest.mark.ac("AC-6")
def test_ac06_leading_json_and_full_hoist_together(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["--full", "--json", "widget", "ok"])

    assert result.returncode == 0
    assert result.stderr == "note: moved --full --json after 'widget'\n"
    assert calls[0]["args"]["json"] is True
    assert calls[0]["args"]["full"] is True


@pytest.mark.ac("AC-6")
def test_ac06_note_is_plain_text_even_with_json_requested(tmp_path: Path) -> None:
    result, _ = run_cli(tmp_path, ["--json", "widget", "ok"])

    # The note is plain text on stderr, distinct from the JSON error envelope.
    assert result.stderr.startswith("note: ")
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stderr)


@pytest.mark.ac("AC-6")
def test_ac06_other_leading_flag_passes_through_unchanged(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["--count", "widget", "ok"])

    assert result.returncode == 2
    assert "note:" not in result.stderr
    assert 'Unknown command "--count"' in result.stderr
    assert calls == []


# --------------------------------------------------------------------------
# AC-7: single-candidate quote split; zero/several candidates, str option, and
# tokens after `--` all leave argv unchanged; no handler runs during probing.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-7")
def test_ac07_single_verified_candidate_is_split_and_noted(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "ok", "--count", "2 --full"])

    assert result.returncode == 0
    assert (
        result.stderr == "note: split quoted argument '2 --full' into ['2', '--full']\n"
    )
    assert calls[0]["args"]["count"] == 2
    assert calls[0]["args"]["full"] is True


@pytest.mark.ac("AC-7")
def test_ac07_zero_candidates_keeps_argv_and_fails_normally(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "--count", "two --full"])

    assert result.returncode == 2
    assert "note:" not in result.stderr
    assert calls == []


@pytest.mark.ac("AC-7")
def test_ac07_several_candidates_keeps_argv_and_fails_normally(tmp_path: Path) -> None:
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


@pytest.mark.ac("AC-7")
def test_ac07_plain_str_option_value_is_never_split(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "--label", "a --count 2"])

    assert result.returncode == 0
    assert "note:" not in result.stderr
    assert calls[0]["args"]["label"] == "a --count 2"
    assert calls[0]["args"]["count"] == 1


@pytest.mark.ac("AC-7")
def test_ac07_token_after_double_dash_is_never_split(tmp_path: Path) -> None:
    result, calls = run_cli(tmp_path, ["widget", "ok", "--", "--count 2"])

    assert result.returncode == 0
    assert "note:" not in result.stderr
    assert calls[0]["args"]["words"] == ["--count 2"]
    assert calls[0]["args"]["count"] == 1


# --------------------------------------------------------------------------
# AC-8: emit with json_mode=True or a dict dumps in full, ignoring `limit`.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-8")
def test_ac08_dict_dumps_whole_even_with_limit_set(tmp_path: Path) -> None:
    result, _ = run_cli(tmp_path, ["emit_dict", "--limit", "1"])

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"items": [1, 2, 3, 4, 5]}


@pytest.mark.ac("AC-8")
def test_ac08_json_mode_list_dumps_whole_even_with_limit_set(tmp_path: Path) -> None:
    result, _ = run_cli(tmp_path, ["emit_json_list", "--limit", "1"])

    assert result.returncode == 0
    assert json.loads(result.stdout) == [1, 2, 3, 4, 5]


# --------------------------------------------------------------------------
# AC-9: emit text mode truncates a list; --full prints everything.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-9")
def test_ac09_text_list_truncates_at_limit(tmp_path: Path) -> None:
    result, _ = run_cli(tmp_path, ["emit_list", "--limit", "3"])

    assert result.stdout.splitlines() == [
        "a",
        "b",
        "c",
        "... showing 3 of 5; pass --full for the rest (limit=3)",
    ]


@pytest.mark.ac("AC-9")
def test_ac09_full_prints_every_item(tmp_path: Path) -> None:
    result, _ = run_cli(tmp_path, ["emit_list", "--limit", "3", "--full"])

    assert result.stdout.splitlines() == [
        "a",
        "b",
        "c",
        "d",
        "e",
        "... showing 5 of 5 (--full; default limit=3)",
    ]


# --------------------------------------------------------------------------
# AC-10: exact public surface, no JSON-input flag, and Cyclopts JSON-string
# parameters (dataclass, list[int]) still parse.
# --------------------------------------------------------------------------


@pytest.mark.ac("AC-10")
def test_ac10_public_surface_is_exactly_five_names() -> None:
    assert set(fromargs.__all__) == {
        "CliError",
        "contract_error",
        "emit",
        "repair_argv",
        "run",
    }
    assert len(fromargs.__all__) == 5
    for name in fromargs.__all__:
        assert hasattr(fromargs, name)


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

    If ``run`` (or a fixture command) ever gained a JSON-input/stdin reader,
    piping invalid JSON in would surface as a parse error here; instead the
    result is byte-for-byte identical to the same argv with stdin closed.
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


@pytest.mark.ac("AC-10")
def test_ac10_dataclass_and_list_int_parameters_still_parse(tmp_path: Path) -> None:
    result, calls = run_cli(
        tmp_path, ["conf", "--point", '{"a": 1}', "--numbers", "[1, 2]"]
    )

    assert result.returncode == 0
    assert calls == [
        {
            "command": "conf",
            "args": {"point": {"a": 1}, "numbers": [1, 2], "json": False},
        }
    ]

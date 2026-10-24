"""The shared redactor stays linear on hostile input and covers multiline and unterminated quotes."""

import subprocess
import sys

import pytest

from skillz_experiments import _redact

DEADLINE_SECONDS = 15


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("hostile", [
    "a" * 65536,
    "a_" * 32768,
    "a-" * 32768,
    "password" + "-a" * 32768,
    "=" * 65536,
    ":=" * 32768,
    "a." * 32768,
    "eyJ" + "a." * 32768],
    ids=["word", "underscores", "hyphens", "keyword-hyphens", "equals", "colon-equals", "dots", "jwt-dots"])
def test_redacting_64_kib_without_whitespace_finishes_before_the_deadline(hostile: str) -> None:
    program = "import sys\nfrom skillz_experiments._redact import redact\nredact(sys.stdin.read())\n"
    try:
        done = subprocess.run([sys.executable, "-c", program], input=hostile, text=True, capture_output=True,
                              timeout=DEADLINE_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        pytest.fail(f"redact took longer than {DEADLINE_SECONDS}s on {len(hostile)} characters")
    assert done.returncode == 0, done.stderr


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("token", ["a" * 5000, "build_artifact_name_" * 200, "path-to-some-long-name-" * 200, "x.y.z" * 1000])
def test_a_long_ordinary_token_stays_intact(token: str) -> None:
    assert _redact.redact(f"before {token} after") == f"before {token} after"


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("line", [
    'password: "first line\nsecond line zzcanary" user: kept',
    '{"password": "first\n\nzzcanary\nlast", "user": "kept"}',
    "secret='one\ntwo zzcanary' user: kept"])
def test_a_quoted_credential_value_that_spans_lines_is_redacted_whole(line: str) -> None:
    redacted = _redact.redact(line)
    assert "zzcanary" not in redacted and "first" not in redacted and "one" not in redacted, redacted
    assert "kept" in redacted


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("line", [
    'password: "never closed zzcanary and more',
    "token='never closed\nzzcanary on line two",
    'before password="unterminated zzcanary'])
def test_an_unterminated_quote_after_a_credential_key_redacts_through_the_end(line: str) -> None:
    assert "zzcanary" not in _redact.redact(line)

"""The shared redactor stays linear on hostile input and covers multiline and unterminated quotes."""

import subprocess
import sys
import time

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


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("unit", ['password"x"', "password: x ", "a_key=v ", '{"refreshToken": "x"} '],
                         ids=["adjacent-quotes", "spaced-pairs", "keys", "json"])
def test_the_cost_of_many_credentials_grows_linearly_with_the_length(unit: str) -> None:
    """Each sample runs for at least 100 ms of CPU time, and the two sizes alternate, so noise hits both alike."""
    texts = {count: unit * count + "cookie=Z" for count in (3000, 6000)}

    def per_run_seconds(text: str) -> float:
        runs, begin = 0, time.process_time()
        while runs == 0 or time.process_time() - begin < 0.1:
            _ = _redact.redact(text)
            runs += 1
        return (time.process_time() - begin) / runs

    best = dict.fromkeys(texts, float("inf"))
    for _ in range(3):
        for count, text in texts.items():
            best[count] = min(best[count], per_run_seconds(text))
    small, large = best[3000], best[6000]
    assert large <= 3 * small, f"doubling the input multiplied the cost by {large / small:.1f}"


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("sentence", [
    "The token's scope is narrow. Keep this explanation.",
    "The secret's rotation is monthly. Keep this explanation.",
    "The password's length is eight. Keep this explanation.",
    "We don't log it. Keep this explanation.",
    "The tokens' scope is narrow. Keep this explanation."])
def test_a_possessive_or_contraction_does_not_hide_the_rest_of_the_prose(sentence: str) -> None:
    """Over-redaction wins where prose and a credential look alike: `secrets' two words` after a plural key stays
    prose, and a credential that starts with `'s ` or `'t ` after its key stays prose."""
    assert _redact.redact(sentence).endswith(". Keep this explanation.")


@pytest.mark.ac("gh-501:AC-4")
def test_a_possessive_before_a_quoted_credential_keeps_the_prose_and_redacts_the_credential() -> None:
    redacted = _redact.redact("The secret's scope is narrow, password: 'two words zzcanary' kept")
    assert "zzcanary" not in redacted and "two words" not in redacted, redacted
    assert redacted.endswith(" kept") and "scope is narrow" in redacted, redacted


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("line", [
    "password'first zzcanary last' kept",
    "password'first\nzzcanary last' kept",
    "password'first zzcanary last"],
    ids=["closed", "multiline", "unterminated"])
def test_a_single_quoted_credential_right_after_its_key_is_redacted_whole(line: str) -> None:
    redacted = _redact.redact(line)
    assert "zzcanary" not in redacted and "first" not in redacted and "last" not in redacted, redacted


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("line", ["token: 'two words zzcanary'", "token 'two words zzcanary'", "token='two words zzcanary'"])
def test_a_single_quoted_value_after_a_key_is_still_redacted_whole(line: str) -> None:
    assert _redact.redact(line + " kept") == "[redacted] kept"

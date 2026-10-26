"""The shared redactor stays linear on hostile input and covers multiline and unterminated quotes."""

import json
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
@pytest.mark.parametrize("key", ["password", "token", "api_key", "secret", "authorization"])
@pytest.mark.parametrize("layers", [0, 1, 2, 3])
def test_a_credential_in_json_text_that_is_encoded_again_is_redacted(key: str, layers: int) -> None:
    text = json.dumps({key: "zzcanary", "user": "kept"})
    for _ in range(layers):
        text = json.dumps(text)[1:-1]
    redacted = _redact.redact(json.dumps({"SKILL.md": text}))
    assert "zzcanary" not in redacted, redacted


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("line", [
    'password: "never closed zzcanary and more',
    "token='never closed\nzzcanary on line two",
    'before password="unterminated zzcanary'])
def test_an_unterminated_quote_after_a_credential_key_redacts_through_the_end(line: str) -> None:
    assert "zzcanary" not in _redact.redact(line)


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("unit", ['password"x"', "password: x ", "a_key=v ", '{"refreshToken": "x"} ', "secret's ",
                                  "secret's 'a' "],
                         ids=["adjacent-quotes", "spaced-pairs", "keys", "json", "possessives", "possessives-then-quotes"])
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
    "The password's length is eight. Keep this explanation."])
def test_a_lowercase_possessive_with_no_later_quote_keeps_the_prose(sentence: str) -> None:
    redacted = _redact.redact(sentence)
    assert redacted.startswith("The [redacted] ") and redacted.endswith(". Keep this explanation."), redacted


@pytest.mark.ac("gh-501:AC-4")
def test_the_possessive_keeps_the_exact_prose_after_it() -> None:
    assert _redact.redact("The token's scope is narrow. Keep this explanation.") == (
        "The [redacted] scope is narrow. Keep this explanation.")
    assert _redact.redact("secret's") == "[redacted]"


@pytest.mark.ac("gh-501:AC-4")
def test_a_contraction_without_a_credential_key_stays_intact() -> None:
    assert _redact.redact("We don't log it. Keep this explanation.") == "We don't log it. Keep this explanation."


@pytest.mark.ac("gh-501:AC-4")
def test_a_possessive_before_a_quoted_credential_redacts_the_credential() -> None:
    """The value that starts at `'s` runs to the end: some prose goes, and no credential stays."""
    redacted = _redact.redact("The secret's scope is narrow, password: 'two words zzcanary' kept")
    assert "zzcanary" not in redacted and redacted.startswith("The [redacted]"), redacted


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("head", [
    "password't zzcanary'",
    "token'd- zzcanary'",
    "api_key'S zzcanary'",
    "tokens' zzcanary'",
    "PASSWORD's zzcanary'",
    "token'S zzcanary'",
    "Token's zzcanary'",
    "my-token's zzcanary'",
    "token's1 zzcanary'"])
@pytest.mark.parametrize("shape", ["closed", "multiline", "unterminated"])
def test_a_quote_that_only_looks_like_an_apostrophe_opens_a_value(head: str, shape: str) -> None:
    line = {"closed": f"{head} kept", "multiline": f"{head.replace(' ', chr(10), 1)} kept",
            "unterminated": head.removesuffix("'")}[shape]
    redacted = _redact.redact(line)
    assert "zzcanary" not in redacted, redacted


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


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("head", [
    "password'one\\' two zzcanary'",
    "password'a=' token'two zzcanary'",
    "'password''a=' token'two zzcanary'",
    "token'x\\' password'y\\' zzcanary'"],
    ids=["escaped-quote", "later-key", "quoted-key", "two-escapes"])
@pytest.mark.parametrize("shape", ["closed", "multiline", "unterminated"])
def test_an_adjacent_quote_never_ends_early_at_an_escaped_or_later_quote(head: str, shape: str) -> None:
    line = {"closed": f"{head} kept", "multiline": f"{head.replace(' ', chr(10), 1)} kept",
            "unterminated": head.removesuffix("'")}[shape]
    redacted = _redact.redact(line)
    assert "zzcanary" not in redacted, redacted


ESCAPED_OPENINGS = [
    r'password=\"one\\\" two zzcanary\" kept',
    r'password=\\"one two zzcanary\\" kept',
    r"password=\'one two zzcanary\' kept",
    r"token: \\'one two zzcanary\\' kept",
    r'{\"secret\": \"one \\\" two zzcanary\"}',
]


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize("line", ESCAPED_OPENINGS)
def test_an_escaped_quote_opening_a_credential_value_redacts_through_the_end(line: str) -> None:
    redacted = _redact.redact(line)
    assert "zzcanary" not in redacted and "two" not in redacted, redacted


@pytest.mark.ac("gh-501:AC-4")
def test_an_unescaped_quoted_value_still_stops_at_its_close() -> None:
    assert _redact.redact('password="one two" kept') == "[redacted] kept"


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize(("key", "expected"), [("password.md", True), ("api_key", True), ("Authorization", True),
                                               ("SKILL.md", False), ("@hypothesis", False)])
def test_is_credential_key_names_the_keys_whose_whole_value_goes(key: str, expected: bool) -> None:
    assert _redact.is_credential_key(key) is expected


DOTTED_AND_ESCAPED_KEYS = [
    r'password.md=\"one two zzcanary\" kept',
    r'my.secret.json=\"one two zzcanary\" kept',
    'password.md="one two zzcanary" kept',
    'token.txt: "one two zzcanary" kept',
    '{"password.md": "one two zzcanary"} kept',
    "'api_key.v2': 'one two zzcanary' kept"]


@pytest.mark.ac("gh-512:AC-1")
@pytest.mark.parametrize("line", DOTTED_AND_ESCAPED_KEYS)
def test_a_dotted_or_file_like_credential_key_redacts_its_whole_quoted_value(line: str) -> None:
    redacted = _redact.redact(line)
    assert "zzcanary" not in redacted and "two" not in redacted, redacted


@pytest.mark.ac("gh-512:AC-2")
@pytest.mark.parametrize("key", ["password", "api_key", "password.md", "my.secret.json"])
@pytest.mark.parametrize("layers", [2, 3, 4])
@pytest.mark.parametrize("shape", [
    '{"%s": "one two zzcanary"} kept',
    '"%s": "one two zzcanary" kept',
    '%s="one two zzcanary" kept',
    '"%s"="one two zzcanary" kept'])
def test_a_quoted_credential_key_at_two_or_more_escape_layers_redacts_its_value(key: str, layers: int, shape: str) -> None:
    text = shape % key
    for _ in range(layers):
        text = json.dumps(text)[1:-1]
    redacted = _redact.redact(text)
    assert "zzcanary" not in redacted and "two" not in redacted, redacted


APOSTROPHES = ["'", "’", "‘"]


@pytest.mark.ac("gh-512:AC-3")
@pytest.mark.parametrize("mark", APOSTROPHES)
@pytest.mark.parametrize("key", ["password", "token", "api_key", "secret"])
@pytest.mark.parametrize("shape", [
    "{key}{q} one two zzcanary",
    "{key}{q}one two zzcanary{q} kept",
    "{key}: {q}one two zzcanary{q} kept",
    "{key}={q}one two zzcanary",
    "{key}{q}one\ntwo zzcanary"])
def test_a_unicode_apostrophe_after_a_credential_key_redacts_like_an_ascii_quote(mark: str, key: str, shape: str) -> None:
    redacted = _redact.redact(shape.format(key=key, q=mark))
    assert "zzcanary" not in redacted and "two" not in redacted, redacted


@pytest.mark.ac("gh-512:AC-3")
def test_a_unicode_possessive_keeps_the_prose_like_an_ascii_one() -> None:
    sentence = "The token’s scope is narrow. Keep this explanation."
    assert _redact.redact(sentence) == _redact.redact(sentence.replace("’", "'"))
    assert _redact.redact(sentence).endswith(". Keep this explanation.")


ADVERSARIAL_UNIT = [
    "password.md=\\\"a ",
    "password’ a ",
    "\\\\\\\"my.secret.json\\\\\\\": ",
    "password." + "a." * 8 + " ",
    "token’s 'a' "]


@pytest.mark.ac("gh-512:AC-4")
@pytest.mark.parametrize("unit", ADVERSARIAL_UNIT)
def test_redact_stays_linear_on_dotted_escaped_and_unicode_keys(unit: str) -> None:
    """Quadrupling a 400 KB input must not multiply the CPU time by more than 8 (linear is 4, quadratic is 16)."""
    small = unit * (400_000 // len(unit))
    large = unit * (1_600_000 // len(unit))

    def best_seconds(text: str) -> float:
        best = float("inf")
        for _ in range(5):
            begin = time.process_time()
            _ = _redact.redact(text)
            best = min(best, time.process_time() - begin)
        return best

    fast, slow = best_seconds(small), best_seconds(large)
    assert slow < DEADLINE_SECONDS, f"1.6 MB took {slow:.1f}s"
    assert slow <= 8 * max(fast, 0.01), f"quadrupling the input multiplied the cost by {slow / max(fast, 0.01):.1f}"

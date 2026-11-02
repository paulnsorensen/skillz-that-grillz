"""The shared redactor stays linear on hostile input and covers multiline and unterminated quotes."""

import json
import subprocess
import sys
from typing import cast

import pytest
from cpu_growth import LIMIT, STEP, best_seconds

from skillz_experiments import _redact

DEADLINE_SECONDS = 15


HOSTILE = {
    "word": "a" * 65536,
    "underscores": "a_" * 32768,
    "hyphens": "a-" * 32768,
    "keyword-hyphens": "password" + "-a" * 32768,
    "equals": "=" * 65536,
    "colon-equals": ":=" * 32768,
    "dots": "a." * 32768,
    "jwt-dots": "eyJ" + "a." * 32768}
# One child redacts every input and prints the seconds each one takes, so each input keeps its own deadline.
TIMED = ("import json, sys, time\nfrom skillz_experiments._redact import redact\n"
         "for name, text in json.load(sys.stdin).items():\n"
         "    start = time.perf_counter()\n    redact(text)\n    print(json.dumps([name, time.perf_counter() - start]), flush=True)\n")


@pytest.mark.ac("gh-501:AC-4")
def test_redacting_64_kib_without_whitespace_finishes_before_the_deadline() -> None:
    try:
        done = subprocess.run([sys.executable, "-c", TIMED], input=json.dumps(HOSTILE), text=True, capture_output=True,
                              timeout=DEADLINE_SECONDS * len(HOSTILE), check=False)
    except subprocess.TimeoutExpired:
        pytest.fail(f"redact took longer than {DEADLINE_SECONDS}s on a hostile input of 64 KiB")
    assert done.returncode == 0, done.stderr
    seconds = dict(cast(list[tuple[str, float]], [json.loads(line) for line in done.stdout.splitlines()]))
    assert sorted(seconds) == sorted(HOSTILE)
    assert {name: took for name, took in seconds.items() if took >= DEADLINE_SECONDS} == {}


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
@pytest.mark.parametrize(("key", "layers"), [
    ("password", 0), ("token", 1), ("api_key", 2), ("secret", 3),
    ("authorization", 0), ("password", 1), ("token", 2), ("authorization", 3)])
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
    """16 times the credentials must not cost more than 64 times the CPU time (linear is 16, quadratic is 256)."""
    small, large = best_seconds(_redact.redact, unit * 2000 + "cookie=Z", unit * 2000 * STEP + "cookie=Z")
    assert large <= LIMIT * small, f"{STEP} times the input multiplied the cost by {large / small:.1f}"


@pytest.mark.ac("gh-501:AC-4")
def test_a_lowercase_possessive_with_no_later_quote_keeps_the_prose() -> None:
    redacted = _redact.redact("The secret's rotation is monthly. Keep this explanation.")
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


LOOKALIKE_HEADS = [
    "password't zzcanary'",
    "token'd- zzcanary'",
    "api_key'S zzcanary'",
    "tokens' zzcanary'",
    "PASSWORD's zzcanary'",
    "token'S zzcanary'",
    "Token's zzcanary'",
    "my-token's zzcanary'",
    "token's1 zzcanary'"]


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize(("head", "shape"), [
    *[(head, "closed") for head in LOOKALIKE_HEADS],
    (LOOKALIKE_HEADS[0], "multiline"), (LOOKALIKE_HEADS[4], "multiline"),
    (LOOKALIKE_HEADS[1], "unterminated"), (LOOKALIKE_HEADS[8], "unterminated")])
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


ADJACENT_HEADS = {
    "escaped-quote": "password'one\\' two zzcanary'",
    "later-key": "password'a=' token'two zzcanary'",
    "quoted-key": "'password''a=' token'two zzcanary'",
    "two-escapes": "token'x\\' password'y\\' zzcanary'"}


@pytest.mark.ac("gh-501:AC-4")
@pytest.mark.parametrize(("name", "shape"), [
    *[(name, "closed") for name in ADJACENT_HEADS],
    ("escaped-quote", "multiline"), ("two-escapes", "multiline"), ("later-key", "unterminated"), ("quoted-key", "unterminated")])
def test_an_adjacent_quote_never_ends_early_at_an_escaped_or_later_quote(name: str, shape: str) -> None:
    head = ADJACENT_HEADS[name]
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


ESCAPE_SHAPES = [
    '{"%s": "one two zzcanary"} kept',
    '"%s": "one two zzcanary" kept',
    '%s="one two zzcanary" kept',
    '"%s"="one two zzcanary" kept']


@pytest.mark.ac("gh-512:AC-2")
@pytest.mark.parametrize(("key", "layers", "shape"), [
    *[(key, 2, shape) for key in ("password", "api_key", "password.md", "my.secret.json") for shape in ESCAPE_SHAPES],
    *[("password.md", layers, shape) for layers in (3, 4) for shape in ESCAPE_SHAPES]])
def test_a_quoted_credential_key_at_two_or_more_escape_layers_redacts_its_value(key: str, layers: int, shape: str) -> None:
    text = shape % key
    for _ in range(layers):
        text = json.dumps(text)[1:-1]
    redacted = _redact.redact(text)
    assert "zzcanary" not in redacted and "two" not in redacted, redacted


APOSTROPHES = ["'", "’", "‘"]
APOSTROPHE_KEYS = ["password", "token", "api_key", "secret"]
APOSTROPHE_SHAPES = [
    "{key}{q} one two zzcanary",
    "{key}{q}one two zzcanary{q} kept",
    "{key}: {q}one two zzcanary{q} kept",
    "{key}={q}one two zzcanary",
    "{key}{q}one\ntwo zzcanary"]


@pytest.mark.ac("gh-512:AC-3")
@pytest.mark.parametrize(("mark", "key", "shape"), [
    (mark, APOSTROPHE_KEYS[(number + index) % len(APOSTROPHE_KEYS)], shape)
    for number, mark in enumerate(APOSTROPHES) for index, shape in enumerate(APOSTROPHE_SHAPES)])
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
    """16 times a 200 KB input must not cost more than 64 times the CPU time (linear is 16, quadratic is 256).

    The large input is 3.2 MB, so a quadratic part that is more than two times the linear part at 1.6 MB still fails
    the test.
    """
    count = 200_000 // len(unit)
    small, large = best_seconds(_redact.redact, unit * count, unit * count * STEP)
    assert large < DEADLINE_SECONDS, f"3.2 MB took {large:.1f}s"
    assert large <= LIMIT * small, f"{STEP} times the input multiplied the cost by {large / small:.1f}"

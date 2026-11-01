"""Property-based checks of the harness parsers for Claude Code output and role configuration, with inputs that Hypothesis generates."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from random import Random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from skillz_experiments._cases import CodedError
from skillz_experiments._runtime import SANDBOX_SECONDS
from skillz_experiments.harness._claude._events import parse_events
from skillz_experiments.harness._claude._options import EFFORTS, OPTION_FIELDS, SANDBOX_SECONDS_LIMIT, ClaudeOptions
from skillz_experiments.harness._codex import CODEX_IDENTITY
from skillz_experiments.harness._roles import CLAUDE_IDENTITY, Role, parse_role

INVALID_STREAM = "^invalid Claude Code JSON event stream$"
# Text with the characters that a line-oriented parser can split on. JSON escapes only `\r` and `\n` of them.
TEXT = st.text(st.characters() | st.sampled_from(["\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}", "\x85", "\x0b", "\x0c", "\x1c", "\r", "\n"]),
               max_size=8)
SCALARS = st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | TEXT
JSON: st.SearchStrategy[object] = st.recursive(
    SCALARS, lambda inner: st.lists(inner, max_size=3) | st.dictionaries(TEXT, inner, max_size=3), max_leaves=10)
EVENTS: st.SearchStrategy[dict[str, object]] = st.dictionaries(TEXT, JSON, max_size=4)
NEWLINES = st.sampled_from(["\n", "\r\n", "\r"])
BLANKS = st.sampled_from(["", " ", "\t", " \t "])


def _line(event: dict[str, object], ascii_only: bool) -> str:
    # Claude Code is a Node program: `JSON.stringify` writes U+2028, U+2029, and U+0085 unescaped.
    return json.dumps(event, ensure_ascii=ascii_only)


@st.composite
def _streams(draw: st.DrawFn) -> tuple[str, list[dict[str, object]]]:
    """Draw event lines with blank lines between them, mixed line endings, and an optional final line ending."""
    events = draw(st.lists(EVENTS, max_size=5))
    lines = [draw(BLANKS) for _ in range(draw(st.integers(0, 2)))]
    for event in events:
        lines.append(_line(event, draw(st.booleans())))
        lines += [draw(BLANKS) for _ in range(draw(st.integers(0, 2)))]
    stream = "".join(line + draw(NEWLINES) for line in lines)
    return (stream if draw(st.booleans()) else stream.rstrip("\r\n")), events


@st.composite
def _bad_lines(draw: st.DrawFn) -> str:
    """Draw one nonblank line that is not a JSON object: a truncated object, another JSON value, or garbage."""
    whole = _line(draw(EVENTS), draw(st.booleans()))
    truncated = whole[:draw(st.integers(1, len(whole) - 1))]
    other = json.dumps(draw(SCALARS | st.lists(JSON, max_size=3)))
    garbage = draw(st.sampled_from(["not json", "{'a': 1}", '{"a": 1}}', "{" * 100_000, "[" * 100_000, "}"]))
    return draw(st.sampled_from([truncated, other, garbage]))


@given(_streams())
def test_parse_events_reads_every_object_line_in_order(case: tuple[str, list[dict[str, object]]]) -> None:
    stream, events = case

    parsed = parse_events(stream)

    assert parsed == events
    assert [_line(event, True) for event in parsed] == [_line(event, True) for event in events]


@given(_streams(), _bad_lines(), st.data())
def test_parse_events_refuses_the_whole_stream_when_one_line_is_not_a_json_object(
        case: tuple[str, list[dict[str, object]]], bad: str, data: st.DataObject) -> None:
    lines = case[0].split("\n")
    lines.insert(data.draw(st.integers(0, len(lines))), bad)

    with pytest.raises(RuntimeError, match=INVALID_STREAM):
        _ = parse_events("\n".join(lines))


@given(st.lists(st.text() | _bad_lines() | st.builds(_line, EVENTS, st.booleans()), max_size=4),
       st.lists(NEWLINES | st.sampled_from(["\N{LINE SEPARATOR}", "\x0c", ""]), min_size=4, max_size=4))
def test_parse_events_returns_only_dicts_or_raises_only_the_stream_error_on_arbitrary_text(
        lines: list[str], endings: list[str]) -> None:
    stream = "".join(line + ending for line, ending in zip(lines, endings, strict=False))
    try:
        parsed = parse_events(stream)
    except RuntimeError as error:
        assert str(error) == "invalid Claude Code JSON event stream"
        assert error.__cause__ is None and error.__suppress_context__  # `from None` keeps the parser error out of the traceback.
        return
    assert all(type(event) is dict and all(type(key) is str for key in event) for event in parsed)


ABSOLUTE = st.lists(st.sampled_from(["a", "b", ".", "..", "", "opt"]), max_size=4).map(lambda parts: "/" + "/".join(parts))
VALID_SECONDS = st.integers(1, SANDBOX_SECONDS_LIMIT)
OPTION_ERRORS = ("effort must be one of", "sandbox_read must be a list", "sandbox_seconds must be an integer")


@st.composite
def _options(draw: st.DrawFn) -> tuple[dict[str, object], list[str]]:
    """Draw a valid option mapping and its read roots. Each field is optional, and the read roots are a list or a tuple."""
    value: dict[str, object] = {}
    roots = draw(st.lists(ABSOLUTE, max_size=4))
    if draw(st.booleans()):
        value["effort"] = draw(st.none() | st.sampled_from(EFFORTS))
    if roots or draw(st.booleans()):
        value["sandbox_read"] = tuple(roots) if draw(st.booleans()) else roots
    if draw(st.booleans()):
        value["sandbox_seconds"] = draw(VALID_SECONDS)
    return value, roots


@given(_options(), st.dictionaries(TEXT.filter(lambda key: key not in OPTION_FIELDS), JSON, max_size=2), st.randoms())
def test_claude_options_accept_every_valid_mapping_and_normalize_the_read_roots(
        case: tuple[dict[str, object], list[str]], other: dict[str, object], rng: Random) -> None:
    value, roots = case

    options = ClaudeOptions.parse(other | value)

    assert options.effort == value.get("effort")
    assert options.sandbox_seconds == value.get("sandbox_seconds", SANDBOX_SECONDS)
    assert list(options.sandbox_read) == sorted({os.path.normpath(root) for root in roots})
    assert ClaudeOptions.parse(options.data()) == options
    reordered = [*roots, *roots]
    rng.shuffle(reordered)
    assert ClaudeOptions.parse(value | {"sandbox_read": reordered}) == options


RELATIVE = st.text().filter(lambda text: not text.startswith("/"))


def _with_bad_root(roots: list[str], bad: object) -> list[object]:
    return [*roots, bad]


# Each strategy draws a boundary value half of the time, so a derandomized run of 100 examples still reaches each one.
BAD_FIELDS: dict[str, st.SearchStrategy[object]] = {
    "effort": st.sampled_from(["", "HIGH", "none", " low", 1, True, ["low"], {"low": 1}])
              | st.one_of(st.text().filter(lambda text: text not in EFFORTS), st.integers(), st.lists(st.sampled_from(EFFORTS))),
    "sandbox_read": st.sampled_from(["/a", "", None, 0, {"/a": "/a"}])
                    | st.builds(_with_bad_root, st.lists(ABSOLUTE, max_size=2),
                                st.sampled_from(["", "a", "a/b", None, 1, ["/a"]]) | RELATIVE),
    "sandbox_seconds": st.sampled_from([True, False, 0, -1, SANDBOX_SECONDS_LIMIT + 1, 1.0, "60", None])
                       | st.one_of(st.integers(max_value=0), st.integers(min_value=SANDBOX_SECONDS_LIMIT + 1), st.floats(),
                                   st.text(max_size=3)),
}


@pytest.mark.parametrize("field", sorted(BAD_FIELDS))
def test_claude_options_refuse_a_mapping_with_one_invalid_field(field: str) -> None:
    @given(_options(), BAD_FIELDS[field])
    def check(case: tuple[dict[str, object], list[str]], bad: object) -> None:
        with pytest.raises(ValueError, match=f"^{field} must be "):
            _ = ClaudeOptions.parse(case[0] | {field: bad})

    check()


OPTION_VALUES = (st.sampled_from(EFFORTS) | VALID_SECONDS | st.lists(ABSOLUTE, max_size=3) | TEXT
                 | st.sampled_from([True, 0, SANDBOX_SECONDS_LIMIT + 1, None, "/a"]) | st.tuples(ABSOLUTE, RELATIVE) | JSON)


@given(st.dictionaries(st.sampled_from(sorted(OPTION_FIELDS)) | TEXT, OPTION_VALUES, max_size=4))
def test_claude_options_return_valid_options_or_raise_only_value_error_on_an_arbitrary_mapping(value: dict[str, object]) -> None:
    try:
        options = ClaudeOptions.parse(value)
    except ValueError as error:
        assert type(error) is ValueError
        assert str(error).startswith(OPTION_ERRORS)
        return
    assert options.effort is None or options.effort in EFFORTS
    assert type(options.sandbox_seconds) is int and 1 <= options.sandbox_seconds <= SANDBOX_SECONDS_LIMIT
    assert list(options.sandbox_read) == sorted(set(options.sandbox_read))
    assert all(Path(root).is_absolute() and os.path.normpath(root) == root for root in options.sandbox_read)


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return a configuration directory that holds a `codex` executable, so a Codex role resolves without Codex on PATH."""
    folder = tmp_path_factory.mktemp("roles")
    codex = folder / "codex"
    _ = codex.write_text("#!/bin/sh\n")
    codex.chmod(0o755)
    return folder


PYTHON = str(Path(sys.executable).absolute())
LONG = st.integers(250, 300).map(lambda size: "x" * size)
MODELS = st.builds(str.__add__, st.sampled_from(["gpt-", "claude-", "m"]), st.text(max_size=8))
# Arguments that name no file in `root`, so the role keeps them as they are.
# A long option value makes a path name longer than the 255-byte limit of a file name.
ARGUMENTS = st.lists(st.from_regex(r"-?[ab]{1,4}|--[ab]{1,3}=[ab]{1,3}", fullmatch=True) | LONG.map("--prompt=".__add__),
                     max_size=3)


@given(_options(), MODELS, st.none() | MODELS, st.booleans())
def test_parse_role_accepts_a_valid_claude_role(case: tuple[dict[str, object], list[str]], default: str, model: str | None,
                                               path_only: bool) -> None:
    options = case[0]
    value: dict[str, object] = {"adapter": "claude", "command": [PYTHON], **options} | ({} if model is None else {"model": model})

    role = parse_role(value, default, Path("/nonexistent-root"), path_only=path_only)

    assert role == Role("claude", model or default, (PYTHON,), CLAUDE_IDENTITY, ClaudeOptions.parse(options))


def test_parse_role_accepts_a_valid_command_role(root: Path) -> None:
    @given(MODELS, st.none() | MODELS, ARGUMENTS, MODELS)
    def check(default: str, model: str | None, arguments: list[str], identity: str) -> None:
        value: dict[str, object] = {"adapter": "command", "command": [PYTHON, *arguments], "identity": identity}

        role = parse_role(value | ({} if model is None else {"model": model}), default, root)

        assert role == Role("command", model or default, (PYTHON, *arguments), identity)

    check()


FOREIGN_FIELDS = [("codex", field) for field in ("command", "identity", *sorted(OPTION_FIELDS))] + [
    ("command", field) for field in sorted(OPTION_FIELDS)]
REFUSALS = {"codex": "^Codex role accepts only adapter and model$",
            "command": "^effort, sandbox_read, and sandbox_seconds apply only to a claude role$"}


@given(st.sampled_from(FOREIGN_FIELDS), OPTION_VALUES)
def test_parse_role_refuses_a_field_that_the_adapter_does_not_take(case: tuple[str, str], given_value: object) -> None:
    adapter, field = case
    value: dict[str, object] = {"adapter": adapter}
    if adapter == "command":
        value.update(command=[PYTHON], identity="tool")

    with pytest.raises(ValueError, match=REFUSALS[adapter]):
        _ = parse_role(value | {field: given_value}, "m", Path("/nonexistent-root"))


def test_parse_role_accepts_a_valid_codex_role(root: Path) -> None:
    @given(st.booleans(), MODELS, st.none() | MODELS)
    def check(named: bool, default: str, model: str | None) -> None:
        value: dict[str, object] = {"adapter": "codex"} if named else {}
        if model is not None:
            value["model"] = model

        role = parse_role(value, default, root)

        assert (role.adapter, role.model, role.identity, role.options) == ("codex", model or default, CODEX_IDENTITY, ClaudeOptions())
        assert len(role.command) == 1 and Path(role.command[0]).is_absolute() and Path(role.command[0]).name == "codex"

    check()


ROLE_KEYS = st.sampled_from(["adapter", "command", "identity", "model", "isolation", *sorted(OPTION_FIELDS)])
COMMANDS = st.lists(st.just(PYTHON) | st.just("codex") | LONG | TEXT | st.integers(), max_size=3)
ROLE_VALUES = (OPTION_VALUES | st.sampled_from(["claude", "codex", "command", "nono", "", " "]) | MODELS | COMMANDS
               | st.just([PYTHON]))


@st.composite
def _role_mappings(draw: st.DrawFn) -> dict[str, object]:
    """Draw a role mapping that is often valid for its adapter, then sometimes set one key to any value."""
    adapter = draw(st.sampled_from(["claude", "codex", "command", None]))
    value: dict[str, object] = {} if adapter is None else {"adapter": adapter}
    if draw(st.booleans()):
        value["model"] = draw(MODELS)
    executable = draw(st.sampled_from([[PYTHON], []]) | st.lists(LONG, min_size=1, max_size=1))
    if adapter == "claude":
        value |= draw(_options())[0] | ({"command": executable} if draw(st.booleans()) else {})
    if adapter == "command":
        value |= {"command": executable + draw(ARGUMENTS), "identity": draw(MODELS)}
    if draw(st.booleans()):
        value |= draw(st.dictionaries(ROLE_KEYS | TEXT, ROLE_VALUES, min_size=1, max_size=1))
    return value


ROLE_FIELDS = {"codex": {"adapter", "model"}, "claude": {"adapter", "model", "command", *OPTION_FIELDS},
               "command": {"adapter", "model", "command", "identity"}}


def test_parse_role_returns_a_role_or_raises_only_value_error_on_an_arbitrary_mapping(root: Path) -> None:
    @given(_role_mappings(), st.sampled_from(["", " ", "\t"]) | MODELS | TEXT, st.booleans())
    def check(value: dict[str, object], default: str, path_only: bool) -> None:
        try:
            role = parse_role(value, default, root, path_only=path_only)
        except ValueError:
            return
        assert role.adapter == value.get("adapter", "codex")
        assert set(value) <= ROLE_FIELDS[role.adapter]
        assert role.model == value.get("model", default) and role.model.strip() and role.identity.strip()
        assert role.command and Path(role.command[0]).is_absolute()
        assert role.adapter == "claude" or role.options == ClaudeOptions()

    check()


@given(st.dictionaries(ROLE_KEYS | TEXT, ROLE_VALUES, max_size=3), ROLE_VALUES)
def test_parse_role_refuses_the_removed_isolation_key_with_its_code(value: dict[str, object], isolation: object) -> None:
    with pytest.raises(CodedError) as caught:
        _ = parse_role(value | {"isolation": isolation}, "m", Path("/nonexistent-root"))

    assert caught.value.code == ("nono-removed" if isolation == "nono" else "isolation-removed")

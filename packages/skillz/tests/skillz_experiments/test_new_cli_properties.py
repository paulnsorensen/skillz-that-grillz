"""Property-based checks of `parse_value`, which reads the model-written TOML of a `@new-cli` value."""
from __future__ import annotations

import keyword
import tomllib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from skillz_experiments._candidate import TEXT_FILE_LIMIT
from skillz_experiments._cases import CodedError
from skillz_experiments._new_cli import NAME_PATTERN, VALUE_LIMIT, NewCli, parse_value

CODES = frozenset({"new-cli-module-size", "new-cli-malformed", "new-cli-second-target", "new-cli-extra-key",
                   "new-cli-missing-key", "new-cli-bad-name"})
_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}
# Python refuses to read an integer of more than 4300 digits, so these values sit on both sides of that limit.
_INT_DIGITS = 4300


def _quoted(text: str) -> str:
    """Return `text` as a TOML basic string."""
    return '"' + "".join(_ESCAPES.get(char) or (f"\\u{ord(char):04x}" if ord(char) < 0x20 or ord(char) == 0x7F else char)
                         for char in text) + '"'


def _array(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def _inline(table: dict[str, str]) -> str:
    return "{" + ", ".join(f"{_quoted(key)} = {value}" for key, value in table.items()) + "}"


def _document(table: dict[str, str]) -> str:
    """Return a TOML document from a map of each key to the TOML text of its value."""
    return "".join(f"{_quoted(key)} = {value}\n" for key, value in table.items())


def _long(size: int) -> str:
    """Return the TOML string of a module of `size` characters, without the slow escape of each character."""
    return '"' + "x" * size + '"'


def _outcome(value: str) -> NewCli | str:
    """Return the parsed value, or the code of its `CodedError`. Any other exception fails the test."""
    try:
        return parse_value(value)
    except CodedError as error:
        return error.code


NAMES = st.from_regex(NAME_PATTERN, fullmatch=True).filter(lambda name: not keyword.iskeyword(name.replace("-", "_")))
KEYWORD_NAMES = st.sampled_from([word for word in keyword.kwlist if NAME_PATTERN.fullmatch(word)])
# Python source of any text with at least one character that `str.strip` keeps.
MODULES = st.tuples(st.text(max_size=15), st.characters(exclude_categories=("Z", "Cc", "Cs")),
                    st.text(max_size=15)).map("".join)
NOT_TEXT = st.one_of(st.integers().map(str), st.booleans().map(lambda flag: str(flag).lower()),
                     st.floats(allow_nan=False, allow_infinity=False).map(repr))
SCALARS = st.text(max_size=10).map(_quoted) | NOT_TEXT
# The TOML text of a value of any shape that TOML reads.
VALUES = st.recursive(SCALARS, lambda inner: st.lists(inner, max_size=3).map(_array)
                      | st.dictionaries(st.text(max_size=5), inner, max_size=3).map(_inline), max_leaves=6)
HUGE_INTS = st.integers(_INT_DIGITS - 3, _INT_DIGITS + 3).map(lambda digits: "7" * digits)
ANY_VALUES = VALUES | HUGE_INTS
EXTRA_KEYS = st.text(max_size=6).filter(lambda key: key not in {"name", "module", "target"})
TOMLISH = st.text(alphabet=st.sampled_from(list('namemodule="\'[]{}.,#1 \n')), max_size=40)


@st.composite
def _tables(draw: st.DrawFn) -> dict[str, str]:
    """Draw a table whose name and module are each valid, any value, or absent, and that may hold other keys."""
    table: dict[str, str] = {}
    for key, valid in (("name", NAMES.map(_quoted)), ("module", MODULES.map(_quoted))):
        match draw(st.sampled_from(["valid", "valid", "any", "absent"])):
            case "valid":
                table[key] = draw(valid)
            case "any":
                table[key] = draw(ANY_VALUES)
            case _:
                pass
    if draw(st.booleans()):
        table |= draw(st.dictionaries(st.sampled_from(["name", "module", "target"]) | st.text(max_size=6), ANY_VALUES,
                                      min_size=1, max_size=2))
    return table


@st.composite
def _second_target(draw: st.DrawFn) -> str:
    table = {"name": _quoted(draw(NAMES)), "module": _quoted(draw(MODULES))} | draw(st.dictionaries(EXTRA_KEYS, VALUES, max_size=2))
    match draw(st.sampled_from(["target key", "target table", "name list"])):
        case "target key":
            table["target"] = draw(VALUES)
        case "target table":
            return _document(table) + f"[[target]]\nname = {_quoted(draw(NAMES))}\n"
        case _:
            table["name"] = _array(draw(st.lists(NAMES.map(_quoted) | VALUES, max_size=3)))
    return _document(table)


@st.composite
def _extra_key(draw: st.DrawFn) -> str:
    # A key may be missing too: the extra key is the first fault.
    table = {key: value for key, value in (("name", _quoted(draw(NAMES))), ("module", _quoted(draw(MODULES))))
             if draw(st.booleans())}
    return _document(table | draw(st.dictionaries(EXTRA_KEYS, VALUES, min_size=1, max_size=3)))


@st.composite
def _missing_key(draw: st.DrawFn) -> str:
    # The name is not checked yet, so it may hold any value except a list.
    present = draw(st.sampled_from([(), ("name",), ("module",)]))
    values = {"name": NAMES.map(_quoted) | SCALARS, "module": MODULES.map(_quoted) | VALUES}
    return _document({key: draw(values[key]) for key in present})


@st.composite
def _bad_name(draw: st.DrawFn) -> str:
    # The module is not checked yet, so it may hold any value.
    unmatched = st.text(max_size=45).filter(lambda name: NAME_PATTERN.fullmatch(name) is None)
    name = draw(KEYWORD_NAMES.map(_quoted) | unmatched.map(_quoted) | NOT_TEXT
                | st.dictionaries(st.text(max_size=5), VALUES, max_size=2).map(_inline))
    return _document({"name": name, "module": draw(MODULES.map(_quoted) | VALUES)})


@st.composite
def _malformed(draw: st.DrawFn) -> str:
    valid = {"name": _quoted(draw(NAMES)), "module": _quoted(draw(MODULES))}
    match draw(st.sampled_from(["module not text", "repeated key", "broken", "deep", "huge int"])):
        case "module not text":
            not_text = NOT_TEXT | st.lists(VALUES, max_size=2).map(_array)
            return _document(valid | {"module": draw(not_text)})
        case "repeated key":
            return _document(valid) + f"{draw(st.sampled_from(['name', 'module']))} = {draw(VALUES)}\n"
        case "broken":
            return _document(valid) + draw(st.sampled_from(["=", '"open', "[[", "name", "x = ", "[a\n[a]"]))
        case "deep":
            depth = draw(st.sampled_from([1_000, 5_000, 50_000]))
            return "name = " + "[" * depth + "]" * depth
        case _:
            digits = draw(st.integers(_INT_DIGITS + 1, _INT_DIGITS + 20))
            return _document(valid | {draw(st.sampled_from(["name", "module", "x"])): "7" * digits})


@st.composite
def _module_size(draw: st.DrawFn) -> str:
    name = _quoted(draw(NAMES))
    match draw(st.sampled_from(["blank", "long module", "long value"])):
        case "blank":
            blank = st.text(alphabet=st.sampled_from(list(" \t\n\r\x0b\x0c  　")), max_size=6)
            return _document({"name": name, "module": _quoted(draw(blank))})
        case "long module":
            return _document({"name": name, "module": _long(TEXT_FILE_LIMIT + draw(st.integers(1, 50)))})
        case _:
            # A comment pads the value past its limit around a small, valid module.
            text = _document({"name": name, "module": _quoted(draw(MODULES))})
            return text + "#" + "x" * (VALUE_LIMIT - len(text) + draw(st.integers(0, 50)))


@given(st.text() | TOMLISH)
def test_any_text_gives_a_new_cli_or_a_known_code(value: str) -> None:
    outcome = _outcome(value)

    assert isinstance(outcome, NewCli) or outcome in CODES


@given(_tables())
def test_a_generated_table_gives_a_new_cli_only_when_it_holds_exactly_the_name_and_module(table: dict[str, str]) -> None:
    value = _document(table)

    outcome = _outcome(value)

    if isinstance(outcome, NewCli):
        assert tomllib.loads(value) == {"name": outcome.name, "module": outcome.module}
    else:
        assert outcome in CODES


@given(NAMES, MODULES, st.booleans())
def test_a_well_formed_value_is_accepted_with_its_name_and_module(name: str, module: str, module_first: bool) -> None:
    table = {"name": _quoted(name), "module": _quoted(module)}
    if module_first:
        table = dict(reversed(table.items()))

    cli = parse_value(_document(table))

    assert cli == NewCli(name, module)
    assert cli.path == f"src/{name.replace('-', '_')}/__init__.py"


@pytest.mark.parametrize("size", range(TEXT_FILE_LIMIT - 2, TEXT_FILE_LIMIT + 3))
def test_a_module_is_accepted_up_to_the_text_file_limit(size: int) -> None:
    outcome = _outcome(_document({"name": _quoted("cli"), "module": _long(size)}))

    assert outcome == (NewCli("cli", "x" * size) if size <= TEXT_FILE_LIMIT else "new-cli-module-size")


@pytest.mark.parametrize(("code", "values"), [
    ("new-cli-module-size", _module_size()),
    ("new-cli-malformed", _malformed()),
    ("new-cli-second-target", _second_target()),
    ("new-cli-extra-key", _extra_key()),
    ("new-cli-missing-key", _missing_key()),
    ("new-cli-bad-name", _bad_name()),
])
@given(data=st.data())
def test_a_value_built_for_one_fault_gives_its_code(code: str, values: st.SearchStrategy[str], data: st.DataObject) -> None:
    assert _outcome(data.draw(values)) == code

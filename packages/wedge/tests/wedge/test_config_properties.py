"""Property-based checks of the ``wedge.toml`` parser, with TOML documents that Hypothesis generates."""

from __future__ import annotations

import re
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeAlias

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from wedge._config import ConfigError, WedgeConfig, _parse_table, parse_targets  # pyright: ignore[reportPrivateUsage]

# Each example of the round trip writes a shared-defaults file. The autouse layer-cache fixture of
# conftest.py is function-scoped, and the examples that use tmp_path never write to it.
PROPERTY = settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])

TomlValue: TypeAlias = "str | int | bool | list[TomlValue] | dict[str, TomlValue]"
Table: TypeAlias = dict[str, TomlValue]


def _toml_str(text: str) -> str:
    """A TOML basic string: escape the quote, the backslash, and every control character."""
    escaped = "".join(
        f"\\u{ord(char):04x}" if ord(char) < 0x20 or ord(char) == 0x7F or char in '"\\' else char for char in text
    )
    return f'"{escaped}"'


def _toml_value(value: TomlValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _toml_str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return "{" + ", ".join(f"{_toml_str(key)} = {_toml_value(item)}" for key, item in value.items()) + "}"


def _render(top: Table, targets: Sequence[Table] = ()) -> str:
    lines = [f"{_toml_str(key)} = {_toml_value(value)}" for key, value in top.items()]
    for table in targets:
        lines.append("[[target]]")
        lines.extend(f"{_toml_str(key)} = {_toml_value(value)}" for key, value in table.items())
    return "\n".join(lines) + "\n"


# Text with a non-space character, so `text.strip()` is not empty. Hypothesis pads it with any characters.
TEXT: st.SearchStrategy[str] = st.tuples(
    st.text(st.characters(exclude_categories=("Cs",)), max_size=4),
    st.characters(exclude_categories=("Cc", "Cs", "Zs", "Zl", "Zp")),
    st.text(st.characters(exclude_categories=("Cs",)), max_size=4),
).map("".join)
SAFE_NAMES = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,10}", fullmatch=True)
REPOS = st.from_regex(r"[A-Za-z0-9_.-]{1,8}/[A-Za-z0-9_.-]{1,8}", fullmatch=True)
SELECTORS = st.lists(st.from_regex(r"[a-z_]{1,5}", fullmatch=True), min_size=1, max_size=3).map("/".join)


def _lists(items: st.SearchStrategy[TomlValue], *, min_size: int = 0) -> st.SearchStrategy[TomlValue]:
    """Lists of up to three items, typed as a TOML value."""
    return st.lists(items, min_size=min_size, max_size=3)


# Every key that a file or a [[target]] table can share, with a valid value for it.
SHARED: dict[str, st.SearchStrategy[TomlValue]] = {
    "source": TEXT,
    "repo": REPOS,
    "project": TEXT,
    "source_paths": _lists(SELECTORS, min_size=1),
    "include": _lists(TEXT),
    "groups": _lists(TEXT.filter(lambda group: group != "dev")),
}
REQUIRED_SHARED = ("source", "repo")
TARGET_OVERRIDES = ("source", "source_paths", "include", "groups")

KEYS = st.sampled_from(
    ["name", "entry", "source", "source_paths", "project", "repo", "include", "groups", "target", "group"]
)
VALUES: st.SearchStrategy[TomlValue] = st.recursive(
    st.one_of(TEXT, SAFE_NAMES, REPOS, st.sampled_from(["", " ", "dev", "/abs", "../up"]), st.integers(-3, 3), st.booleans()),
    lambda inner: st.one_of(st.lists(inner, max_size=3), st.dictionaries(KEYS, inner, max_size=5)),
    max_leaves=10,
)
TABLES: st.SearchStrategy[Table] = st.dictionaries(KEYS, VALUES, max_size=8)
# Values at each key's edge of the contract: blank text, unsafe names, the reserved group, unsafe selectors, and
# wrong types.
EDGES: dict[str, list[TomlValue]] = {
    "name": ["", " ", "bad name", "../x"],
    "entry": ["", " ", "\t", 0],
    "source": ["", " ", "\t", True],
    "project": ["", " ", "\t", []],
    "repo": ["", "noslash", "a/b/c", "a b/c"],
    "source_paths": [[], [""], [" "], ["/abs"], ["../up"], ["a/../b"], "pkg"],
    "include": [[""], [" "], [0], "pkg"],
    "groups": [["dev"], [" "], [""], [[]], "runtime"],
    "target": [[], [0], ["x"], "x", {}],
    "group": [["cli"]],
}
EDITS = st.one_of(
    st.sampled_from(sorted(EDGES)).flatmap(lambda key: st.tuples(st.just(key), st.sampled_from(EDGES[key]))),
    st.tuples(KEYS, VALUES),
)


@st.composite
def _valid_case(draw: st.DrawFn) -> tuple[Table, Table, list[Table], tuple[WedgeConfig, ...]]:
    """Shared defaults, the skill's own top level, its [[target]] tables, and the configs they mean.

    The defaults file holds some shared keys, and some of those carry another value that the skill's file shadows.
    """
    values: Table = {key: draw(strategy) for key, strategy in SHARED.items() if key in REQUIRED_SHARED or draw(st.booleans())}
    in_defaults = [key for key in values if draw(st.booleans())]
    defaults: Table = {key: values[key] for key in in_defaults}
    own: Table = {key: value for key, value in values.items() if key not in in_defaults}
    for key in in_defaults:
        if draw(st.booleans()):
            defaults[key] = draw(SHARED[key])
            own[key] = values[key]
    if draw(st.booleans()):
        own = {"name": draw(SAFE_NAMES), "entry": draw(TEXT), **own}
        return defaults, own, [], (_expected({**values, **own}, multi=False),)
    tables: list[Table] = []
    for name in draw(st.lists(SAFE_NAMES, min_size=1, max_size=3, unique=True)):
        table: Table = {"name": name, "entry": draw(TEXT)}
        table.update({key: draw(SHARED[key]) for key in TARGET_OVERRIDES if draw(st.booleans())})
        tables.append(table)
    return defaults, own, tables, tuple(_expected({**values, **table}, multi=True) for table in tables)


def _text(values: Mapping[str, TomlValue], key: str, default: str | None = None) -> str:
    value = values.get(key, default)
    assert isinstance(value, str)
    return value


def _texts(values: Mapping[str, TomlValue], key: str) -> tuple[str, ...]:
    value = values.get(key, [])
    assert isinstance(value, list)
    return tuple(_text({key: item}, key) for item in value)


def _expected(values: Mapping[str, TomlValue], *, multi: bool) -> WedgeConfig:
    return WedgeConfig(
        name=_text(values, "name"),
        entry=_text(values, "entry"),
        source=_text(values, "source"),
        repo=_text(values, "repo"),
        source_paths=_texts(values, "source_paths"),
        project=_text(values, "project", "."),
        include=_texts(values, "include"),
        groups=_texts(values, "groups"),
        multi=multi,
    )


@st.composite
def _perturbed(draw: st.DrawFn) -> str:
    """A valid config with one value replaced: at the top level, in every [[target]] table, or as one more target."""
    _defaults, own, tables, _expected = draw(_valid_case())
    key, value = draw(EDITS)
    where = draw(st.sampled_from(["top", "tables", "targets"]))
    if where == "top":
        return _render({**own, key: value}, tables)
    if where == "tables":
        return _render(own, [{**table, key: value} for table in tables])
    return _render({**own, "target": [*tables, value]})


@st.composite
def _documents(draw: st.DrawFn) -> str:
    """Half the time a valid config with one edge value, else any text, any wedge-like table, or a cut-short config."""
    if draw(st.booleans()):
        return draw(_perturbed())
    valid = _valid_case().map(lambda case: _render(case[1], case[2]))
    truncated = st.tuples(valid, st.integers(0, 200)).map(lambda pair: pair[0][: pair[1]])
    return draw(st.one_of(st.text(), TABLES.map(_render), valid, truncated))


@PROPERTY
@given(_documents())
def test_parse_table_returns_the_toml_table_or_raises_config_error(text: str) -> None:
    path = Path("skill") / "wedge.toml"
    try:
        expected = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        with pytest.raises(ConfigError, match=re.escape(str(path))) as raised:
            _ = _parse_table(path, text)
        assert isinstance(raised.value.__cause__, tomllib.TOMLDecodeError)
        return

    assert _parse_table(path, text) == expected


@PROPERTY
@given(TABLES)
def test_a_rendered_table_parses_back_to_itself(table: Table) -> None:
    assert _parse_table(Path("wedge.toml"), _render(table)) == table


@PROPERTY
@given(_documents())
def test_parse_targets_returns_valid_configs_or_raises_config_error(tmp_path: Path, text: str) -> None:
    try:
        configs = parse_targets(tmp_path / "skill", text)
    except ConfigError:
        return

    assert configs
    assert len({config.name for config in configs}) == len(configs)
    for config in configs:
        assert config.multi == ("target" in tomllib.loads(text))
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", config.name)
        assert re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config.repo)
        assert all(field.strip() for field in (config.entry, config.source, config.project))
        assert all(item.strip() for item in (*config.source_paths, *config.include, *config.groups))
        assert "dev" not in config.groups
        assert not any(Path(item).is_absolute() or ".." in Path(item).parts for item in config.source_paths)


@PROPERTY
@given(_valid_case())
def test_a_valid_config_round_trips_to_its_wedge_configs(
    case: tuple[Table, Table, list[Table], tuple[WedgeConfig, ...]],
) -> None:
    defaults, own, tables, expected = case
    with tempfile.TemporaryDirectory() as root:
        skill = Path(root) / "skill"
        if defaults:
            _ = (Path(root) / "wedge.toml").write_text(_render(defaults), encoding="utf-8")

        assert parse_targets(skill, _render(own, tables)) == expected


@pytest.mark.parametrize(
    ("key", "value"),
    [(key, value) for key, values in EDGES.items() for value in values],
    ids=[f"{key}={value!r}" for key, values in EDGES.items() for value in values],
)
@settings(PROPERTY, max_examples=10)
@given(case=_valid_case())
def test_an_edge_value_where_it_takes_effect_is_a_config_error(
    tmp_path: Path, key: str, value: TomlValue, case: tuple[Table, Table, list[Table], tuple[WedgeConfig, ...]]
) -> None:
    """The edge value goes where nothing shadows it: the top level, every [[target]] table, or the target list."""
    defaults, own, tables, expected = case
    own = {**defaults, **own}
    skill = tmp_path / "skill"
    assert parse_targets(skill, _render(own, tables)) == expected
    if key == "target":
        edited = _render({**{name: item for name, item in own.items() if name not in ("name", "entry")}, key: value})
    elif tables and key not in ("project", "repo"):
        edited = _render(own, [{**table, key: value} for table in tables])
    else:
        edited = _render({**own, key: value}, tables)

    with pytest.raises(ConfigError):
        _ = parse_targets(skill, edited)

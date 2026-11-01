"""Property-based checks of the log reader and the harness adapters, with log files that Hypothesis generates."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agent_session_analytics._adapters import (
    claude_normalize,
    codex_normalize,
    cursor_normalize,
    omp_normalize,
    pi_normalize,
)
from agent_session_analytics._adapters._jsonl import iter_jsonl

# Each example rewrites the same files under the function-scoped tmp_path, so the examples stay independent.
# One example can write several 100 kB lines, so it can take longer than the default deadline.
PROPERTY = settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
T = TypeVar("T")

SCALARS = st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(max_size=8)
JSON: st.SearchStrategy[object] = st.recursive(
    SCALARS, lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=4), inner, max_size=3), max_leaves=6
)
TEXT = st.text(max_size=8)
# A flat value of each JSON type: cheaper to draw than JSON, and enough to put a wrong type in a field.
ANY_TYPE = SCALARS | st.lists(SCALARS, max_size=2) | st.dictionaries(TEXT, SCALARS, max_size=2)

# Text that no JSON parser accepts: an unclosed array nested past the parser recursion limit,
# and an unclosed array whose first number has more digits than int() converts by default.
DEEP = "[" * 100_000
HUGE_INT = "[" + "9" * 5_000
ENDINGS = st.sampled_from([b"\n", b"\r\n", b"\r"])
BLANK = st.sampled_from([b"", b" ", b"\t", b" \t "])


@st.composite
def _truncated(draw: st.DrawFn) -> bytes:
    """A JSON object line cut short, as a crash in the middle of a write leaves it."""
    text = json.dumps(draw(st.dictionaries(st.text(max_size=4), JSON, max_size=3))).encode()
    return text[: draw(st.integers(1, len(text) - 1))]


# Lines that never parse as JSON: truncated objects, invalid UTF-8, text that starts with a non-JSON character,
# and the two lines that make the parser raise an error other than JSONDecodeError.
UNPARSEABLE = st.one_of(
    _truncated(),
    st.binary(max_size=20).map(lambda raw: b"\xff" + raw.replace(b"\n", b"").replace(b"\r", b"")),
    st.text(st.characters(codec="utf-8", exclude_characters="\r\n"), max_size=12).map(lambda text: ("#" + text).encode()),
    st.sampled_from([DEEP.encode(), HUGE_INT.encode()]),
)
NOISE = UNPARSEABLE | BLANK


def _join(draw: st.DrawFn, lines: list[bytes]) -> bytes:
    """Join lines with mixed line endings; the last line has no ending in some examples."""
    endings = draw(st.lists(ENDINGS, min_size=len(lines), max_size=len(lines)))
    data = b"".join(line + ending for line, ending in zip(lines, endings, strict=True))
    return data.removesuffix(endings[-1]) if lines and draw(st.booleans()) else data


@st.composite
def _logs(draw: st.DrawFn, records: st.SearchStrategy[object], noise: st.SearchStrategy[bytes]) -> tuple[list[object], bytes, bytes]:
    """Records as JSON lines: once alone, and once with noise lines before, between, and after them."""
    items = draw(st.lists(records, min_size=2, max_size=6))
    pad = st.sampled_from([b"", b" ", b"\t "])
    lines = [draw(pad) + json.dumps(item).encode() + draw(pad) for item in items]
    noisy: list[bytes] = []
    for line in lines:
        noisy.extend(draw(st.lists(noise, max_size=2)))
        noisy.append(line)
    noisy.extend(draw(st.lists(noise, max_size=2)))
    return items, _join(draw, lines), _join(draw, noisy)


@st.composite
def _soup(draw: st.DrawFn) -> bytes:
    """Arbitrary bytes, or a mix of raw bytes, valid lines, noise lines, and line endings."""
    parts = st.one_of(st.binary(max_size=30), JSON.map(lambda value: json.dumps(value).encode()), NOISE, ENDINGS)
    return draw(st.binary() | st.lists(parts, max_size=12).map(b"".join))


def _mostly(common: st.SearchStrategy[T], rare: st.SearchStrategy[T], one_in: int) -> st.SearchStrategy[T]:
    """Draw from rare in about one example of one_in, and from common in the rest.

    `st.one_of` cannot set this split: it flattens nested alternatives and picks among all of them.
    """
    return st.integers(1, one_in).flatmap(lambda pick: rare if pick == 1 else common)


def _field(valid: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """A field value: mostly the type that the adapter reads, and a value of any JSON type in one example of eight.

    Records nest fields four levels deep, so a larger share of wrong types would seldom reach the innermost reads.
    """
    return _mostly(valid, ANY_TYPE, 8)


def _obj(
    required: Mapping[str, st.SearchStrategy[object]], optional: Mapping[str, st.SearchStrategy[object]] | None = None
) -> st.SearchStrategy[dict[str, object]]:
    return st.fixed_dictionaries(dict(required), optional=dict(optional or {}))


# Text in a field that an adapter parses as JSON: plain text, a JSON object, or an error payload, and in one example
# of three a line that makes the parser raise.
JSON_OBJECT_TEXT = st.dictionaries(st.sampled_from(["cmd", "command", "path"]), ANY_TYPE, max_size=2).map(json.dumps)
PARSED_TEXT = _mostly(
    st.one_of(TEXT, JSON_OBJECT_TEXT, st.sampled_from(['{"status": "error"}', '{"exit_code": 2}', "Process exited with code 1"])),
    st.sampled_from([DEEP, HUGE_INT]),
    3,
)
# A non-empty id or cwd. An adapter keeps the previous value when a new one is empty.
ID = st.text(min_size=1, max_size=8)
ARGUMENTS = TEXT | JSON_OBJECT_TEXT | _obj({}, {"cmd": _field(TEXT), "command": _field(st.lists(TEXT, max_size=3))})

PI_TEXT_BLOCK = _obj({"type": st.just("text"), "text": _field(TEXT)})
PI_BLOCK = PI_TEXT_BLOCK | _obj(
    {"type": st.just("toolCall"), "arguments": _field(ARGUMENTS)}, {"id": _field(TEXT), "name": _field(TEXT)}
)
PI_MESSAGE = st.one_of(
    _obj(
        {"role": st.just("assistant"), "content": _field(st.lists(PI_BLOCK, max_size=3))},
        {
            "stopReason": _field(st.sampled_from(["stop", "toolUse", "length", "error"])),
            "usage": _field(st.dictionaries(st.sampled_from(["input", "output", "cacheRead"]), st.integers(0, 9), max_size=3)),
            "model": _field(TEXT),
            "provider": _field(TEXT),
            "contextSnapshot": _field(_obj({}, {"promptTokens": _field(st.integers(0, 9))})),
        },
    ),
    _obj(
        {"role": st.just("toolResult"), "content": _field(st.lists(PI_TEXT_BLOCK, max_size=3))},
        {"toolCallId": _field(TEXT), "isError": _field(st.booleans())},
    ),
    _obj({"role": st.just("user")}, {"content": ANY_TYPE, "attribution": ANY_TYPE}),
)
# Half the records have a fixed structure, so that the rarer paths run often: a tool call whose arguments are
# PARSED_TEXT, and a session entry that sets the id and cwd that later entries carry.
PI_RECORDS: st.SearchStrategy[object] = _mostly(
    st.one_of(
        _obj({"type": st.just("message"), "message": _field(PI_MESSAGE)}, {"timestamp": _field(TEXT)}),
        _obj({"type": st.just("session")}, {"id": _field(TEXT), "cwd": _field(TEXT)}),
    ),
    st.one_of(
        PARSED_TEXT.map(
            lambda text: {"type": "message", "message": {"role": "assistant", "content": [{"type": "toolCall", "arguments": text}]}}
        ),
        ID.map(lambda value: {"type": "session", "id": value, "cwd": value}),
    ),
    2,
)
# A session entry with no id or cwd keeps the values of the previous session entry.
PI_SKIPPED = st.one_of(
    st.sampled_from([{"type": "session"}, {"type": "session", "id": "", "cwd": None}, {"type": "compaction"}]),
    _obj({"type": st.just("message"), "message": st.one_of(SCALARS, _obj({"role": st.sampled_from(["system", "tool"])}))}),
)

HEADER = "Script completed\nWall time: 0.1 seconds\nOutput:\n"
USAGE = _field(st.dictionaries(st.sampled_from(["input_tokens", "output_tokens"]), st.integers(0, 3), max_size=2))
CODEX_EVENT = st.one_of(
    _obj({"type": st.just("token_count"), "info": _field(_obj({}, {"total_token_usage": USAGE, "last_token_usage": USAGE}))}),
    _obj(
        {
            "type": st.just("item_completed"),
            "item": _field(_obj({"type": st.just("UserMessage"), "content": _field(st.lists(PI_TEXT_BLOCK, max_size=2))})),
        }
    ),
    _obj({"type": st.just("task_complete")}, {"last_agent_message": _field(TEXT)}),
    _obj({"type": st.just("turn_aborted")}, {"reason": _field(TEXT)}),
)
CODEX_ITEM = st.one_of(
    _obj(
        {"type": st.sampled_from(["function_call", "custom_tool_call"]), "arguments": _field(ARGUMENTS)},
        {
            "name": _field(st.sampled_from(["exec_command", "shell", "exec", "apply_patch"])),
            "input": _field(ARGUMENTS),
            "call_id": _field(TEXT),
        },
    ),
    _obj(
        {
            "type": st.sampled_from(["function_call_output", "custom_tool_call_output"]),
            "output": _field(TEXT | st.lists(PI_TEXT_BLOCK, max_size=2) | st.dictionaries(TEXT, ANY_TYPE, max_size=2)),
        },
        {"call_id": _field(TEXT)},
    ),
)
CODEX_SOURCE = TEXT | _obj({}, {"subagent": _field(_obj({}, {"thread_spawn": _field(_obj({}, {"parent_thread_id": _field(TEXT)}))}))})
CODEX_PAYLOADS = {
    "response_item": CODEX_ITEM,
    "event_msg": CODEX_EVENT,
    "session_meta": _obj({}, {"id": _field(TEXT), "cwd": _field(TEXT), "cli_version": _field(TEXT), "source": _field(CODEX_SOURCE)}),
    "turn_context": _obj({}, {"cwd": _field(TEXT), "model": _field(TEXT)}),
}


def _codex_call(name: str, arguments: str) -> dict[str, object]:
    return {"type": "response_item", "payload": {"type": "function_call", "name": name, "arguments": arguments}}


def _codex_output(output: object) -> dict[str, object]:
    return {"type": "response_item", "payload": {"type": "function_call_output", "output": output}}


# Half the records have a fixed structure, so that the rarer paths run often: a tool call or tool output that holds
# PARSED_TEXT, a turn context that sets the cwd, and a token count that emits a token_usage entry.
CODEX_RECORDS: st.SearchStrategy[object] = _mostly(
    st.one_of(
        *(_obj({"type": st.just(kind), "payload": _field(payload)}, {"timestamp": _field(TEXT)}) for kind, payload in CODEX_PAYLOADS.items())
    ),
    st.one_of(
        st.builds(_codex_call, st.sampled_from(["exec_command", "shell", "exec"]), PARSED_TEXT),
        # A string output, bare or after the two script headers, and a block list after the script header.
        st.builds("{}{}".format, st.sampled_from(["", HEADER, "Script completed\nOutput: "]), PARSED_TEXT).map(_codex_output),
        PARSED_TEXT.map(lambda text: _codex_output([{"type": "input_text", "text": HEADER}, {"type": "input_text", "text": text}])),
        ID.map(lambda cwd: {"type": "turn_context", "payload": {"cwd": cwd}}),
        st.integers(0, 9).map(
            lambda tokens: {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": tokens}}}}
        ),
    ),
    2,
)
CODEX_SKIPPED: st.SearchStrategy[object] = st.sampled_from(
    [
        {"type": "session_meta", "payload": 5},
        {"type": "turn_context", "payload": []},
        {"type": "event_msg", "payload": {"type": "agent_reasoning"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user"}},
        {"type": "compacted", "payload": {}},
    ]
)

# Cursor <timestamp> tags: any date, the two ends of the datetime range, and UTC offsets of any size.
CURSOR_STAMP = st.builds(
    "<timestamp>Monday, {}, {}:{:02d} {} (UTC{}{})</timestamp>".format,
    st.sampled_from(["Jan 1, 0001", "Dec 31, 9999"])
    | st.builds("{} {}, {:04d}".format, st.sampled_from(["Jan", "Dec", "Foo"]), st.integers(1, 31), st.integers(1, 9999)),
    st.sampled_from([1, 11, 12]) | st.integers(1, 12),
    st.integers(0, 59),
    st.sampled_from(["AM", "PM"]),
    st.sampled_from(["+", "-"]),
    st.integers(0, 14) | st.sampled_from([0, 14, 10**12]),
)
CURSOR_BLOCK = _obj({"type": st.just("text"), "text": _field(TEXT)}) | _obj(
    {"type": st.just("tool_use")},
    {
        "name": _field(st.sampled_from(["CallMcpTool", "Read"])),
        "input": _field(_obj({}, {"server": _field(TEXT), "toolName": _field(TEXT)})),
    },
)
CURSOR_RECORDS = st.one_of(
    _obj(
        {"role": st.sampled_from(["user", "assistant"]), "message": _field(_obj({"content": _field(st.lists(CURSOR_BLOCK, max_size=3))}))}
    ),
    _obj(
        {"type": st.just("turn_ended")},
        {"status": _field(st.sampled_from(["completed", "error"])), "error": _field(st.sampled_from(["Aborted by user", "boom"]))},
    ),
    CURSOR_STAMP.map(lambda stamp: {"role": "user", "message": {"content": [{"type": "text", "text": stamp}]}}),
)

# Claude logs already use the canonical envelope, so any object is a record, and a stale harness tag is overwritten.
CLAUDE_RECORDS = st.dictionaries(st.sampled_from(["type", "message", "harness", "sessionId"]) | TEXT, JSON, max_size=4)
NOT_AN_OBJECT = SCALARS | st.lists(ANY_TYPE, max_size=3)


@dataclass(frozen=True)
class Adapter:
    harness: str
    normalize: Callable[[str], Iterable[Mapping[str, object]]]
    records: st.SearchStrategy[object]
    # Parsed lines that the adapter drops with no change to its state. Cursor numbers its tool-call ids by parsed
    # line, so every parsed line counts there, and only unparseable lines are noise.
    skipped: st.SearchStrategy[object] | None


# Half the skipped lines are adapter-specific objects; `st.one_of` would give them one share among the six
# alternatives of NOT_AN_OBJECT.
ADAPTERS = {
    "claude": Adapter("claude", claude_normalize, CLAUDE_RECORDS, NOT_AN_OBJECT),
    "codex": Adapter("codex", codex_normalize, CODEX_RECORDS, _mostly(NOT_AN_OBJECT, CODEX_SKIPPED, 2)),
    "cursor": Adapter("cursor", cursor_normalize, CURSOR_RECORDS, None),
    "omp": Adapter("omp", omp_normalize, PI_RECORDS, _mostly(NOT_AN_OBJECT, PI_SKIPPED, 2)),
    "pi": Adapter("pi", pi_normalize, PI_RECORDS, _mostly(NOT_AN_OBJECT, PI_SKIPPED, 2)),
}


def _log_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A transcript path under a Cursor projects root, so every adapter can read it."""
    monkeypatch.setenv("CURSOR_HOME", str(tmp_path))
    path = tmp_path / "projects" / "demo" / "agent-transcripts" / "session.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path, data: bytes) -> list[object]:
    _ = path.write_bytes(data)
    return list(iter_jsonl(str(path)))


def _normalize(adapter: Adapter, path: Path, data: bytes) -> list[dict[str, object]]:
    _ = path.write_bytes(data)
    return [dict(entry) for entry in adapter.normalize(str(path))]


@PROPERTY
@given(data=_soup())
def test_the_reader_never_raises_and_yields_at_most_one_value_per_line(tmp_path: Path, data: bytes) -> None:
    values = _read(tmp_path / "log.jsonl", data)

    assert len(values) <= sum(1 for line in data.splitlines() if line.strip())


@PROPERTY
@given(log=_logs(JSON, NOISE))
def test_the_reader_yields_the_valid_lines_in_order_whatever_noise_surrounds_them(
    tmp_path: Path, log: tuple[list[object], bytes, bytes]
) -> None:
    values, clean, noisy = log
    path = tmp_path / "log.jsonl"

    assert _read(path, clean) == values
    assert _read(path, noisy) == values


@pytest.mark.parametrize("name", sorted(ADAPTERS))
@PROPERTY
@given(data=st.data())
def test_an_adapter_never_raises_on_a_corrupt_log_and_tags_every_entry_with_its_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, data: st.DataObject
) -> None:
    adapter = ADAPTERS[name]
    _, _, log = data.draw(_logs(adapter.records, NOISE | NOT_AN_OBJECT.map(lambda value: json.dumps(value).encode())))

    entries = _normalize(adapter, _log_path(tmp_path, monkeypatch), log)

    assert [entry["harness"] for entry in entries] == [adapter.harness] * len(entries)
    # The stage step writes each entry with json.dumps, so an entry must serialize.
    _ = json.dumps(entries)


@pytest.mark.parametrize("name", sorted(ADAPTERS))
@PROPERTY
@given(data=st.data())
def test_noise_lines_between_records_do_not_change_the_entries_of_an_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, data: st.DataObject
) -> None:
    adapter = ADAPTERS[name]
    skipped = NOISE if adapter.skipped is None else adapter.skipped.map(lambda value: json.dumps(value).encode())
    noise = _mostly(NOISE, skipped, 2)
    _, clean, noisy = data.draw(_logs(adapter.records, noise))
    path = _log_path(tmp_path, monkeypatch)

    assert _normalize(adapter, path, noisy) == _normalize(adapter, path, clean)

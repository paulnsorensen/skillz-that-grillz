"""Regression properties: Codex event lines with raw Unicode line separators."""
from __future__ import annotations

import json

from hypothesis import example, given
from hypothesis import strategies as st

from skillz_experiments.harness import _codex

# Codex is a Rust program. serde_json writes U+0085, U+2028, and U+2029 raw in strings, but escapes CR and LF.
SEPARATORS = st.sampled_from(["\\x85", "\\u2028", "\\u2029", "\\x0b", "\\x0c", "\\x1c", "\\x1d", "\\x1e"])
TEXT = st.lists(st.text(max_size=4) | SEPARATORS, max_size=4).map("".join)
EVENTS = st.lists(st.dictionaries(TEXT, TEXT | st.none(), max_size=3), min_size=1, max_size=4)


@given(EVENTS, st.sampled_from(["\n", "\r\n"]))
@example([{"\x85": None}], "\n")
def test_codex_events_read_one_object_per_line_with_raw_unicode_separators(
        events: list[dict[str, str | None]], newline: str) -> None:
    stdout = "".join(json.dumps(event, ensure_ascii=False) + newline for event in events)
    assert _codex._events(stdout) == events  # pyright: ignore[reportPrivateUsage]

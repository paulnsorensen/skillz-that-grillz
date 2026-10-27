"""Regression properties: Codex event lines with raw Unicode line separators, and role fingerprints with too-long paths."""
from __future__ import annotations

import json
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

from skillz_experiments.harness import _codex
from skillz_experiments.harness._roles import Role

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


# NAME_MAX is 255 bytes on Linux and macOS, so each name here is too long for one path component.
LONG_NAMES = st.integers(256, 1024).map(lambda size: "a" * size)


@given(LONG_NAMES, st.sampled_from(["", "--prompt=", "NAME="]))
def test_a_role_fingerprint_ignores_a_path_whose_name_is_too_long(name: str, prefix: str) -> None:
    role = Role("command", "model", ("/bin/sh", f"{prefix}/{name}"), "command-1")
    assert role.fingerprint() == role.fingerprint()


def test_a_role_fingerprint_still_hashes_an_existing_command_file(tmp_path: Path) -> None:
    script = tmp_path / "script.sh"
    _ = script.write_text("echo one\n")
    role = Role("command", "model", ("/bin/sh", str(script), "/" + "a" * 300), "command-1")
    before = role.fingerprint()
    _ = script.write_text("echo two\n")
    assert role.fingerprint() != before

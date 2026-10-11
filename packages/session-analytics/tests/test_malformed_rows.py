"""A malformed log field is dropped instead of stopping the ingest. No DuckDB needed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast


def normalize(adapter: str, path: Path, env: dict[str, str]) -> list[object]:
    """Run one ingest adapter in a child process and return its canonical rows."""
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import json, sys; from session_analytics import _ingest; "
            + "print(json.dumps(list(getattr(_ingest, sys.argv[1])(sys.argv[2]))))",
            adapter,
            str(path),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return cast(list[object], json.loads(result.stdout))


def test_pi_rows_keep_their_valid_parts(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    entries = [
        {"type": "session", "id": "s1", "cwd": "/work"},
        {"type": "message", "message": {"role": "assistant", "content": 5, "stopReason": ["x"]}},
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolCallId": "c1",
                "content": [{"type": "text", "text": 7}, {"type": "text", "text": "ok"}],
            },
        },
    ]
    _ = path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    rows = normalize("pi_normalize", path, dict(os.environ))
    envelope = {"harness": "pi", "timestamp": None, "sessionId": "s1", "cwd": "/work"}
    usage = {"input_tokens": None, "output_tokens": None, "cache_read_input_tokens": None}
    assert rows == [
        envelope | {"type": "assistant", "message": {"content": [], "usage": usage}},
        envelope
        | {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "c1", "content": "ok", "is_error": "false"}
                ]
            },
        },
    ]


def test_cursor_user_text_that_is_not_a_string_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "cursor"
    transcripts = root / "projects" / "sample" / "agent-transcripts"
    transcripts.mkdir(parents=True)
    path = transcripts / "t1.jsonl"
    stamp = "<timestamp>Monday, Jan 5, 2026, 10:00 AM (UTC+0)</timestamp> go"
    entries = [
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": 9}, {"type": "text", "text": stamp}]},
        },
        {"type": "turn_ended", "status": "completed"},
    ]
    _ = path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    rows = normalize("cursor_normalize", path, os.environ | {"CURSOR_HOME": str(root)})
    assert len(rows) == 1
    row = cast(dict[str, object], rows[0])
    assert row["timestamp"] == "2026-01-05T10:00:00Z"


def test_codex_error_flag_survives_a_later_unusable_block(tmp_path: Path) -> None:
    header = "Script completed\nWall time: 0.1 seconds\nOutput:\n"
    trailers: dict[str, dict[str, object]] = {
        "non-text block": {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
        "null text": {"type": "input_text", "text": None},
    }
    for name, trailer in trailers.items():
        output = [
            {"type": "input_text", "text": header},
            {"type": "input_text", "text": json.dumps({"status": "error"})},
            trailer,
        ]
        entry = {
            "type": "response_item",
            "payload": {"type": "function_call_output", "call_id": "c1", "output": output},
        }
        path = tmp_path / f"rollout-{name.replace(' ', '-')}.jsonl"
        _ = path.write_text(json.dumps(entry) + "\n")
        rows = normalize("codex_normalize", path, dict(os.environ))
        assert len(rows) == 1, name
        row = cast(dict[str, dict[str, list[dict[str, object]]]], rows[0])
        assert row["message"]["content"][0]["is_error"] == "true", name

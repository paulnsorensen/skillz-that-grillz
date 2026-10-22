"""Pi-family token usage: cache reads and writes count toward model_turns.context_tokens."""

from __future__ import annotations

import pytest

from .conftest import Sandbox, needs_duckdb


def _session(harness_dir: str, session_id: str, usage: dict[str, int]) -> tuple[str, list[dict[str, object]]]:
    return (
        f"{harness_dir}/agent/sessions/-w/{session_id}.jsonl",
        [
            {"type": "session", "id": session_id, "cwd": "/w"},
            {
                "type": "message",
                "timestamp": "2026-03-01T00:01:00Z",
                "message": {"role": "assistant", "model": "m", "content": [], "usage": usage},
            },
        ],
    )


@needs_duckdb
@pytest.mark.ac("gh-394:AC-1")
@pytest.mark.ac("gh-394:AC-2")
@pytest.mark.parametrize(("harness", "harness_dir"), [("omp", ".omp"), ("pi", ".pi")])
def test_pi_family_context_tokens_add_cache_read_and_write(
    sandbox: Sandbox, harness: str, harness_dir: str
) -> None:
    path, entries = _session(harness_dir, "s1", {"input": 100, "output": 7, "cacheRead": 4000, "cacheWrite": 250})
    _ = sandbox.write_log(path, entries)
    result = sandbox.run("ingest", "--force")
    assert result.returncode == 0, result.stderr
    rows = sandbox.query(
        "SELECT input_tokens, cache_read_tokens, cache_creation_tokens, context_tokens "
        + f"FROM model_turns WHERE harness = '{harness}'"
    )
    assert rows == [
        {
            "input_tokens": 100,
            "cache_read_tokens": 4000,
            "cache_creation_tokens": 250,
            "context_tokens": 4350,
        }
    ]

"""Harness log adapters and their registry.

Each adapter is (name, discover, normalize):
  discover()      -> list of source paths, or [] when no logs are reachable.
  normalize(path) -> yields canonical raw-entry dicts (already harness-tagged).
An adapter that finds nothing is non-fatal — the run logs it and continues.
"""

from __future__ import annotations

from agent_session_analytics._adapters.claude import claude_discover, claude_normalize
from agent_session_analytics._adapters.codex import codex_discover, codex_normalize
from agent_session_analytics._adapters.copilot import copilot_discover
from agent_session_analytics._adapters.cursor import cursor_discover, cursor_normalize
from agent_session_analytics._adapters.pi_family import (
    omp_discover,
    omp_normalize,
    pi_discover,
    pi_normalize,
)

ADAPTERS = [
    ("claude", claude_discover, claude_normalize),
    ("codex", codex_discover, codex_normalize),
    ("omp", omp_discover, omp_normalize),
    ("pi", pi_discover, pi_normalize),
    ("cursor", cursor_discover, cursor_normalize),
    ("copilot", copilot_discover, None),
]

__all__ = [
    "ADAPTERS",
    "claude_discover",
    "claude_normalize",
    "codex_discover",
    "codex_normalize",
    "copilot_discover",
    "cursor_discover",
    "cursor_normalize",
    "omp_discover",
    "omp_normalize",
    "pi_discover",
    "pi_normalize",
]

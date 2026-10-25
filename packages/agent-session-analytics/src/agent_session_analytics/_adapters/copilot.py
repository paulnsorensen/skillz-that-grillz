"""The Copilot adapter: discovery only, because the CLI keeps no local transcript."""

from __future__ import annotations


def copilot_discover() -> list[str]:
    # GitHub Copilot CLI persists no local session transcript we can find
    # (~/.copilot holds skills/ + mcp-config.json only). See harness-coverage.md.
    return []

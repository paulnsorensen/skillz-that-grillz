from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments.evaluation import Transcript
from skillz_experiments.harness._claude._events import assistant_text
from skillz_experiments.harness._codex import Codex
from test_codex_journey import fake_codex
from test_journey import contract, journey_case


def _claude_turn(*texts: str) -> list[dict[str, object]]:
    return [{"type": "assistant", "message": {"content": [{"type": "text", "text": text} for text in texts]}}]


@pytest.mark.ac("gh-698:AC-1")
@pytest.mark.usefixtures("host_login")
def test_codex_turn_transcript_keeps_every_agent_message_in_order(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    fake_codex(tmp_path, monkeypatch, "two_messages")
    case, candidate = journey_case(tmp_path, contract(), 1)
    transport = Codex("offline-test", Budget(1, 120, 0), lambda: None)
    try:
        result = transport.converse("first request", candidate, case, False,
                                    lambda _transcript: pytest.fail("simulator must not run"))
    finally:
        transport.close()
    transcript = cast(Transcript, result["transcript"])
    assert transcript[1] == {"role": "assistant", "text": "contract text\nsummary"}
    assert transcript[1]["text"] == assistant_text(_claude_turn("contract text", "summary"))

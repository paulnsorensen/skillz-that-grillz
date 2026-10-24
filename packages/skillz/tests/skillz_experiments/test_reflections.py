"""The reflection audit log: one private, redacted, bounded record per reflection call (gh-501)."""
from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments.run._reflections import RECORD_LIMIT, TRUNCATED, ReflectionLog


@pytest.mark.ac("gh-501:AC-6")
@pytest.mark.parametrize("filler", ["x", "é", "\U0001f600"], ids=["ascii", "latin", "astral"])
def test_a_long_record_is_cut_to_64_kib_with_markers_and_stays_private(tmp_path: Path, filler: str) -> None:
    ReflectionLog(tmp_path).record(3, "api_key=sk-live-LOGSECRET " + (filler + " ") * 100_000, {"SKILL.md": (filler + " ") * 45_000})
    path = tmp_path / "reflections" / "3.json"
    data = path.read_bytes()
    assert len(data) <= RECORD_LIMIT and stat.S_IMODE(path.stat().st_mode) == 0o600
    record = cast(dict[str, object], json.loads(data))
    assert record["ordinal"] == 3
    assert cast(str, record["prompt"]).endswith(TRUNCATED) and cast(str, record["answer"]).endswith(TRUNCATED)
    assert "LOGSECRET" not in data.decode()


@pytest.mark.ac("gh-501:AC-6")
def test_a_credential_whose_closing_quote_lies_past_64_kib_is_redacted_before_the_record_is_cut(tmp_path: Path) -> None:
    prompt = "Improve. password=\"ZZCANARYP " + "word " * 20_000 + "\" end"
    answer = {"SKILL.md": "token: 'ZZCANARYA " + "w " * 40_000 + "' done", "@hypothesis": None}
    assert len(prompt) > RECORD_LIMIT and len(json.dumps(answer)) > RECORD_LIMIT
    ReflectionLog(tmp_path).record(7, prompt, answer)
    path = tmp_path / "reflections" / "7.json"
    data = path.read_bytes()
    assert len(data) <= RECORD_LIMIT and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "ZZCANARY" not in data.decode()
    record = cast(dict[str, object], json.loads(data))
    assert record["ordinal"] == 7 and cast(str, record["prompt"]).startswith("Improve. [redacted] end")
    assert record["answer"] == '{"@hypothesis": null, "SKILL.md": [redacted] done"}'
    assert sorted(item.name for item in (tmp_path / "reflections").iterdir()) == ["7.json"]

@pytest.mark.ac("gh-501:AC-6")
def test_a_short_record_holds_the_redacted_prompt_and_answer_whole(tmp_path: Path) -> None:
    ReflectionLog(tmp_path).record(1, "Improve SKILL.md. token=abc123", {"SKILL.md": "new text", "@hypothesis": None})
    record = cast(dict[str, object], json.loads((tmp_path / "reflections" / "1.json").read_text()))
    assert record == {"ordinal": 1, "prompt": "Improve SKILL.md. [redacted]",
                      "answer": json.dumps({"@hypothesis": None, "SKILL.md": "new text"}, sort_keys=True)}


@pytest.mark.ac("gh-501:AC-6")
def test_a_record_never_replaces_an_existing_one(tmp_path: Path) -> None:
    log = ReflectionLog(tmp_path)
    log.record(1, "first", None)
    before = (tmp_path / "reflections" / "1.json").read_bytes()
    with pytest.raises(CodedError) as caught:
        log.record(1, "second", None)
    assert caught.value.code == "reflection-record-exists"
    assert (tmp_path / "reflections" / "1.json").read_bytes() == before


def test_a_symlinked_log_directory_fails_with_a_coded_error(tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "reflections").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(CodedError) as caught:
        ReflectionLog(tmp_path).record(1, "prompt", None)
    assert caught.value.code == "reflection-record-failed" and not list((tmp_path / "elsewhere").iterdir())

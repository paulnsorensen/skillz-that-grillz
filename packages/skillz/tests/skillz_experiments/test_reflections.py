"""The reflection audit log: one private, redacted, bounded record per reflection call (gh-501)."""
from __future__ import annotations

import itertools
import json
import stat
import time
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments.search import Progress
from skillz_experiments.search import request as reflection_request
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
    assert record["answer"] == '{"@hypothesis": null, "SKILL.md": "[redacted] done"}'
    assert sorted(item.name for item in (tmp_path / "reflections").iterdir()) == ["7.json"]


ESCAPED = 'password=\\"first zzcanary last\\" kept'


@pytest.mark.ac("gh-501:AC-6")
@pytest.mark.parametrize("answer", [{"SKILL.md": ESCAPED}, {ESCAPED: 1}, ["ok", (ESCAPED,)]], ids=["value", "key", "nested"])
def test_an_escaped_multiword_credential_in_the_answer_leaves_no_canary(tmp_path: Path, answer: object) -> None:
    ReflectionLog(tmp_path).record(2, "prompt", answer)
    path = tmp_path / "reflections" / "2.json"
    assert "zzcanary" not in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    json.loads(path.read_bytes())


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


def _answer_of(tmp_path: Path, answer: object, ordinal: int = 9) -> tuple[str, dict[str, object]]:
    ReflectionLog(tmp_path).record(ordinal, "prompt", answer)
    text = (tmp_path / "reflections" / f"{ordinal}.json").read_text()
    return text, cast(dict[str, object], json.loads(cast(str, json.loads(text)["answer"])))


@pytest.mark.ac("gh-501:AC-6")
@pytest.mark.parametrize("answer", [
    {"password": "zzcanary"}, {"password": 424242}, {"password": 4242.5}, {"password": b"zzcanary"},
    {"password": {"inner": ["zzcanary", 424242]}}, {"password": ["zzcanary"]}, {"password.md": "zzcanary"},
    {"api_key": object()}, {b"password": "zzcanary"}],
    ids=["str", "int", "float", "bytes", "nested-dict", "list", "component-key", "object", "bytes-key"])
def test_the_whole_value_under_a_credential_key_is_redacted(tmp_path: Path, answer: object) -> None:
    text, parsed = _answer_of(tmp_path, answer)
    assert "zzcanary" not in text and "424242" not in text and "4242.5" not in text
    assert list(parsed.values()) == ["[redacted]"]


@pytest.mark.ac("gh-501:AC-6")
def test_bytes_decode_and_unsupported_leaves_fail_closed(tmp_path: Path) -> None:
    _, parsed = _answer_of(tmp_path, {"a": b"token=zzcanary kept", "b": bytearray(b"x"), "c": object(), "d": 7, "e": 1.5,
                                      "f": [None, True], b"g": "h"})
    assert parsed == {"a": "[redacted] kept", "b": "x", "c": "[redacted]", "d": 7, "e": 1.5, "f": [None, True],
                      "g": "h"}


@pytest.mark.ac("gh-501:AC-6")
def test_keys_that_collide_after_redaction_keep_every_entry(tmp_path: Path) -> None:
    _, parsed = _answer_of(tmp_path, {"https://a.example": 1, "https://b.example": 2, "https://c.example": 3, "x": 4})
    assert parsed == {"[redacted]": 1, "[redacted]#2": 2, "[redacted]#3": 3, "x": 4}


@pytest.mark.ac("gh-501:AC-6")
def test_an_escaped_double_quote_credential_in_the_prompt_leaves_no_canary(tmp_path: Path) -> None:
    prompt = r'Improve SKILL.md. password=\"one\\\" two zzcanary\" kept'
    ReflectionLog(tmp_path).record(4, prompt, None)
    text = (tmp_path / "reflections" / "4.json").read_text()
    assert "zzcanary" not in text and "two" not in text


@pytest.mark.ac("gh-501:AC-6")
@pytest.mark.parametrize("layers", [0, 1, 2])
@pytest.mark.parametrize("key", ["password", "token", "api_key", "secret", "authorization"])
def test_an_escaped_credential_in_a_component_leaves_no_canary_in_the_logged_request(tmp_path: Path, key: str, layers: int) -> None:
    text = json.dumps({key: "zzcanary", "user": "kept"})
    for _ in range(layers):
        text = json.dumps(text)[1:-1]
    prompt, _ = reflection_request("prose", {"SKILL.md": text}, {}, ["SKILL.md"], progress=Progress(spent=0, planned=100, proposal=1))
    assert "zzcanary" in prompt
    ReflectionLog(tmp_path).record(5, prompt, None)
    assert "zzcanary" not in (tmp_path / "reflections" / "5.json").read_text()


@pytest.mark.ac("gh-501:AC-6")
def test_the_cost_of_colliding_keys_grows_linearly_with_their_number(tmp_path: Path) -> None:
    """Each sample runs for at least 100 ms of CPU time, and the two sizes alternate, so noise hits both alike."""
    values = {count: {f"https://{index}.example": index for index in range(count)} for count in (2000, 4000)}
    calls = itertools.count()

    def per_run_seconds(value: dict[str, int]) -> float:
        runs, begin = 0, time.process_time()
        while runs == 0 or time.process_time() - begin < 0.1:
            run_dir = tmp_path / str(next(calls))
            run_dir.mkdir()
            ReflectionLog(run_dir).record(1, "p", value)
            runs += 1
        return (time.process_time() - begin) / runs

    best = dict.fromkeys(values, float("inf"))
    for _ in range(3):
        for count, value in values.items():
            best[count] = min(best[count], per_run_seconds(value))
    assert best[4000] <= 3 * best[2000], f"doubling the keys multiplied the cost by {best[4000] / best[2000]:.1f}"

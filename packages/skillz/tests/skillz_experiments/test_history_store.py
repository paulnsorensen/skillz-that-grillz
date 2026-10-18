"""The private cross-run history store: location, modes, compatibility, retention, and safe failure."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments.run._history_store import KEEP_RUNS, UNREADABLE, directory, load, store_path, write

ROOT = Path(__file__).resolve().parents[4]
FIELDS: dict[str, object] = {"contract_hash": "c", "skill": "echo-skill", "statistics": {"min_gain": 0.0}}


def _record(**fields: object) -> dict[str, object]:
    merged = FIELDS | fields
    return {"compatibility": {"fingerprint": json.dumps(merged, sort_keys=True), "fields": merged}}


def _attempt(index: int, **fields: object) -> dict[str, object]:
    return {"index": index, "parent": "p", "candidate": f"c{index}", "components": "d", "hypothesis": f"idea {index}",
            "fixed": ["t0-0"], "broke": [], "validation_delta": 0.1, "accepted": True, "rejected": None,
            "rejected_coded": False} | fields


def _path() -> Path:
    return store_path("echo-skill", "/skills/echo-skill")


@pytest.mark.ac("gh-243:AC-1")
@pytest.mark.usefixtures("umask_022")
def test_the_store_lives_under_xdg_state_with_private_modes_and_one_line_per_run(history_state: Path) -> None:
    path = _path()
    assert path.parent == history_state / "skillz" / "history" == directory()
    assert re.fullmatch(r"[0-9a-f]{64}\.jsonl", path.name) and path != store_path("echo-skill", "/skills/other")
    assert write(path, _record(), "run-a", [_attempt(1, extra="dropped")]) is None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
    (line,) = path.read_text(encoding="utf-8").splitlines()
    stored = cast(dict[str, object], json.loads(line))
    assert set(stored) == {"run_id", "timestamp", "compatibility", "attempts"} and stored["run_id"] == "run-a"
    assert stored["compatibility"] == _record()["compatibility"]
    assert stored["attempts"] == [_attempt(1)]
    loaded = load(path, _record(), "run-b")
    assert loaded.attempts == [{"run_id": "run-a"} | _attempt(1)] and loaded.runs == ["run-a"]
    assert load(path, _record(), "run-a").attempts == [], "a run never loads its own line"


def test_a_relative_xdg_state_home_falls_back_to_the_home_state_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert directory() == tmp_path / ".local/state/skillz/history"


@pytest.mark.ac("gh-243:AC-2")
def test_a_run_with_a_differing_field_is_excluded_and_named() -> None:
    path = _path()
    _ = write(path, _record(), "run-a", [_attempt(1)])
    _ = write(path, _record(statistics={"min_gain": 0.1}), "run-b", [_attempt(1)])
    _ = write(path, {"compatibility": {}}, "run-c", [_attempt(1)])
    loaded = load(path, _record(), "run-d")
    assert loaded.runs == ["run-a"] and [item["run_id"] for item in loaded.attempts] == ["run-a"]
    assert loaded.excluded == [{"run_id": "run-b", "fields": ["statistics"]},
                               {"run_id": "run-c", "fields": ["missing-fingerprint"]}]


@pytest.mark.ac("gh-243:AC-5")
def test_a_corrupt_line_is_skipped_and_counted() -> None:
    path = _path()
    _ = write(path, _record(), "run-a", [_attempt(1)])
    with path.open("a", encoding="utf-8") as handle:
        _ = handle.write('not json\n{"run_id": 5}\n[1]\n{"run_id": "x", "compatibility": {}, "attempts": [3]}\n\n')
    loaded = load(path, _record(), "run-b")
    assert loaded.skipped_lines == 4 and loaded.runs == ["run-a"] and loaded.warnings == []


@pytest.mark.ac("gh-243:AC-5")
@pytest.mark.parametrize("broken", ["file-mode", "directory-mode", "symlink"])
def test_an_unsafe_or_unreadable_store_gives_history_unreadable_and_no_attempt(broken: str, tmp_path: Path) -> None:
    path = _path()
    _ = write(path, _record(), "run-a", [_attempt(1, hypothesis="private-idea")])
    if broken == "file-mode":
        path.chmod(0o644)
    elif broken == "directory-mode":
        path.parent.chmod(0o755)
    else:
        elsewhere = tmp_path / "elsewhere.jsonl"
        _ = elsewhere.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        elsewhere.chmod(0o600)
        path.unlink()
        path.symlink_to(elsewhere)
    loaded = load(path, _record(), "run-b")
    assert loaded.attempts == [] and loaded.warnings == [UNREADABLE]
    assert loaded.summary() == {"enabled": True, "runs": [], "excluded": [], "skipped_lines": 0, "warnings": [UNREADABLE]}
    assert write(path, _record(), "run-b", [_attempt(1)]) == UNREADABLE


def test_a_missing_store_loads_nothing_and_warns_nothing() -> None:
    loaded = load(_path(), _record(), "run-a")
    assert loaded.summary() == {"enabled": True, "runs": [], "excluded": [], "skipped_lines": 0, "warnings": []}
    assert not directory().exists()


def test_the_store_keeps_the_newest_runs_and_replaces_a_rewritten_run() -> None:
    path = _path()
    for number in range(KEEP_RUNS + 3):
        assert write(path, _record(), f"run-{number}", [_attempt(1)]) is None
    ids = [cast(dict[str, object], json.loads(line))["run_id"] for line in path.read_text(encoding="utf-8").splitlines()]
    assert ids == [f"run-{number}" for number in range(3, KEEP_RUNS + 3)]
    assert write(path, _record(), "run-5", [_attempt(1), _attempt(2)]) is None
    lines = [cast(dict[str, object], json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == KEEP_RUNS and [item["run_id"] for item in lines].count("run-5") == 1
    assert lines[-1]["run_id"] == "run-5" and len(cast(list[object], lines[-1]["attempts"])) == 2
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@pytest.mark.ac("gh-243:AC-7")
def test_an_adr_records_the_default_on_store_its_location_its_privacy_rule_and_no_history() -> None:
    text = (ROOT / "docs/agents/decisions/skillz-autoimprove.md").read_text(encoding="utf-8")
    match = re.search(r"^### ADR-020: Keep a private cross-run attempt history.*?(?=^### |^_Source|\Z)", text, re.M | re.S)
    assert match is not None
    adr = match[0]
    assert "[status: accepted]" in adr and "on by default" in adr
    assert "`$XDG_STATE_HOME/skillz/history/<key>.jsonl`" in adr and "`~/.local/state`" in adr
    assert "mode 0700" in adr and "mode 0600" in adr and f"newest {KEEP_RUNS} runs" in adr
    assert "**Privacy rule:** a line holds no request, expected, label, or holdout data." in adr
    assert "`run --no-history`" in adr and "`run-config-differs`" in adr and f"`{UNREADABLE}`" in adr
    assert "never feed the stall counter" in adr


@pytest.mark.ac("gh-243:AC-5")
def test_a_deeply_nested_line_is_skipped_and_counted_without_crashing() -> None:
    path = _path()
    _ = write(path, _record(), "run-a", [_attempt(1)])
    with path.open("ab") as handle:
        _ = handle.write(b"[" * 200000 + b"\n")
    loaded = load(path, _record(), "run-b")
    assert loaded.skipped_lines == 1 and loaded.runs == ["run-a"] and loaded.warnings == []
    assert write(path, _record(), "run-b", [_attempt(1)]) is None
    repaired = load(path, _record(), "run-c")
    assert repaired.runs == ["run-a", "run-b"] and repaired.skipped_lines == 0


@pytest.mark.ac("gh-243:AC-5")
def test_a_bad_utf8_line_is_skipped_and_the_other_lines_load_and_a_write_repairs_the_store() -> None:
    path = _path()
    _ = write(path, _record(), "run-a", [_attempt(1)])
    with path.open("ab") as handle:
        _ = handle.write(b"\xff\xfe\n")
    loaded = load(path, _record(), "run-b")
    assert loaded.skipped_lines == 1 and loaded.runs == ["run-a"] and loaded.warnings == []
    assert write(path, _record(), "run-b", [_attempt(1)]) is None
    repaired = load(path, _record(), "run-c")
    assert repaired.runs == ["run-a", "run-b"] and repaired.skipped_lines == 0


@pytest.mark.ac("gh-243:AC-5")
def test_a_partial_trailing_line_does_not_swallow_the_line_of_the_next_write() -> None:
    path = _path()
    _ = write(path, _record(), "run-a", [_attempt(1)])
    with path.open("ab") as handle:
        _ = handle.write(b'{"run_id": "cut')
    assert write(path, _record(), "run-b", [_attempt(1)]) is None
    loaded = load(path, _record(), "run-c")
    assert loaded.runs == ["run-a", "run-b"] and loaded.skipped_lines == 0


@pytest.mark.ac("gh-243:AC-5")
def test_corrupt_lines_use_no_retention_slot() -> None:
    path = _path()
    _ = write(path, _record(), "run-0", [_attempt(1)])
    with path.open("ab") as handle:
        _ = handle.write(b"not json\n\xff\n[1]\n")
    for number in range(1, KEEP_RUNS):
        assert write(path, _record(), f"run-{number}", [_attempt(1)]) is None
    loaded = load(path, _record(), "run-next")
    assert loaded.runs == [f"run-{number}" for number in range(KEEP_RUNS)] and loaded.skipped_lines == 0


@pytest.mark.ac("gh-243:AC-5")
def test_a_failure_while_replacing_the_store_keeps_the_old_store_and_leaves_no_temporary_file(
        monkeypatch: pytest.MonkeyPatch) -> None:
    path = _path()
    _ = write(path, _record(), "run-a", [_attempt(1)])
    before = path.read_bytes()

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    assert write(path, _record(), "run-b", [_attempt(1)]) == UNREADABLE
    assert path.read_bytes() == before
    assert sorted(entry.name for entry in path.parent.iterdir()) == sorted([path.name, f"{path.name}.lock"])


@pytest.mark.ac("gh-243:AC-4")
def test_uncoded_rejection_text_is_stored_as_rejected_and_a_code_is_kept() -> None:
    path = _path()
    attempts = [_attempt(1, accepted=False, rejected="failed at /home/someone/secret/path", rejected_coded=False),
                _attempt(2, accepted=False, rejected="candidate-leakage-rejected", rejected_coded=True)]
    assert write(path, _record(), "run-a", attempts) is None
    text = path.read_text(encoding="utf-8")
    assert "/home/someone" not in text
    (line,) = text.splitlines()
    stored = cast(list[dict[str, object]], cast(dict[str, object], json.loads(line))["attempts"])
    assert [item["rejected"] for item in stored] == ["rejected", "candidate-leakage-rejected"]


@pytest.mark.ac("gh-243:AC-5")
def test_the_lock_file_is_private() -> None:
    path = _path()
    _ = write(path, _record(), "run-a", [_attempt(1)])
    lock = path.parent / f"{path.name}.lock"
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    lock.chmod(0o644)
    assert write(path, _record(), "run-b", [_attempt(1)]) == UNREADABLE
    assert load(path, _record(), "run-c").warnings == [UNREADABLE]

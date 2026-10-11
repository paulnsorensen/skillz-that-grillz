"""Attack the `evals/evals.json` seeder: kind precedence, unsafe fixtures, size bounds, and the coverage check."""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments import _cases, _seed
from skillz_experiments._cases import CodedError
from skillz_experiments._intake import load_draft
from skillz_experiments._seed import READ_LIMIT, SEED_LIMIT
from skillz_experiments._workflow import Stop, run

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]


def _evals(target: Path, entries: list[object]) -> None:
    _ = (target / "evals/evals.json").write_text(json.dumps({"evals": entries}), encoding="utf-8")


def _entry(name: str, **extra: object) -> dict[str, object]:
    return {"name": name, "prompt": f"prompt-{name}", "expected_output": "out", **extra}


def _seed_cases(target: Path, tmp_path: Path) -> list[dict[str, object]]:
    with pytest.raises(Stop) as caught:
        _ = run(target, tmp_path / "run", "local-test", live=True)
    assert caught.value.code == "cases-missing"
    return cast(list[dict[str, object]], caught.value.data["seed_cases"])


def _unreadable(target: Path, tmp_path: Path) -> CodedError:
    with pytest.raises(CodedError) as caught:
        _ = run(target, tmp_path / "run", "local-test", live=True)
    assert caught.value.code == "evals-unreadable"
    return caught.value


@pytest.mark.parametrize(("extra", "kind"), [
    ({}, "trigger"),
    ({"assertions": []}, "trigger"),
    ({"assertions": ["a"]}, "task"),
    ({"expected_skill": "echo-skill"}, "trigger"),
    ({"expected_skill": "echo-skill", "assertions": ["a"]}, "task"),
    ({"expected_skill": None}, "near-miss"),
    ({"expected_skill": None, "assertions": []}, "near-miss"),
    ({"expected_skill": None, "assertions": ["a"]}, "near-miss"),
    ({"expected_skill": None, "files": ["nope.md"]}, "near-miss"),
])
def test_seed_kind_precedence_matrix(tmp_path: Path, make_target: Maker, extra: dict[str, object], kind: str) -> None:
    target = make_target(tmp_path)
    if kind == "task":
        (target / "evals/files").mkdir()
    _evals(target, [_entry("e", **extra)])
    seed = _seed_cases(target, tmp_path)
    assert [case["kind"] for case in seed] == [kind]
    assert ("files" in seed[0]) == (kind == "task") and ("expected" in seed[0]) == (kind == "task")


def test_seed_is_deterministic_json_and_preserves_order_for_many_entries(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    names = [f"case-{(index * 7919) % 3000:04d}" for index in range(3000)]
    _evals(target, [_entry(name) for name in names])
    first = _seed_cases(target, tmp_path)
    second = _seed_cases(target, tmp_path)
    assert first == second and [case["id"] for case in first] == names
    assert json.loads(json.dumps(first)) == first


def test_empty_evals_list_seeds_an_empty_list(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _evals(target, [])
    assert _seed_cases(target, tmp_path) == []


def test_seed_with_family_added_round_trips_through_the_draft_validator(
        tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    (target / "evals/files").mkdir()
    _ = (target / "evals/files/a.md").write_text("alpha\n", encoding="utf-8")
    _evals(target, [_entry("t", assertions=["a"], files=["evals/files/a.md"]), _entry("g"),
                    _entry("m", expected_skill=None)])
    seed = _seed_cases(target, tmp_path)
    drafted = [case | {"family": "fam"} for case in seed]
    out = tmp_path / "run"
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = (out / "cases.draft.json").write_text(json.dumps(drafted), encoding="utf-8")
    loaded = load_draft(out / "cases.draft.json")
    assert [case.identifier for case in loaded] == ["t", "g", "m"]


def test_task_seed_without_files_needs_the_agent_drafted_fixture(tmp_path: Path, make_target: Maker) -> None:
    """A task entry with no files seeds files {}. The agent drafts the fixture: the draft validator rejects the bare seed and accepts it with a fixture."""
    target = make_target(tmp_path)
    _evals(target, [_entry("bare", assertions=["a"])])
    seed = _seed_cases(target, tmp_path)
    assert seed[0]["files"] == {}
    out = tmp_path / "run"
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    draft = out / "cases.draft.json"
    _ = draft.write_text(json.dumps([seed[0] | {"family": "fam"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="a task case needs files"):
        _ = load_draft(draft)
    _ = draft.write_text(json.dumps([seed[0] | {"family": "fam", "files": {"input.txt": "start\n"}}]), encoding="utf-8")
    assert load_draft(draft)[0].files == {"input.txt": "start\n"}


@pytest.mark.parametrize(("path", "fragment"), [
    ("./evals/files/a.md", "unsafe relative path"), ("evals//files/a.md", "unsafe relative path"),
    ("evals/files/a.md/", "unsafe relative path"), ("evals\\files\\a.md", "unsafe relative path"),
    ("evals/.hidden/a.md", "must not be hidden"), ("evals/files/../files/a.md", "unsafe relative path"),
    ("tmp/a.md", "must be under evals/files/"), ("AGENTS.md", "must be under evals/files/"),
    ("evals/a.md", "must be under evals/files/"), ("evals/files", "must be under evals/files/"),
    ("evals/files/a\x00.md", "unsafe relative path"), ("", "unsafe relative path"),
])
def test_unusual_fixture_paths_stop_with_evals_unreadable(
        tmp_path: Path, make_target: Maker, path: str, fragment: str) -> None:
    target = make_target(tmp_path)
    (target / "evals/files").mkdir()
    _ = (target / "evals/files/a.md").write_text("x\n", encoding="utf-8")
    (target / "evals/.hidden").mkdir()
    _ = (target / "evals/.hidden/a.md").write_text("x\n", encoding="utf-8")
    _ = (target / "evals/a.md").write_text("x\n", encoding="utf-8")
    _evals(target, [_entry("bad", assertions=["a"], files=[path])])
    message = str(_unreadable(target, tmp_path))
    assert "entry bad" in message and fragment in message


def test_symlinked_parent_in_the_middle_of_a_fixture_path_is_refused(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    (target / "evals/files/real/deep").mkdir(parents=True)
    _ = (target / "evals/files/real/deep/a.md").write_text("x\n", encoding="utf-8")
    os.symlink(target / "evals/files/real", target / "evals/files/alias")
    _evals(target, [_entry("bad", assertions=["a"], files=["evals/files/alias/deep/a.md"])])
    assert "cannot be read" in str(_unreadable(target, tmp_path))


def test_fifo_fixture_is_refused_without_blocking(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    (target / "evals/files").mkdir()
    os.mkfifo(target / "evals/files/pipe.md")
    _evals(target, [_entry("bad", assertions=["a"], files=["evals/files/pipe.md"])])
    assert "must be a regular file" in str(_unreadable(target, tmp_path))


@pytest.mark.parametrize("content", ["", "   \n"])
def test_blank_fixture_stops_with_evals_unreadable(tmp_path: Path, make_target: Maker, content: str) -> None:
    target = make_target(tmp_path)
    (target / "evals/files").mkdir()
    _ = (target / "evals/files/blank.md").write_text(content, encoding="utf-8")
    _evals(target, [_entry("bad", assertions=["a"], files=["evals/files/blank.md"])])
    assert "must be nonempty text" in str(_unreadable(target, tmp_path))


def test_oversized_fixture_is_refused(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    (target / "evals/files").mkdir()
    _ = (target / "evals/files/big.md").write_text("x" * (READ_LIMIT + 1), encoding="utf-8")
    _evals(target, [_entry("bad", assertions=["a"], files=["evals/files/big.md"])])
    assert "is too large" in str(_unreadable(target, tmp_path))


def test_oversized_evals_file_is_refused(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _ = (target / "evals/evals.json").write_text(json.dumps({"evals": [_entry("a", pad="x" * READ_LIMIT)]}))
    assert "is too large" in str(_unreadable(target, tmp_path))


def test_evals_path_that_is_a_directory_or_dangling_link_is_refused(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    (target / "evals/evals.json").mkdir()
    _ = _unreadable(target, tmp_path)
    (target / "evals/evals.json").rmdir()
    os.symlink(tmp_path / "nowhere.json", target / "evals/evals.json")
    _ = _unreadable(target, tmp_path)


def test_non_utf8_and_bom_evals_file_is_refused(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _ = (target / "evals/evals.json").write_bytes(b'{"evals": ["\xff"]}')
    _ = _unreadable(target, tmp_path)
    _ = (target / "evals/evals.json").write_bytes(b'\xef\xbb\xbf{"evals": []}')
    _ = _unreadable(target, tmp_path)


@pytest.mark.parametrize(("bad", "fragment"), [
    (None, "entry #1: expected a JSON object"), (3, "entry #1: expected a JSON object"),
    (True, "entry #1: expected a JSON object"), ([], "entry #1: expected a JSON object"),
    ("str", "entry #1: expected a JSON object"),
    ({"name": True, "prompt": "p"}, "entry #1: name must be nonempty text"),
    ({"name": "  ", "prompt": "p"}, "entry #1: name must be nonempty text"),
    ({"name": "a", "prompt": "  \n"}, "entry a: prompt must be nonempty text"),
    ({"name": "a", "prompt": "p", "expected_output": 5}, "expected_output must be text"),
    ({"name": "a", "prompt": "p", "assertions": [None]}, "assertions must be a list of text"),
    ({"name": "a", "prompt": "p", "assertions": [""]}, "assertions must hold nonempty text"),
    ({"name": "a", "prompt": "p", "files": "x"}, "files must be a list of text"),
    ({"name": "a", "prompt": "p", "expected_skill": ""}, "expected_skill must be nonempty text or null"),
    ({"name": "a", "prompt": "p", "family": " "}, "family must be nonempty text"),
])
def test_bad_entries_stop_with_evals_unreadable(
        tmp_path: Path, make_target: Maker, bad: object, fragment: str) -> None:
    target = make_target(tmp_path)
    _evals(target, [bad])
    assert fragment in str(_unreadable(target, tmp_path))


def _shared_fixture(target: Path, size: int, entries: int) -> None:
    (target / "evals/files").mkdir()
    _ = (target / "evals/files/shared.md").write_text("x" * size, encoding="utf-8")
    _evals(target, [_entry(f"e{index}", assertions=["a"], files=["evals/files/shared.md"]) for index in range(entries)])


def test_entries_that_share_a_fixture_read_it_once(
        tmp_path: Path, make_target: Maker, monkeypatch: pytest.MonkeyPatch) -> None:
    target = make_target(tmp_path)
    _shared_fixture(target, 1000, 50)
    reads: list[str] = []

    def spy(root: Path, name: str, limit: int) -> str:
        reads.append(name)
        return _cases.read_text(root, name, limit)

    monkeypatch.setattr(_seed, "read_text", spy)
    assert len(_seed_cases(target, tmp_path)) == 50
    assert reads.count("evals/files/shared.md") == 1


def test_the_seed_total_is_bounded_however_many_entries_share_a_fixture(
        tmp_path: Path, make_target: Maker, monkeypatch: pytest.MonkeyPatch) -> None:
    target = make_target(tmp_path)
    _shared_fixture(target, SEED_LIMIT // 10, 200)
    reads: list[str] = []

    def spy(root: Path, name: str, limit: int) -> str:
        reads.append(name)
        return _cases.read_text(root, name, limit)

    monkeypatch.setattr(_seed, "read_text", spy)
    assert "seed is too large" in str(_unreadable(target, tmp_path))
    assert reads.count("evals/files/shared.md") == 1


def test_the_largest_accepted_seed_still_loads_as_a_draft(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    entries = 4
    _shared_fixture(target, SEED_LIMIT // entries - 1000, entries)
    seed = _seed_cases(target, tmp_path)
    assert len(seed) == entries
    out = tmp_path / "run"
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = (out / "cases.draft.json").write_text(json.dumps([case | {"family": "f"} for case in seed]), encoding="utf-8")
    assert len(load_draft(out / "cases.draft.json")) == entries
    (out / "cases.draft.json").unlink()
    _ = (target / "evals/files/shared.md").write_text("x" * (SEED_LIMIT // entries + 1000), encoding="utf-8")
    assert "seed is too large" in str(_unreadable(target, tmp_path))


def test_seed_keeps_request_and_name_verbatim(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    request = "  café ‮ line\n\ttail  "
    _evals(target, [{"name": "né 1", "prompt": request, "expected_output": ""}])
    seed = _seed_cases(target, tmp_path)
    assert seed[0]["request"] == request and seed[0]["id"] == "né 1"


def _draft_and_run(tmp_path: Path, cases: list[dict[str, object]]) -> CodedError:
    out = tmp_path / "run"
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = (out / "cases.draft.json").write_text(json.dumps(cases), encoding="utf-8")
    with pytest.raises(CodedError) as caught:
        _ = run(tmp_path / "echo-skill", out, "local-test", live=True)
    return caught.value


def _extra(name: str, request: str | None = None, kind: str = "task") -> dict[str, object]:
    case: dict[str, object] = {"id": name, "family": "f", "kind": kind, "request": request or f"prompt-{name}",
                               "source": "skill"}
    if kind == "task":
        case |= {"files": {"fixture.md": "h\n"}, "expected": {}}
    return case


@pytest.mark.parametrize("changed", [
    "prompt-e ", " prompt-e", "Prompt-e", "prompt-e\n", "prompt‑e",
])
def test_coverage_requires_the_exact_request(tmp_path: Path, make_target: Maker, changed: str) -> None:
    target = make_target(tmp_path)
    _evals(target, [_entry("e")])
    error = _draft_and_run(tmp_path, [_extra("e", changed, kind="trigger")])
    assert error.code == "cases-uncovered" and "- e: request changed" in str(error)
    assert changed.strip() not in str(error)


def test_coverage_rejects_nfd_versus_nfc_request(tmp_path: Path, make_target: Maker) -> None:
    _ = make_target(tmp_path)
    _evals(tmp_path / "echo-skill", [{"name": "u", "prompt": "café", "expected_output": ""}])
    error = _draft_and_run(tmp_path, [_extra("u", "café", kind="trigger")])
    assert error.code == "cases-uncovered"


def test_coverage_names_every_problem_and_runs_before_the_family_budget_check(
        tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _evals(target, [_entry("a"), _entry("b"), _entry("c", assertions=["x"])])
    error = _draft_and_run(tmp_path, [_extra("a", kind="trigger"), _extra("c", kind="trigger")])
    assert error.code == "cases-uncovered"
    assert "- b: missing" in str(error) and "- c:" in str(error) and "- a:" not in str(error)


def test_coverage_compares_the_draft_kind_not_the_split_kind(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target = make_target(tmp_path)
    _evals(target, [{**_entry("t0-0", assertions=["a"]), "prompt": "request-t0-0-end"},
                    {**_entry("t1-0", assertions=["a"]), "prompt": "request-t1-0-end"}])
    out = tmp_path / "run"
    _ = write_draft(out)
    with pytest.raises(Stop) as caught:
        _ = run(target, out, "local-test", live=True)
    assert caught.value.code == "cases-unapproved"


def test_duplicate_draft_ids_stay_a_draft_error_before_coverage(
        tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _evals(target, [_entry("e")])
    out = tmp_path / "run"
    out.mkdir(mode=0o700, parents=True)
    cases = [_extra("e", kind="trigger"), _extra("e", kind="trigger")]
    _ = (out / "cases.draft.json").write_text(json.dumps(cases), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate case id e"):
        _ = run(target, out, "local-test", live=True)


def test_coverage_stops_with_evals_unreadable_when_evals_breaks_after_the_seed(
        tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _evals(target, [_entry("e")])
    _ = (target / "evals/evals.json").write_text("{broken")
    error = _draft_and_run(tmp_path, [_extra("e", kind="trigger")])
    assert error.code == "evals-unreadable"


def test_a_draft_without_evals_json_is_never_checked_for_coverage(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target = make_target(tmp_path)
    assert not (target / "evals/evals.json").exists()
    out = tmp_path / "run"
    _ = write_draft(out)
    with pytest.raises(Stop) as caught:
        _ = run(target, out, "local-test", live=True)
    assert caught.value.code == "cases-unapproved"


def test_one_entry_with_many_distinct_fixtures_stops_reading_at_the_seed_bound(
        tmp_path: Path, make_target: Maker, monkeypatch: pytest.MonkeyPatch) -> None:
    target = make_target(tmp_path)
    (target / "evals/files").mkdir()
    paths = [f"evals/files/f{index}.md" for index in range(40)]
    for path in paths:
        _ = (target / path).write_text("x" * (SEED_LIMIT // 10), encoding="utf-8")
    _evals(target, [_entry("wide", assertions=["a"], files=paths)])
    reads: list[str] = []

    def spy(root: Path, name: str, limit: int) -> str:
        reads.append(name)
        return _cases.read_text(root, name, limit)

    monkeypatch.setattr(_seed, "read_text", spy)
    assert "seed is too large" in str(_unreadable(target, tmp_path))
    assert len([name for name in reads if name != "evals/evals.json"]) <= 11

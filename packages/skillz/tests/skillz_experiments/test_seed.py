from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._intake import DRAFT_NAME, load_draft
from skillz_experiments._seed import seed_cases
from skillz_experiments.run import Stop, run

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]


def _evals(target: Path, entries: list[dict[str, object]]) -> None:
    _ = (target / "evals/evals.json").write_text(json.dumps({"skill_name": "echo-skill", "evals": entries}))


def _entry(name: str, **extra: object) -> dict[str, object]:
    return {"id": 1, "name": name, "prompt": f"prompt-{name}", "expected_output": f"out-{name}", "files": [],
            **extra}


def _stop(target: Path, tmp_path: Path) -> Stop:
    with pytest.raises(Stop) as caught:
        _ = run(target, tmp_path / "run", "local-test", live=True)
    return caught.value


def _seed_cases(target: Path, tmp_path: Path) -> list[dict[str, object]]:
    stop = _stop(target, tmp_path)
    assert stop.code == "cases-missing"
    return cast(list[dict[str, object]], stop.data["seed_cases"])


def _unreadable(target: Path, tmp_path: Path) -> CodedError:
    with pytest.raises(CodedError) as caught:
        _ = run(target, tmp_path / "run", "local-test", live=True)
    assert caught.value.code == "evals-unreadable"
    return caught.value


def test_seed_maps_each_entry_in_file_order(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    (target / "evals/files").mkdir()
    _ = (target / "evals/files/a.md").write_text("alpha\n", encoding="utf-8")
    _evals(target, [
        _entry("zeta-task", assertions=["says alpha"], files=["evals/files/a.md"]),
        _entry("alpha-trigger", assertions=[]),
        _entry("mid-miss", expected_skill=None, assertions=["ignored"]),
        _entry("bare-trigger"),
    ])
    seed = _seed_cases(target, tmp_path)
    assert [case["id"] for case in seed] == ["zeta-task", "alpha-trigger", "mid-miss", "bare-trigger"]
    assert [case["kind"] for case in seed] == ["task", "trigger", "near-miss", "trigger"]
    assert all(case["source"] == "skill" for case in seed)
    assert seed[0]["request"] == "prompt-zeta-task"
    assert seed[0]["files"] == {"evals/files/a.md": "alpha\n"}
    assert seed[0]["expected"] == {"expected_output": "out-zeta-task", "assertions": ["says alpha"]}
    assert "files" not in seed[1] and "expected" not in seed[2]


def test_a_task_entry_without_files_seeds_an_empty_fixture(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _evals(target, [_entry("bare", assertions=["a"])])
    assert _seed_cases(target, tmp_path)[0]["files"] == {}


def test_a_task_entry_keeps_no_default_family_and_the_entry_family_wins(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _evals(target, [_entry("a", family="billing"), _entry("d", assertions=["x"])])
    seed = _seed_cases(target, tmp_path)
    assert seed[0]["family"] == "billing" and "family" not in seed[1]


@pytest.mark.ac("gh-292:AC-1")
def test_a_seeded_trigger_and_near_miss_draft_loads_with_family_equal_to_id(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _evals(target, [_entry("trig"), _entry("miss", expected_skill=None)])
    seed = _seed_cases(target, tmp_path)
    path = tmp_path / DRAFT_NAME
    _ = path.write_text(json.dumps(seed), encoding="utf-8")
    assert [(case.kind, case.identifier, case.family) for case in load_draft(path)] == [
        ("trigger", "trig", "trig"), ("near-miss", "miss", "miss")]


@pytest.mark.ac("gh-292:AC-1")
def test_the_repository_skill_seed_loads_as_a_draft(tmp_path: Path) -> None:
    seed = seed_cases(Path(__file__).resolve().parents[4] / "skills/skillz")
    assert seed
    path = tmp_path / DRAFT_NAME
    _ = path.write_text(json.dumps(seed), encoding="utf-8")
    assert [case.identifier for case in load_draft(path)] == [case["id"] for case in seed]


def test_no_evals_file_keeps_the_old_stop(tmp_path: Path, make_target: Maker) -> None:
    stop = _stop(make_target(tmp_path), tmp_path)
    assert stop.code == "cases-missing" and "seed_cases" not in stop.data and "facts" in stop.data
    assert not (tmp_path / "run/cases.draft.json").exists()


def test_the_runner_never_writes_the_draft(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _evals(target, [_entry("a")])
    _ = _seed_cases(target, tmp_path)
    assert not (tmp_path / "run/cases.draft.json").exists()


def _symlinked(target: Path) -> None:
    (target / "evals/files").mkdir()
    outside = target.parent / "outside.md"
    _ = outside.write_text("x\n", encoding="utf-8")
    os.symlink(outside, target / "evals/files/link.md")


def _symlinked_dir(target: Path) -> None:
    (target / "evals/files").mkdir()
    (target.parent / "elsewhere").mkdir()
    _ = (target.parent / "elsewhere/a.md").write_text("x\n", encoding="utf-8")
    os.symlink(target.parent / "elsewhere", target / "evals/files/dir")


def _binary(target: Path) -> None:
    (target / "evals/files").mkdir()
    _ = (target / "evals/files/bin.md").write_bytes(b"\xff\xfe\x00bad")


def _nothing(target: Path) -> None:
    del target


@pytest.mark.parametrize(("prepare", "path", "fragment"), [
    (_symlinked, "evals/files/link.md", "cannot be read"),
    (_symlinked_dir, "evals/files/dir/a.md", "cannot be read"),
    (_binary, "evals/files/bin.md", "must be UTF-8 text"),
    (_nothing, "../outside.md", "unsafe relative path"),
    (_nothing, "/etc/hostname", "unsafe relative path"),
    (_nothing, "evals", "must be under evals/files/"),
    (_nothing, "evals/files/missing.md", "cannot be read"),
])
def test_a_bad_fixture_path_stops_with_evals_unreadable(
        tmp_path: Path, make_target: Maker, prepare: Callable[[Path], None], path: str, fragment: str) -> None:
    target = make_target(tmp_path)
    prepare(target)
    _evals(target, [_entry("bad", assertions=["a"], files=[path])])
    message = str(_unreadable(target, tmp_path))
    assert "entry bad" in message and fragment in message and str(tmp_path) not in message


def _document(**extra: object) -> str:
    return json.dumps({"evals": [{"name": "a", "prompt": "p", "expected_output": "o", **extra}]})


@pytest.mark.parametrize(("document", "fragment"), [
    ("{not json", "Expecting"),
    ("[]", "expected a JSON object"),
    (json.dumps({"evals": {}}), "evals must be a list"),
    (json.dumps({"evals": ["x"]}), "entry #1: expected a JSON object"),
    (json.dumps({"evals": [{"name": "a", "prompt": "p"}, {"prompt": "p"}]}), "entry #2: name must be nonempty text"),
    (json.dumps({"evals": [{"name": "a", "prompt": "p", "expected_output": "o"},
                           {"name": "a", "prompt": "q", "expected_output": "o"}]}), "entry names must be unique"),
    (json.dumps({"evals": [{"name": 4, "prompt": "p", "expected_output": "o"}]}), "name must be nonempty text"),
    (json.dumps({"evals": [{"name": "a", "prompt": "", "expected_output": "o"}]}), "entry a: prompt must be nonempty text"),
    (_document(assertions="no"), "assertions must be a list of text"),
    (_document(assertions=[" "]), "assertions must hold nonempty text"),
    (_document(files=[3]), "files must be a list of text"),
    (_document(expected_skill=3), "expected_skill must be nonempty text or null"),
    (_document(expected_skill=""), "expected_skill must be nonempty text or null"),
    (_document(family=""), "family must be nonempty text"),
    (_document(family=3), "family must be nonempty text"),
])
def test_a_malformed_evals_file_stops_with_evals_unreadable(
        tmp_path: Path, make_target: Maker, document: str, fragment: str) -> None:
    target = make_target(tmp_path)
    _ = (target / "evals/evals.json").write_text(document)
    assert fragment in str(_unreadable(target, tmp_path))


def test_a_symlinked_evals_file_stops_with_evals_unreadable(tmp_path: Path, make_target: Maker) -> None:
    target = make_target(tmp_path)
    _ = (tmp_path / "real.json").write_text(json.dumps({"evals": []}))
    os.symlink(tmp_path / "real.json", target / "evals/evals.json")
    _ = _unreadable(target, tmp_path)


def _task(name: str, request: str | None = None, kind: str = "task") -> dict[str, object]:
    case: dict[str, object] = {"id": name, "family": name, "kind": kind,
                               "request": request or f"prompt-{name}", "source": "skill"}
    if kind == "task":
        case |= {"files": {"fixture.md": "hello\n"}, "expected": {}}
    return case


def _covered(tmp_path: Path, cases: list[dict[str, object]], write_draft: Drafter) -> CodedError:
    target, out = tmp_path / "echo-skill", tmp_path / "run"
    _ = write_draft(out)
    existing = cast(list[dict[str, object]], json.loads((out / "cases.draft.json").read_text()))
    _ = (out / "cases.draft.json").write_text(json.dumps(cases + existing))
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, "local-test", live=True)
    return caught.value


def test_a_draft_that_covers_every_entry_plus_extras_proceeds_to_approval(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target = make_target(tmp_path)
    _evals(target, [_entry("one", assertions=["a"]), _entry("two")])
    error = _covered(tmp_path, [_task("one"), _task("two", kind="trigger")], write_draft)
    assert isinstance(error, Stop) and error.code == "cases-unapproved"


def test_a_draft_that_drops_an_entry_stops_with_cases_uncovered(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target = make_target(tmp_path)
    _evals(target, [_entry("one", assertions=["a"]), _entry("gone", assertions=["a"])])
    error = _covered(tmp_path, [_task("one")], write_draft)
    assert error.code == "cases-uncovered" and "gone" in str(error) and "- one" not in str(error)
    assert "remove" in str(error) and "evals.json" in str(error)


def test_a_draft_that_changes_a_request_stops_with_cases_uncovered(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target = make_target(tmp_path)
    _evals(target, [_entry("one", assertions=["a"])])
    error = _covered(tmp_path, [_task("one", request="reworded")], write_draft)
    assert error.code == "cases-uncovered" and "one" in str(error) and "request" in str(error)


def test_a_draft_that_changes_a_kind_stops_with_cases_uncovered(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target = make_target(tmp_path)
    _evals(target, [_entry("one", assertions=["a"])])
    error = _covered(tmp_path, [_task("one", kind="trigger")], write_draft)
    assert error.code == "cases-uncovered" and "one" in str(error) and "kind" in str(error)


def test_coverage_does_not_read_fixtures(tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target = make_target(tmp_path)
    (target / "evals/files").mkdir()
    fixture = target / "evals/files/a.md"
    _ = fixture.write_text("alpha\n", encoding="utf-8")
    _evals(target, [_entry("one", assertions=["a"], files=["evals/files/a.md"])])
    assert _seed_cases(target, tmp_path)[0]["files"] == {"evals/files/a.md": "alpha\n"}
    fixture.unlink()
    error = _covered(tmp_path, [_task("one")], write_draft)
    assert error.code == "cases-unapproved"

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import make_workspace, stage_task
from skillz_experiments._cases import Case, CodedError, Fixture, image_type, load_cases, text_files
from skillz_experiments._contract import Grader
from skillz_experiments._graders import _grading_workspace, judge  # pyright: ignore[reportPrivateUsage]
from skillz_experiments._intake import IntakeCase, case_hash
from skillz_experiments._seed import SEED_LIMIT, seed_cases, seed_document

Maker = Callable[..., Path]
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
HEADS = {"png": b"\x89PNG\r\n\x1a\n", "jpg": b"\xff\xd8\xff\xe0", "gif": b"GIF89a", "webp": b"RIFF\x00\x00\x00\x00WEBPVP8 "}
TEXT_CASE_HASH = "408517dd03b455bbe3f669736e99849f2f246ca4dfe17764999d346506b27d8d"
TEXT_SEED_SHA = "fb8901da313e661cfa53dc89b33ad8594d74bc6b7f4de70dfc6882dcc3a9b764"


def _marker(data: bytes, kind: str = "png") -> dict[str, str]:
    return {"base64": base64.b64encode(data).decode("ascii"), "type": kind}


def _target(tmp_path: Path, make_target: Maker, files: dict[str, bytes], entries: list[list[str]]) -> Path:
    target = make_target(tmp_path)
    (target / "evals/files").mkdir()
    for name, data in files.items():
        _ = (target / name).write_bytes(data)
    evals = [{"name": f"e{index}", "prompt": "p", "expected_output": "o", "assertions": ["a"], "files": paths}
             for index, paths in enumerate(entries)]
    _ = (target / "evals/evals.json").write_text(json.dumps({"skill_name": "echo-skill", "evals": evals}))
    return target


def _case(files: dict[str, Fixture]) -> Case:
    return Case("c1", "fam", "train", "look", files, {"criteria": [{"id": "r", "text": "THE answer SHALL be right."}]},
                "skill", True)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.ac("gh-589:AC-1")
@pytest.mark.parametrize("kind", sorted(HEADS))
def test_an_image_fixture_enters_the_seed_as_a_base64_marker_with_its_type(
        tmp_path: Path, make_target: Maker, kind: str) -> None:
    data = HEADS[kind] + b"\x00\x01\x02" * 20
    target = _target(tmp_path, make_target, {"evals/files/pic.bin": data}, [["evals/files/pic.bin"]])
    seeded = cast(list[dict[str, object]], seed_cases(target))
    assert seeded[0]["files"] == {"evals/files/pic.bin": _marker(data, kind)}
    assert image_type(data) == kind


@pytest.mark.ac("gh-589:AC-1")
def test_an_image_fixture_that_cases_share_is_stored_and_counted_once(tmp_path: Path, make_target: Maker) -> None:
    data = PNG + b"\x07" * (SEED_LIMIT // 8)
    path = "evals/files/shared.png"
    target = _target(tmp_path, make_target, {path: data}, [[path]] * 12)
    seeded = cast(list[dict[str, object]], seed_cases(target))
    document = seed_document(seeded)
    assert document["fixtures"] == {path: _marker(data)}
    text = json.dumps(document, sort_keys=True, indent=2)
    assert text.count(_marker(data)["base64"]) == 1
    assert len(text) < len(_marker(data)["base64"]) + 20_000


@pytest.mark.ac("gh-589:AC-1")
def test_distinct_image_fixtures_count_their_encoded_size_against_the_seed_limit(
        tmp_path: Path, make_target: Maker) -> None:
    raw = PNG + b"\x01" * (SEED_LIMIT * 2 // 5)
    paths = [f"evals/files/{index}.png" for index in range(2)]
    target = _target(tmp_path, make_target, {path: raw + path.encode() for path in paths}, [[path] for path in paths])
    with pytest.raises(CodedError, match="seed is too large") as caught:
        _ = seed_cases(target)
    assert caught.value.code == "evals-unreadable"


@pytest.mark.ac("gh-589:AC-1")
def test_the_exact_image_bytes_reach_the_task_and_grading_workspaces(tmp_path: Path) -> None:
    case = _case({"pic.png": _marker(PNG), "note.txt": "hello"})
    task = make_workspace(tmp_path / "task")
    stage_task(task, None, case)
    assert (task / "pic.png").read_bytes() == PNG and _digest(task / "pic.png") == hashlib.sha256(PNG).hexdigest()
    assert (task / "note.txt").read_text() == "hello"
    with _grading_workspace(case, {"out.txt": "x"}, "judge") as workspace:
        assert _digest(workspace / "pic.png") == hashlib.sha256(PNG).hexdigest()


@pytest.mark.ac("gh-589:AC-1")
def test_the_judge_prompt_names_an_image_fixture_but_carries_no_bytes(tmp_path: Path) -> None:
    del tmp_path
    encoded = _marker(PNG)["base64"]
    case = _case({"pic.png": _marker(PNG), "note.txt": "hello"})
    prompts: list[str] = []

    class Stop(Exception):
        pass

    class Transport:
        def invoke(self, prompt: str, **_: object) -> dict[str, object]:
            prompts.append(prompt)
            raise Stop

    with pytest.raises(Stop):
        _ = judge(Transport(), Grader("judge", rubric="rubric"), case, "answer", None, holdout=False)
    assert "[png image, " in prompts[0] and "not shown]" in prompts[0] and "hello" in prompts[0]
    assert encoded not in prompts[0] and text_files(case.files)["pic.png"] in prompts[0]


@pytest.mark.ac("gh-589:AC-1")
def test_a_frozen_case_file_loads_an_image_marker_and_rejects_a_forged_one(tmp_path: Path) -> None:
    def manifest(marker: object) -> Path:
        path = tmp_path / "cases.json"
        item = {"id": "c1", "family": "f", "split": "train", "kind": "task", "request": "r", "files": {"p.png": marker},
                "expected": {"answer": "a"}, "provenance": "skill", "provider_approved": True}
        _ = path.write_text(json.dumps({"schema_version": 1, "cases": [item]}))
        return path

    kinds = {"task": "judge"}
    assert load_cases(manifest(_marker(PNG)), kinds)[0].files == {"p.png": _marker(PNG)}
    for forged in (_marker(b"not an image"), _marker(PNG, "gif"), {"base64": "!!", "type": "png"},
                   {**_marker(PNG), "extra": "x"}):
        with pytest.raises(ValueError, match="png, jpg, webp, or gif image"):
            _ = load_cases(manifest(forged), kinds)


@pytest.mark.ac("gh-589:AC-2")
@pytest.mark.parametrize("data", [b"\xff\xfe\x00bad", b"RIFF\xff\xff\xff\xffWAVEfmt ", b"\x89PNG\r\n", b"GIF90a\xff"])
def test_a_binary_fixture_outside_the_allowlist_is_refused_with_a_coded_error(
        tmp_path: Path, make_target: Maker, data: bytes) -> None:
    target = _target(tmp_path, make_target, {"evals/files/bin.dat": data}, [["evals/files/bin.dat"]])
    with pytest.raises(CodedError) as caught:
        _ = seed_cases(target)
    assert caught.value.code == "evals-unreadable"
    assert "entry e0" in str(caught.value) and "must be UTF-8 text or a png, jpg, webp, or gif image" in str(caught.value)


@pytest.mark.ac("gh-589:AC-2")
def test_a_text_only_seed_and_its_case_hash_stay_byte_identical() -> None:
    """The pinned values come from the code before the image allowlist; text fixtures must not move them."""
    case = IntakeCase("c1", "fam", "task", "do it", "skill", {"a.txt": "alpha", "b.md": "beta"}, {"answer": "x"}, None, False,
                      split="train")
    assert case_hash([case], 7) == TEXT_CASE_HASH
    cases: list[dict[str, object]] = [{"id": "c1", "files": {"a.txt": "alpha", "b.md": "beta"}},
                                      {"id": "c2", "files": {"a.txt": "alpha"}}]
    text = json.dumps(seed_document(cases), sort_keys=True, indent=2) + "\n"
    assert hashlib.sha256(text.encode()).hexdigest() == TEXT_SEED_SHA

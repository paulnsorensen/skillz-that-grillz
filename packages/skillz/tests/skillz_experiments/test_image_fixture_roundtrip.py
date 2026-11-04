from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cases import Fixture, load_cases
from skillz_experiments._evals_format import parse_journeys
from skillz_experiments._intake import IntakeCase, case_hash, freeze
from skillz_experiments._records import share_files
from skillz_experiments._seed import _fixture  # pyright: ignore[reportPrivateUsage]

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
MARKER = {"base64": base64.b64encode(PNG).decode("ascii"), "type": "png"}
SHIPPED_JOURNEYS = Path(__file__).resolve().parents[4] / "skills/skillz/evals/journeys.json"


@pytest.mark.ac("gh-589:AC-1")
def test_an_image_marker_round_trips_through_share_files_freeze_and_load_cases_with_a_stable_hash(tmp_path: Path) -> None:
    expected = {"criteria": [{"id": "r", "text": "THE answer SHALL be right."}]}
    cases = [IntakeCase(f"c{n}", f"f{n}", "task", "look", "skill", {"pic.png": MARKER, "n.txt": "hi"}, expected, None, False,
                        split="train") for n in range(2)]
    approved = case_hash(cases, 7)
    fixtures: dict[str, Fixture] = {}
    assert share_files([case.data() for case in cases], fixtures)[0]["files"] == ["pic.png", "n.txt"]
    out = tmp_path / "cases.json"
    freeze(out, cases, 7, approved)
    assert json.loads(out.read_text())["fixtures"]["pic.png"] == MARKER
    loaded = load_cases(out, {"task": "judge"})
    assert [case.files for case in loaded] == [{"pic.png": MARKER, "n.txt": "hi"}] * 2
    rebuilt = [IntakeCase(case.identifier, case.family, "task", case.request, "skill", case.files, case.expected, None, False,
                          split="train") for case in loaded]
    assert case_hash(rebuilt, 7) == approved


@pytest.mark.ac("gh-589:AC-1")
def test_a_journey_case_keeps_an_inline_image_marker_in_its_files_map() -> None:
    shipped = cast(list[dict[str, object]], json.loads(SHIPPED_JOURNEYS.read_text()))
    parsed = parse_journeys([shipped[0] | {"files": {"evals/files/pic.png": MARKER}}], strict=False)
    assert parsed.errors == ()
    assert parsed.cases[0]["files"] == {"evals/files/pic.png": MARKER}


@pytest.mark.ac("gh-589:AC-2")
def test_a_utf8_text_fixture_that_starts_with_image_magic_stays_text(tmp_path: Path) -> None:
    text = "GIF89a is the header of a GIF file. Explain it.\n"
    (tmp_path / "evals/files").mkdir(parents=True)
    _ = (tmp_path / "evals/files/note.txt").write_text(text)
    assert _fixture(tmp_path, "evals/files/note.txt", {}) == text

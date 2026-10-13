"""The stall signal: a pure read of the proposals since the last validation gain."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from skillz_experiments.search import Signal, assess
from skillz_experiments.search._convergence import MIN_KNOWN, SHIFT_AT, STOP_AT, WARN_AT

ROOT = Path(__file__).resolve().parents[4]

GAIN = 0.2
NONE = 0.0


@pytest.mark.ac("gh-240:AC-1")
@pytest.mark.parametrize(("nones", "level"), [(2, "ok"), (3, "warn"), (4, "warn"), (5, "shift"), (7, "shift"),
                                              (8, "stop"), (12, "stop")])
def test_consecutive_none_observations_climb_from_warn_to_shift_to_stop(nones: int, level: str) -> None:
    signal = assess([GAIN] + [NONE] * nones, band=0.0)
    assert (signal.level, signal.streak) == (level, nones)


@pytest.mark.ac("gh-240:AC-1")
def test_fewer_than_four_known_observations_always_give_ok() -> None:
    assert assess([NONE] * 3, band=0.0) == Signal("ok", 3, "")
    assert assess([NONE, None, NONE, None, NONE], band=0.0).level == "ok"
    assert assess([], band=0.0) == Signal("ok", 0, "")


@pytest.mark.ac("gh-240:AC-2")
def test_a_gain_resets_the_streak() -> None:
    assert assess([NONE] * 7 + [GAIN], band=0.0) == Signal("ok", 0, "")
    assert assess([NONE] * 7 + [GAIN, NONE, NONE], band=0.0).streak == 2


@pytest.mark.ac("gh-240:AC-2")
def test_only_a_rise_above_the_band_is_a_gain() -> None:
    assert assess([NONE] * 4 + [0.05], band=0.05).streak == 5
    assert assess([NONE] * 4 + [0.06], band=0.05).streak == 0


@pytest.mark.ac("gh-240:AC-3")
def test_unknown_observations_leave_the_streak_unchanged() -> None:
    plain = assess([GAIN] + [NONE] * 4, band=0.0)
    mixed = assess([GAIN, None, NONE, None, None, NONE, NONE, None, NONE, None], band=0.0)
    assert mixed == plain and mixed.level == "warn"
    assert assess([NONE] * 7 + [None] * 5, band=0.0).level == "shift"


def test_the_advice_is_pinned_for_warn_and_shift_and_empty_otherwise() -> None:
    assert assess([GAIN] + [NONE] * 3, band=0.0).advice == (
        "The last 3 proposals gave no validation gain. Try a different kind of change.")
    assert assess([GAIN] + [NONE] * 6, band=0.0).advice == (
        "The last 6 proposals gave no validation gain. Change a different component or section. "
        "Do not refine the same lines again.")
    assert assess([GAIN] + [NONE] * 8, band=0.0).advice == ""


def _section(path: Path, heading: str) -> str:
    match = re.search(rf"^### {re.escape(heading)}.*?(?=^##|\Z)", path.read_text(encoding="utf-8"), re.M | re.S)
    assert match is not None, heading
    return match[0]


@pytest.mark.ac("gh-240:AC-6")
def test_the_experiments_reference_documents_the_stall_stop_and_its_thresholds() -> None:
    text = _section(ROOT / "skills/skillz/references/experiments.md", "Stall stop")
    assert "`converged-stalled`" in text
    assert f"From a streak of {WARN_AT}, the prompt advises a different kind of change." in text
    assert f"From a streak of {SHIFT_AT}, it advises a different component or section." in text
    assert f"At a streak of {STOP_AT}, the search stops" in text
    assert f"Fewer than {MIN_KNOWN} known observations" in text and "always on" in text


@pytest.mark.ac("gh-240:AC-7")
def test_an_adr_records_the_default_on_stall_stop_and_its_thresholds() -> None:
    text = _section(ROOT / "docs/agents/decisions/skillz-autoimprove.md", "ADR-016: Stop a stalled search by default")
    assert "[status: accepted]" in text and "on by default" in text and "`converged-stalled`" in text
    assert f"`warn` at a streak of {WARN_AT}, `shift` at {SHIFT_AT}, and `stop` at {STOP_AT}." in text
    assert f"Fewer than {MIN_KNOWN} known observations give `ok`." in text
    assert "`Statistics.min_gain`" in text

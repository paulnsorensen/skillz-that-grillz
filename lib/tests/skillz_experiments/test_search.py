from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import pytest

from skillz_experiments._search import Mode, optimize
from skillz_experiments._wedge import admit, new_script


@pytest.mark.parametrize("mode", ["prompt", "prompt-cli"])
def test_real_gepa_evaluates_changed_components(mode: Mode, capsys: pytest.CaptureFixture[str]) -> None:
    seed = {"SKILL.md": "seed", "scripts/inspect_skill.py": "original"}
    replacement = "better PRIVATE_CANDIDATE_SENTINEL"
    seen: list[tuple[dict[str, str], object]] = []

    def evaluate(candidate: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        assert example in ("train", "validation")
        seen.append((candidate.copy(), example))
        return float(candidate["SKILL.md"] == replacement), {"failure": "Use better"}

    def propose(candidate: dict[str, str], reflective_dataset: Mapping[str, Sequence[Mapping[str, object]]],
                components_to_update: list[str]) -> dict[str, str]:
        assert candidate
        assert reflective_dataset
        return {key: replacement if key == "SKILL.md" else "changed" for key in components_to_update}

    winner = optimize(seed, mode, ["train"], ["validation"], evaluate, propose, code="scripts/inspect_skill.py")
    assert winner["SKILL.md"] == replacement
    assert winner["scripts/inspect_skill.py"] == ("original" if mode == "prompt" else "changed")
    assert any(item != seed and example == "train" for item, example in seen)
    assert any(item != seed and example == "validation" for item, example in seen)
    captured = capsys.readouterr()
    assert "PRIVATE_CANDIDATE_SENTINEL" not in captured.out + captured.err


def test_cli_gepa_freezes_all_text() -> None:
    seed = {"SKILL.md": "seed", "references/selected.md": "reference",
            "scripts/inspect_skill.py": "original", "scripts/other.py": "frozen"}
    seen: list[tuple[dict[str, str], object]] = []

    def evaluate(candidate: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        seen.append((candidate.copy(), example))
        assert {key: value for key, value in candidate.items() if key != "scripts/inspect_skill.py"} == {
            key: value for key, value in seed.items() if key != "scripts/inspect_skill.py"}
        return float(candidate["scripts/inspect_skill.py"] == "changed"), {"task_correct": 0.0}

    def propose(candidate: dict[str, str], feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        assert candidate == {"scripts/inspect_skill.py": "original"}
        assert components == ["scripts/inspect_skill.py"]
        assert feedback
        return {"scripts/inspect_skill.py": "changed"}

    winner = optimize(seed, "cli", ["train"], ["validation"], evaluate, propose, code="scripts/inspect_skill.py")
    assert winner == seed | {"scripts/inspect_skill.py": "changed"}
    assert (winner, "train") in seen
    assert (winner, "validation") in seen


SEED_WEDGE = {"SKILL.md": "seed", "scripts/inspect_skill.py": "original"}
NEW_PATH = "scripts/offload.py"


def _proposal(files: dict[str, str], skill: str = f"Run {NEW_PATH}.") -> dict[str, str]:
    return {"SKILL.md": skill, "wedge-files": json.dumps(files)}


def test_wedge_admission_accepts_one_referenced_new_script() -> None:
    assert admit(SEED_WEDGE, _proposal({NEW_PATH: "print(1)\n"})) == {NEW_PATH: "print(1)\n"}
    assert new_script(SEED_WEDGE, SEED_WEDGE | {NEW_PATH: "x"}) == NEW_PATH
    assert new_script(SEED_WEDGE, SEED_WEDGE) is None


@pytest.mark.parametrize("proposal", [
    _proposal({NEW_PATH: "x"}, skill="Run nothing."),
    _proposal({NEW_PATH: "x", "scripts/second.py": "y"}),
    _proposal({"references/notes.py": "x"}, skill="Run references/notes.py."),
    _proposal({"notes.md": "x"}, skill="Run notes.md."),
    _proposal({"scripts/inspect_skill.py": "changed"}, skill="Run scripts/inspect_skill.py."),
    _proposal({"scripts/../escape.py": "x"}, skill="Run scripts/../escape.py."),
    _proposal({}),
    {"SKILL.md": f"Run {NEW_PATH}.", "wedge-files": "not json"},
    {"SKILL.md": f"Run {NEW_PATH}.", "wedge-files": json.dumps({NEW_PATH: 7})},
    {"SKILL.md": f"Run {NEW_PATH}.", "wedge-files": "[" * 100_000},
    _proposal({NEW_PATH: "x"}, skill="Run scripts/offload.pyz."),
    _proposal({NEW_PATH: "x"}, skill="Run myscripts/offload.py."),
    _proposal({NEW_PATH: "x"}, skill=f"Run python3 .agents/skills/other-skill/{NEW_PATH}."),
])
def test_wedge_admission_rejects_other_proposals(proposal: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        _ = admit(SEED_WEDGE, proposal, "echo-skill")


def test_wedge_mode_searches_skill_text_and_the_wedge_component() -> None:
    seed = {"SKILL.md": "seed", "wedge-files": "{}"}

    def evaluate(candidate: dict[str, str], _example: object) -> tuple[float, dict[str, object]]:
        return float(NEW_PATH in candidate["wedge-files"]), {"task_correct": 0.0}

    def propose(candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        assert sorted(components) == ["SKILL.md", "wedge-files"]
        assert sorted(candidate) == ["SKILL.md", "wedge-files"]
        return _proposal({NEW_PATH: "print(1)\n"})

    winner = optimize(seed, "wedge", ["train"], ["validation"], evaluate, propose, code=None)
    assert NEW_PATH in winner["wedge-files"]


@pytest.mark.parametrize("prefix", ["./", "${CLAUDE_SKILL_DIR}/", "<this-skill-directory>/", ".agents/skills/echo-skill/", ""])
def test_wedge_admission_accepts_a_prefixed_reference(prefix: str) -> None:
    skill = f"Run python3 {prefix}{NEW_PATH}."
    assert admit(SEED_WEDGE, _proposal({NEW_PATH: "x"}, skill=skill), "echo-skill") == {NEW_PATH: "x"}


def test_wedge_admission_accepts_a_reference_that_ends_a_sentence() -> None:
    assert admit(SEED_WEDGE, _proposal({NEW_PATH: "x"}, skill=f"Run `{NEW_PATH}`. Then stop.")) == {NEW_PATH: "x"}


def test_cli_search_without_a_helper_path_is_an_error_not_the_skillz_default() -> None:
    with pytest.raises(ValueError, match="needs a helper path"):
        _ = optimize({"SKILL.md": "seed"}, "cli", ["train"], ["validation"],
                     lambda candidate, example: (0.0, {}), lambda candidate, feedback, components: {}, code=None)

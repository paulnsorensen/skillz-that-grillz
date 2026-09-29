from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from skillz_experiments._search import optimize


@pytest.mark.parametrize("mode", ["prompt", "prompt-cli"])
def test_real_gepa_evaluates_changed_components(mode: str, capsys: pytest.CaptureFixture[str]) -> None:
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

    winner = optimize(seed, mode, ["train"], ["validation"], evaluate, propose)
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

    winner = optimize(seed, "cli", ["train"], ["validation"], evaluate, propose)
    assert winner == seed | {"scripts/inspect_skill.py": "changed"}
    assert (winner, "train") in seen
    assert (winner, "validation") in seen

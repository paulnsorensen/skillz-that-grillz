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

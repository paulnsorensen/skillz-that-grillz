from pathlib import Path

from skillz_experiments._discovery import valid_listing


def test_native_listing_requires_exact_enabled_skill_and_no_errors(tmp_path: Path) -> None:
    skill: dict[str, object] = {"name": "skillz", "path": str(tmp_path / ".agents/skills/skillz/SKILL.md"),
                                "scope": "repo", "enabled": True, "pluginId": None}
    assert valid_listing({"data": [{"cwd": str(tmp_path), "skills": [skill], "errors": []}]}, tmp_path)
    assert not valid_listing({"data": [{"cwd": str(tmp_path), "skills": [dict(skill, name="other")], "errors": []}]}, tmp_path)
    assert not valid_listing({"data": [{"cwd": str(tmp_path), "skills": [dict(skill, enabled=False)], "errors": []}]}, tmp_path)
    assert not valid_listing({"data": [{"cwd": str(tmp_path), "skills": [skill], "errors": ["invalid"]}]}, tmp_path)
    assert not valid_listing({"data": [{"cwd": str(tmp_path), "skills": [skill, dict(skill, name="host")], "errors": []}]}, tmp_path)

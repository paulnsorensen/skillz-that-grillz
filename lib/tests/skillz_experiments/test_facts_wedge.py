"""`audit-facts` wedge checks: own targets, frozen sources, and foreign binaries (C4, AC-15). No build, no network."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._facts import audit_facts
from skillz_experiments._wedge_targets import WEDGE_EDIT_LIMIT, _scan, load_sites  # pyright: ignore[reportPrivateUsage]

ROOT = Path(__file__).resolve().parents[3]
SKILL = "---\nname: demo\ndescription: Does a demo thing. Use when asked. Do NOT use for other things.\n---\n# demo\n"
Checks = list[dict[str, object]]


def make_skill(tmp_path: Path, *, alpha: dict[str, str | bytes] | None = None, foreign: bool = False,
               sub: bool = False) -> Path:
    repo = tmp_path / "repo"
    _ = subprocess.run(["git", "init", "-q", str(repo)], check=True)
    skill = repo / "skills/demo"
    (skill / "scripts").mkdir(parents=True)
    for package in ("alpha", "beta", "sub"):
        (repo / package).mkdir()
        _ = (repo / package / "__init__.py").write_text("x = 1\n")
    _ = (repo / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\n')
    _ = (repo / "uv.lock").write_text("version = 1\n")
    _ = (repo / ".gitignore").write_text("alpha/ignored.py\n")
    for name, content in (alpha or {}).items():
        _ = (repo / "alpha" / name).write_bytes(content if isinstance(content, bytes) else content.encode())
    _ = (skill / "SKILL.md").write_text(SKILL)
    _ = (skill / "wedge.toml").write_text(
        'project = "../.."\nrepo = "o/n"\n\n[[target]]\nname = "alpha"\nentry = "alpha:main"\nsource = "../../alpha"\n\n'
        + '[[target]]\nname = "beta"\nentry = "beta:main"\nsource = "../../beta"\n')
    if sub:
        (skill / "wedge/scripts").mkdir(parents=True)
        _ = (skill / "wedge/wedge.toml").write_text(
            'name = "sub"\nentry = "sub:main"\nsource = "../../../sub"\nproject = "../../.."\nrepo = "o/n"\n')
        _ = (skill / "wedge/scripts/sub.pyz").write_bytes(b"PK")
    if foreign:
        (skill / "nested/scripts").mkdir(parents=True)
        _ = (skill / "nested/scripts/other.pyz").write_bytes(b"PK")
    return skill


def wedge_checks(skill: Path) -> Checks:
    checks = cast(Checks, audit_facts(skill)["checks"])
    return [check for check in checks if str(check["id"]).startswith("wedge.")]


def own_row(skill: Path) -> dict[str, object]:
    rows = [check for check in wedge_checks(skill) if check["id"] == "wedge.own-targets"]
    assert len(rows) == 1
    return rows[0]


def test_own_targets_are_listed_editable_and_a_clean_skill_has_no_flag(tmp_path: Path) -> None:
    checks = wedge_checks(make_skill(tmp_path))
    assert [(check["id"], check["status"]) for check in checks] == [("wedge.own-targets", "pass")]
    assert "`alpha` (editable)" in str(checks[0]["detail"]) and "`beta` (editable)" in str(checks[0]["detail"])
    assert set(checks[0]) == {"id", "rule", "path", "line", "status", "detail"}


def test_an_over_limit_target_is_listed_frozen_and_is_no_failure(tmp_path: Path) -> None:
    skill = make_skill(tmp_path, alpha={"big.py": "#" * (WEDGE_EDIT_LIMIT + 1)})
    checks = wedge_checks(skill)
    assert [(check["id"], check["status"]) for check in checks] == [("wedge.own-targets", "pass")]
    detail = str(checks[0]["detail"])
    assert "`alpha` (frozen:" in detail and str(WEDGE_EDIT_LIMIT + 1 + 6) in detail
    assert "`beta` (editable)" in detail  # each target is checked alone, so a big first target does not freeze a later one


def test_the_edit_limit_counts_characters_not_bytes(tmp_path: Path) -> None:
    text = "é" * 60_000
    assert len(text) < WEDGE_EDIT_LIMIT < len(text.encode())
    detail = str(own_row(make_skill(tmp_path, alpha={"big.py": text}))["detail"])
    assert "`alpha` (editable)" in detail


@pytest.mark.parametrize(("name", "content"), [("ignored.py", "x = 1\n"), ("blob.bin", b"\x00\x01"),
                                                ("latin.py", b"\xff\xfe"), ("huge.py", "#" * 262_145)],
                         ids=["git-ignored", "binary", "not-utf8", "over-text-limit"])
def test_a_source_the_planner_cannot_edit_freezes_the_target(tmp_path: Path, name: str, content: str | bytes) -> None:
    skill = make_skill(tmp_path, alpha={name: content})
    site = next(site for site in load_sites(skill) if site.name == "alpha")
    assert _scan(site.paths, site.repo)[1] is None
    detail = str(own_row(skill)["detail"])
    assert "`alpha` (frozen: its sources cannot be edited" in detail and "`beta` (editable)" in detail


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_an_unreadable_source_is_a_failed_check_not_size_zero(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    target = tmp_path / "repo/alpha/__init__.py"
    target.chmod(0)
    try:
        row = own_row(skill)
    finally:
        target.chmod(0o644)
    assert row["status"] == "fail" and "alpha" in str(row["detail"])


def test_an_installed_copy_without_a_project_is_not_applicable(tmp_path: Path) -> None:
    skill = make_skill(tmp_path, foreign=True, sub=True)
    installed = tmp_path / "installed" / "demo"
    _ = shutil.copytree(skill, installed)
    checks = wedge_checks(installed)
    assert [(check["id"], check["status"]) for check in checks] == [("wedge.own-targets", "not-applicable")]
    assert "pyproject.toml" in str(checks[0]["detail"])


def test_an_invalid_wedge_toml_still_fails_without_a_project(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    _ = (skill / "wedge.toml").write_text("name = [\n")
    assert [(check["id"], check["status"]) for check in wedge_checks(skill)] == [("wedge.own-targets", "fail")]


def test_a_pyz_that_no_own_config_builds_is_a_foreign_binary(tmp_path: Path) -> None:
    checks = wedge_checks(make_skill(tmp_path, foreign=True, sub=True))
    foreign = [check for check in checks if check["id"] == "wedge.foreign-binary"]
    assert [check["path"] for check in foreign] == ["nested/scripts/other.pyz"] and foreign[0]["status"] == "fail"


def test_a_pyz_built_by_a_lower_wedge_toml_is_own(tmp_path: Path) -> None:
    checks = wedge_checks(make_skill(tmp_path, sub=True))
    assert [(check["id"], check["status"]) for check in checks] == [("wedge.own-targets", "pass")]


def test_a_pyz_built_by_a_nested_skills_wedge_toml_is_foreign(tmp_path: Path) -> None:
    skill = make_skill(tmp_path, sub=True)
    _ = (skill / "wedge/SKILL.md").write_text(SKILL)
    foreign = [check["path"] for check in wedge_checks(skill) if check["id"] == "wedge.foreign-binary"]
    assert foreign == ["wedge/scripts/sub.pyz"]


def test_a_skill_without_wedge_files_has_no_wedge_check(tmp_path: Path) -> None:
    skill = tmp_path / "plain"
    skill.mkdir()
    _ = (skill / "SKILL.md").write_text(SKILL)
    assert wedge_checks(skill) == []


def test_the_real_skillz_skill_has_no_wedge_failure() -> None:
    checks = wedge_checks(ROOT / "skills/skillz")
    assert [check for check in checks if check["status"] != "pass"] == []
    row = own_row(ROOT / "skills/skillz")
    assert "`skillz-experiment` (frozen:" in str(row["detail"]) and "`inspect-skill` (editable)" in str(row["detail"])

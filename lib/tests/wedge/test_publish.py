"""AC-W6: publish is idempotent and race-safe against a fake ``gh`` CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, TypedDict

import pytest

import wedge._publish as wedge_publish
from wedge._lock import load_lock
from wedge._publish import publish

FIXTURE_NAME = "cheese-cave"


class FakeGh(TypedDict):
    store: Path
    repo: str


def _upload_count(store: Path) -> int:
    assets_dir = store / "assets"
    if not assets_dir.is_dir():
        return 0
    return len(list(assets_dir.iterdir()))


@pytest.mark.ac("AC-W6")
def test_publish_then_republish_skips_without_a_second_upload(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    repo = fake_gh["repo"]

    first = publish([skill], repo=repo, target="deadbeef")
    assert first[FIXTURE_NAME]["status"] == "published"
    uploads_after_first = _upload_count(fake_gh["store"])
    assert uploads_after_first == 1

    second = publish([skill], repo=repo, target="deadbeef")
    assert second[FIXTURE_NAME]["status"] == "skipped"
    assert _upload_count(fake_gh["store"]) == uploads_after_first


@pytest.mark.ac("AC-W6")
def test_no_wedged_skills_makes_no_gh_calls(fake_gh: FakeGh) -> None:
    # main has no wedged skills yet; a merge must not create an empty release.
    assert publish([], repo=fake_gh["repo"], target="deadbeef") == {}
    assert not (fake_gh["store"] / "calls.log").exists()


@pytest.mark.ac("AC-W6")
def test_stale_lock_fails_before_any_upload(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    source = skill.parent.parent.parent / "fromargs" / "examples" / "cheese_cave.py"
    source.write_text(source.read_text() + "\n# touched\n")
    repo = fake_gh["repo"]

    result = publish([skill], repo=repo, target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "failed"
    assert "stale" in result[FIXTURE_NAME]["reason"]
    assert _upload_count(fake_gh["store"]) == 0


@pytest.mark.ac("AC-W6")
def test_conflicting_existing_asset_fails_on_digest(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    repo = fake_gh["repo"]
    lock_data = load_lock(skill, FIXTURE_NAME)
    subprocess.run(["gh", "release", "create", "wedge", "--repo", repo], check=True)
    conflicting = tmp_path / lock_data.asset
    conflicting.write_bytes(b"not the real pyz")
    subprocess.run(
        ["gh", "release", "upload", "wedge", str(conflicting), "--repo", repo], check=True
    )

    result = publish([skill], repo=repo, target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "failed"
    assert "digest" in result[FIXTURE_NAME]["reason"]


@pytest.mark.ac("AC-W6")
def test_a_racing_identical_upload_is_treated_as_skipped(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    repo = fake_gh["repo"]
    real_run = wedge_publish._run

    def _racing_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["release", "upload"]:
            asset_path = Path(args[3])
            subprocess.run(
                ["gh", "release", "upload", wedge_publish.RELEASE, str(asset_path), "--repo", repo],
                check=True,
            )
        return real_run(args)

    monkeypatch.setattr(wedge_publish, "_run", _racing_run)

    result = publish([skill], repo=repo, target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "skipped"
    assert _upload_count(fake_gh["store"]) == 1


@pytest.mark.ac("AC-W6")
def test_ensure_release_tolerates_a_create_race(
    fake_gh: FakeGh, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = fake_gh["repo"]
    real_run = wedge_publish._run
    state = {"rival_ran": False}

    def _racing_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["release", "create"] and not state["rival_ran"]:
            state["rival_ran"] = True
            subprocess.run(["gh", "release", "create", "wedge", "--repo", repo], check=True)
        return real_run(args)

    monkeypatch.setattr(wedge_publish, "_run", _racing_run)

    wedge_publish._ensure_release(repo, "deadbeef")

    assert state["rival_ran"]
    assert real_run(["release", "view", "wedge", "--repo", repo]).returncode == 0


def _publish_subprocess_script(skill: Path, repo: str) -> str:
    return (
        "import json\n"
        "from pathlib import Path\n"
        "from wedge._publish import publish\n"
        f"result = publish([Path({str(skill)!r})], repo={repo!r}, target='deadbeef')\n"
        "print(json.dumps(result))\n"
    )


@pytest.mark.ac("AC-W6")
def test_two_concurrent_publishes_yield_exactly_one_upload(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill_a = copy_locked_fixture(tmp_path / "checkout-a")
    skill_b = copy_locked_fixture(tmp_path / "checkout-b")
    repo = fake_gh["repo"]
    env = dict(os.environ)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _publish_subprocess_script(skill, repo)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for skill in (skill_a, skill_b)
    ]
    outputs = [proc.communicate() for proc in procs]

    for proc, (_, stderr) in zip(procs, outputs):
        assert proc.returncode == 0, stderr

    statuses = sorted(json.loads(stdout)[FIXTURE_NAME]["status"] for stdout, _ in outputs)
    assert statuses == ["published", "skipped"]
    assert _upload_count(fake_gh["store"]) == 1

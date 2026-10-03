"""AC-W6: publish is idempotent and race-safe against a fake ``gh`` CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, TypedDict, cast

import pytest

import wedge._publish as wedge_publish
from wedge._build import build
from wedge._cli import publish_cmd
from wedge._digest import content_sha256
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


def _seed_branch(fake_gh: FakeGh, branch: str, history: list[str]) -> None:
    """Tell the fake gh which commits ``branch`` contains, tip last."""
    state_path = fake_gh["store"] / "state.json"
    state = cast(dict[str, dict[str, object]], json.loads(state_path.read_text())) if state_path.is_file() else {}
    state.setdefault(fake_gh["repo"], {})["branches"] = {branch: history}
    _ = state_path.write_text(json.dumps(state))


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _commit(skill_dir: Path, message: str) -> str:
    _ = _git("add", "-A", cwd=skill_dir)
    _ = subprocess.run(
        [
            "git", "-c", "user.name=t", "-c", "user.email=t@t",
            "-C", str(skill_dir), "commit", "-q", "-m", message,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return _git("rev-parse", "HEAD", cwd=skill_dir)


def _init_git_repo(skill_dir: Path) -> str:
    """Make ``skill_dir`` a real git repo at one commit; returns its HEAD sha."""
    _ = subprocess.run(["git", "init", "-q", "-b", "main", str(skill_dir)], check=True, capture_output=True)
    return _commit(skill_dir, "seed")


def _init_git_repo_with_second_commit(skill_dir: Path) -> tuple[str, str]:
    """Two commits on ``main``; ``skill_dir``'s checkout is left detached at the first.

    Returns ``(first_sha, second_sha)``."""
    first = _init_git_repo(skill_dir)
    marker = skill_dir / ".second-commit"
    _ = marker.write_text("second\n")
    second = _commit(skill_dir, "second")
    _ = _git("checkout", "-q", first, cwd=skill_dir)
    return first, second


@pytest.mark.ac("AC-W6")
def test_publish_refuses_a_target_the_branch_does_not_contain(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    sha = _init_git_repo(skill)
    _seed_branch(fake_gh, "main", ["c0ffee", "deadbeef"])

    with pytest.raises(ValueError, match="is not on main"):
        _ = publish([skill], repo=fake_gh["repo"], target=sha, branch="main")

    calls = (fake_gh["store"] / "calls.log").read_text().splitlines()
    assert all(json.loads(call)[0] == "api" for call in calls), "refused before any release call"
    assert _upload_count(fake_gh["store"]) == 0


@pytest.mark.ac("AC-W6")
@pytest.mark.parametrize("tip_last", [True, False])
def test_publish_accepts_the_branch_tip_or_an_ancestor(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
    tip_last: bool,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    sha = _init_git_repo(skill)
    history = ["c0ffee", sha] if tip_last else [sha, "deadbeef"]
    _seed_branch(fake_gh, "main", history)

    result = publish([skill], repo=fake_gh["repo"], target=sha, branch="main")

    assert result[FIXTURE_NAME]["status"] == "published"


@pytest.mark.ac("AC-W6")
def test_publish_accepts_a_shallow_checkout_of_the_target(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    """``_resolve_commit`` needs only the target object, not its ancestry, so
    a shallow clone of the target commit must still pass."""
    skill = copy_locked_fixture(tmp_path / "checkout")
    sha = _init_git_repo(skill)
    _ = subprocess.run(["git", "-C", str(skill), "repack", "-adq"], check=True, capture_output=True)
    _seed_branch(fake_gh, "main", ["c0ffee", sha])

    result = publish([skill], repo=fake_gh["repo"], target=sha, branch="main")

    assert result[FIXTURE_NAME]["status"] == "published"


@pytest.mark.ac("AC-W6")
@pytest.mark.parametrize("use_branch_name", [False, True])
def test_publish_refuses_a_target_that_is_not_the_checkout_head(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
    use_branch_name: bool,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    _first, second = _init_git_repo_with_second_commit(skill)
    target = "main" if use_branch_name else second

    with pytest.raises(ValueError, match="resolves to"):
        _ = publish([skill], repo=fake_gh["repo"], target=target, branch="main")

    assert not (fake_gh["store"] / "calls.log").exists(), "refused before any gh call"


@pytest.mark.ac("AC-W6")
def test_publish_cmd_refuses_a_target_the_branch_does_not_contain(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    sha = _init_git_repo(skill)
    _seed_branch(fake_gh, "main", ["c0ffee", "deadbeef"])

    with pytest.raises(ValueError, match="is not on main"):
        _ = publish_cmd([str(skill)], repo=fake_gh["repo"], target=sha, branch="main")

    assert _upload_count(fake_gh["store"]) == 0


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
    _ = source.write_text(source.read_text() + "\n# touched\n")
    repo = fake_gh["repo"]

    result = publish([skill], repo=repo, target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "failed"
    assert "stale" in result[FIXTURE_NAME]["reason"]
    assert _upload_count(fake_gh["store"]) == 0




@pytest.mark.ac("AC-W6")
def test_config_repository_mismatch_fails_before_gh(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    config = skill / "wedge.toml"
    _ = config.write_text(config.read_text().replace(
        'repo = "paulnsorensen/skillz-that-grillz"',
        'repo = "other-owner/other-repo"',
    ))

    with pytest.raises(ValueError, match="repository"):
        _ = publish([skill], repo=fake_gh["repo"], target="deadbeef")

    assert not (fake_gh["store"] / "calls.log").exists()



@pytest.mark.ac("AC-W6")
@pytest.mark.parametrize("repo", ['"   "', '"owner/"', '"/repo"'])
def test_invalid_config_repository_is_rejected_before_gh(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
    repo: str,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    config = skill / "wedge.toml"
    _ = config.write_text(config.read_text().replace(
        'repo = "paulnsorensen/skillz-that-grillz"', f"repo = {repo}"
    ))

    result = publish([skill], repo=fake_gh["repo"], target="deadbeef")
    assert result[FIXTURE_NAME]["status"] == "failed"

    assert not (fake_gh["store"] / "calls.log").exists()


@pytest.mark.ac("AC-W6")
def test_lock_repository_mismatch_fails_before_gh(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    lock_path = skill / "scripts" / f"{FIXTURE_NAME}.wedge.json"
    lock_data = cast(dict[str, object], json.loads(lock_path.read_text()))
    lock_data["repo"] = "other-owner/other-repo"
    _ = lock_path.write_text(json.dumps(lock_data))

    with pytest.raises(ValueError, match="lock repository"):
        _ = publish([skill], repo=fake_gh["repo"], target="deadbeef")

    assert not (fake_gh["store"] / "calls.log").exists()
@pytest.mark.ac("AC-W6")
def test_conflicting_existing_asset_fails_on_digest(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    repo = fake_gh["repo"]
    lock_data = load_lock(skill, FIXTURE_NAME)
    _ = subprocess.run(["gh", "release", "create", "wedge", "--repo", repo], check=True)
    conflicting = tmp_path / lock_data.asset
    _ = conflicting.write_bytes(b"not the real pyz")
    _ = subprocess.run(
        ["gh", "release", "upload", "wedge", str(conflicting), "--repo", repo], check=True
    )

    result = publish([skill], repo=repo, target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "failed"
    assert "digest" in result[FIXTURE_NAME]["reason"]


@pytest.mark.ac("AC-W6")
def test_lock_for_another_repo_fails_without_an_upload(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    with pytest.raises(ValueError, match="publish repository"):
        _ = publish([skill], repo="someone-else/fork", target="deadbeef")
    assert _upload_count(fake_gh["store"]) == 0


@pytest.mark.ac("AC-W6")
def test_bad_layout_is_a_failed_result_not_a_crash(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    toml = skill / "wedge.toml"
    _ = toml.write_text(toml.read_text().replace("src/fromargs", "src/missing"))

    result = publish([skill], repo=fake_gh["repo"], target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "failed"
    assert "does not exist" in result[FIXTURE_NAME]["reason"]
    assert _upload_count(fake_gh["store"]) == 0


@pytest.mark.ac("AC-W6")
def test_a_racing_identical_upload_is_treated_as_skipped(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    repo = fake_gh["repo"]
    real_run = cast(Callable[[list[str]], subprocess.CompletedProcess[str]], getattr(wedge_publish, "_run"))

    def _racing_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["release", "upload"]:
            asset_path = Path(args[3])
            _ = subprocess.run(
                ["gh", "release", "upload", wedge_publish.RELEASE, str(asset_path), "--repo", repo],
                check=True,
            )
        return real_run(args)

    monkeypatch.setattr(wedge_publish, "_run", _racing_run)

    result = publish([skill], repo=repo, target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "skipped"
    assert _upload_count(fake_gh["store"]) == 1




@pytest.mark.ac("AC-W6")
def test_duplicate_names_fail_before_gh(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill_a = copy_locked_fixture(tmp_path / "checkout-a")
    skill_b = copy_locked_fixture(tmp_path / "checkout-b")

    with pytest.raises(ValueError, match="duplicate"):
        _ = publish([skill_a, skill_b], repo=fake_gh["repo"], target="deadbeef")

    assert not (fake_gh["store"] / "calls.log").exists()
@pytest.mark.ac("AC-W6")
def test_ensure_release_tolerates_a_create_race(
    fake_gh: FakeGh, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = fake_gh["repo"]
    real_run = cast(Callable[[list[str]], subprocess.CompletedProcess[str]], getattr(wedge_publish, "_run"))
    state = {"rival_ran": False}

    def _racing_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["release", "create"] and not state["rival_ran"]:
            state["rival_ran"] = True
            _ = subprocess.run(["gh", "release", "create", "wedge", "--repo", repo], check=True)
        return real_run(args)

    monkeypatch.setattr(wedge_publish, "_run", _racing_run)

    ensure_release = cast(Callable[[str, str], None], getattr(wedge_publish, "_ensure_release"))
    ensure_release(repo, "deadbeef")

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


@pytest.mark.ac("AC-W6")
def test_release_create_uses_full_publish_flags(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")

    result = publish([skill], repo=fake_gh["repo"], target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "published"
    calls = [json.loads(line) for line in (fake_gh["store"] / "calls.log").read_text().splitlines()]
    assert [
        "release",
        "create",
        "wedge",
        "--repo",
        fake_gh["repo"],
        "--title",
        "wedge",
        "--notes",
        "Rolling release: content-addressed wedge .pyz assets.",
        "--prerelease",
        "--latest=false",
        "--target",
        "deadbeef",
    ] in calls



@pytest.mark.ac("AC-W6")
def test_malformed_config_does_not_block_later_valid_skill(
    tmp_path: Path, copy_locked_fixture: Callable[[Path], Path], fake_gh: FakeGh
) -> None:
    malformed = copy_locked_fixture(tmp_path / "malformed")
    valid = copy_locked_fixture(tmp_path / "valid")
    config = malformed / "wedge.toml"
    _ = config.write_text(config.read_text().replace(
        'repo = "paulnsorensen/skillz-that-grillz"', 'repo = "not-a-slug"'
    ))

    result = publish([malformed, valid], repo=fake_gh["repo"], target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "published"
    assert result["cheese-cave-0"]["status"] == "failed"


@pytest.mark.ac("AC-W6")
def test_malformed_lock_does_not_block_later_valid_skill(
    tmp_path: Path, copy_locked_fixture: Callable[[Path], Path], fake_gh: FakeGh
) -> None:
    malformed = copy_locked_fixture(tmp_path / "malformed")
    valid = copy_locked_fixture(tmp_path / "valid")
    config = malformed / "wedge.toml"
    _ = config.write_text(config.read_text().replace(
        'name = "cheese-cave"', 'name = "broken-skill"'
    ))
    lock = malformed / "scripts" / "broken-skill.wedge.json"
    _ = lock.write_text("{}\n")

    result = publish([malformed, valid], repo=fake_gh["repo"], target="deadbeef")

    assert result["broken-skill"]["status"] == "failed"
    assert result[FIXTURE_NAME]["status"] == "published"
    assert "invalid fields" in result["broken-skill"]["reason"]


@pytest.mark.ac("AC-W6")
def test_direct_bundle_is_skipped_without_gh_calls(
    tmp_path: Path, copy_locked_fixture: Callable[[Path], Path], fake_gh: FakeGh
) -> None:
    # ADR-010: a direct bundle commits scripts/NAME.pyz and has no lock;
    # `wedge bundle --check` owns it, so release publication leaves it alone.
    skill = copy_locked_fixture(tmp_path / "direct")
    scripts = skill / "scripts"
    (scripts / f"{FIXTURE_NAME}.wedge.json").unlink()
    (scripts / FIXTURE_NAME).unlink()
    _ = (scripts / f"{FIXTURE_NAME}.pyz").write_bytes(b"bundle")

    result = publish([skill], repo=fake_gh["repo"], target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "skipped"
    assert "direct bundle" in result[FIXTURE_NAME]["reason"]
    assert not (fake_gh["store"] / "calls.log").exists()


@pytest.mark.ac("AC-W6")
def test_missing_lock_without_a_bundle_still_fails(
    tmp_path: Path, copy_locked_fixture: Callable[[Path], Path], fake_gh: FakeGh
) -> None:
    skill = copy_locked_fixture(tmp_path / "unlocked")
    (skill / "scripts" / f"{FIXTURE_NAME}.wedge.json").unlink()

    result = publish([skill], repo=fake_gh["repo"], target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "failed"
    assert not (fake_gh["store"] / "calls.log").exists()


@pytest.mark.ac("AC-W6")
def test_all_malformed_locks_make_no_gh_calls(
    tmp_path: Path, copy_locked_fixture: Callable[[Path], Path], fake_gh: FakeGh
) -> None:
    malformed = copy_locked_fixture(tmp_path / "malformed")
    config = malformed / "wedge.toml"
    _ = config.write_text(config.read_text().replace(
        'name = "cheese-cave"', 'name = "broken-skill"'
    ))
    lock = malformed / "scripts" / "broken-skill.wedge.json"
    _ = lock.write_text("{}\n")

    result = publish([malformed], repo=fake_gh["repo"], target="deadbeef")

    assert result["broken-skill"]["status"] == "failed"
    assert not (fake_gh["store"] / "calls.log").exists()
    assert "invalid fields" in result["broken-skill"]["reason"]


@pytest.mark.ac("AC-W6")
def test_asset_from_another_zlib_build_counts_as_published(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
    rewrite_pyz: Callable[..., Path],
) -> None:
    skill = copy_locked_fixture(tmp_path / "checkout")
    repo = fake_gh["repo"]
    built = build(skill, tmp_path / "local")
    other = rewrite_pyz(built.path, tmp_path / "other" / built.path.name, level=1)
    _ = subprocess.run(["gh", "release", "create", "wedge", "--repo", repo], check=True)
    _ = subprocess.run(["gh", "release", "upload", "wedge", str(other), "--repo", repo], check=True)

    result = publish([skill], repo=repo, target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "skipped"
    assert _upload_count(fake_gh["store"]) == 1


@pytest.mark.ac("AC-W6")
def test_published_asset_is_not_trusted_when_this_host_builds_other_content(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
    rewrite_pyz: Callable[..., Path],
) -> None:
    """A lock and asset written on another host agree with each other, but
    this host builds different content from the same key. Publish must fail
    rather than skip, or a corrupt build from that host ships unverified."""
    skill = copy_locked_fixture(tmp_path / "checkout")
    repo = fake_gh["repo"]
    built = build(skill, tmp_path / "local")
    foreign = rewrite_pyz(
        built.path, tmp_path / "foreign.pyz", extra={"site-packages/edited.py": b"# other host\n"}
    )
    foreign_digest = content_sha256(foreign)
    foreign_asset = foreign.rename(tmp_path / f"{FIXTURE_NAME}-{foreign_digest[:12]}.pyz")
    lock_path = skill / "scripts" / f"{FIXTURE_NAME}.wedge.json"
    lock_data = cast(dict[str, object], json.loads(lock_path.read_text()))
    lock_data["content_sha256"] = foreign_digest
    lock_data["asset"] = foreign_asset.name
    _ = lock_path.write_text(json.dumps(lock_data))
    _ = subprocess.run(["gh", "release", "create", "wedge", "--repo", repo], check=True)
    _ = subprocess.run(
        ["gh", "release", "upload", "wedge", str(foreign_asset), "--repo", repo], check=True
    )

    result = publish([skill], repo=repo, target="deadbeef")

    assert result[FIXTURE_NAME]["status"] == "failed"
    assert "built content digest" in result[FIXTURE_NAME]["reason"]
    assert _upload_count(fake_gh["store"]) == 1


def _rename_skill(skill_dir: Path, new_name: str) -> None:
    """Rename a locked ``cheese-cave`` copy's config and lock to ``new_name``."""
    config = skill_dir / "wedge.toml"
    _ = config.write_text(config.read_text().replace(f'name = "{FIXTURE_NAME}"', f'name = "{new_name}"'))
    lock_path = skill_dir / "scripts" / f"{FIXTURE_NAME}.wedge.json"
    lock_data = cast(dict[str, object], json.loads(lock_path.read_text()))
    lock_data["name"] = new_name
    digest12 = cast(str, lock_data["content_sha256"])[:12]
    lock_data["asset"] = f"{new_name}-{digest12}.pyz"
    _ = lock_path.write_text(json.dumps(lock_data))
    _ = lock_path.rename(skill_dir / "scripts" / f"{new_name}.wedge.json")


@pytest.mark.ac("AC-W6")
def test_two_skills_one_stale_the_other_still_publishes(
    tmp_path: Path,
    copy_locked_fixture: Callable[[Path], Path],
    fake_gh: FakeGh,
) -> None:
    """A reversed pairing of build outcomes and locks must not let the stale
    skill's failure silently swap with the fresh skill's success."""
    stale = copy_locked_fixture(tmp_path / "stale")
    fresh = copy_locked_fixture(tmp_path / "fresh")
    _rename_skill(stale, "cheese-cave-stale")
    source = stale.parent.parent.parent / "fromargs" / "examples" / "cheese_cave.py"
    _ = source.write_text(source.read_text() + "\n# touched\n")

    result = publish([stale, fresh], repo=fake_gh["repo"], target="deadbeef")

    assert result["cheese-cave-stale"]["status"] == "failed"
    assert "stale" in result["cheese-cave-stale"]["reason"]
    assert result[FIXTURE_NAME]["status"] == "published"

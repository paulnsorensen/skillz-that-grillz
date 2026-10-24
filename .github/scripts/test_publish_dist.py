"""The dist publisher appends one main tree plus its built archives per main commit.

Each test drives real git against a bare remote, the way the workflow runs.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from typing_extensions import override

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_dist  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/publish-dist.yml"
RETARGET = ROOT / ".github/workflows/retarget-dist-prs.yml"
ARCHIVE = "skills/demo/scripts/demo.pyz"
IDENTITY = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        env={**os.environ, **IDENTITY},
    ).stdout.strip()


class PublishDistTest(unittest.TestCase):
    tmp: Path  # pyright: ignore[reportUninitializedInstanceVariable]
    repo: Path  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tmp = Path(scratch.name)
        remote = self.tmp / "origin.git"
        _ = subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self.repo = self.tmp / "source"
        _ = subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        _ = _git(self.repo, "remote", "add", "origin", str(remote))
        skill = self.repo / "skills/demo"
        skill.mkdir(parents=True)
        _ = (skill / "SKILL.md").write_text("# demo\n")
        _ = _git(self.repo, "add", "skills")
        _ = self._commit("one")

    def _commit(self, label: str) -> str:
        _ = (self.repo / "src.txt").write_text(label)
        _ = _git(self.repo, "add", "src.txt")
        _ = _git(self.repo, "commit", "-q", "-m", label)
        _ = _git(self.repo, "push", "-q", "origin", "HEAD:main")
        _ = _git(self.repo, "fetch", "-q", "origin")
        return _git(self.repo, "rev-parse", "HEAD")

    def _stage(self, name: str, version: str) -> Path:
        root = self.tmp / name
        archive = root / ARCHIVE
        archive.parent.mkdir(parents=True)
        _ = archive.write_bytes(f"#!/usr/bin/env python3\n# {version}\n".encode())
        archive.chmod(0o755)
        return root

    def _publish(self, source: str, staged: Path) -> publish_dist.Result:
        return publish_dist.publish(
            self.repo, source=source, staged=staged, remote="origin", branch="dist", trunk="origin/main",
        )

    def _clone_dist(self) -> Path:
        """Clone dist the way `npx skills add` clones a branch: depth 1."""
        clone = self.tmp / f"clone-{len(list(self.tmp.glob('clone-*')))}"
        remote = _git(self.repo, "remote", "get-url", "origin")
        _ = subprocess.run(
            ["git", "clone", "-q", "--depth", "1", "--branch", "dist", f"file://{remote}", str(clone)], check=True,
        )
        return clone

    def _dist_tip(self) -> str:
        return _git(self.repo, "ls-remote", "origin", "refs/heads/dist").split()[0]

    def test_first_publish_holds_source_and_archives(self) -> None:
        sha = _git(self.repo, "rev-parse", "HEAD")
        result = self._publish(sha, self._stage("s1", "v1"))

        self.assertEqual(result.status, "published")
        clone = self._clone_dist()
        self.assertEqual((clone / "src.txt").read_text(), "one")
        self.assertEqual((clone / "skills/demo/SKILL.md").read_text(), "# demo\n")
        self.assertTrue((clone / ARCHIVE).read_bytes().endswith(b"# v1\n"))
        self.assertTrue((clone / ARCHIVE).stat().st_mode & 0o111, "archive lost its executable bit")
        self.assertEqual(_git(clone, "log", "-1", "--format=%(trailers:key=Source-Commit,valueonly)"), sha)

    def test_next_publish_moves_source_and_archive_in_one_fast_forward(self) -> None:
        first = self._publish(_git(self.repo, "rev-parse", "HEAD"), self._stage("s1", "v1"))
        second = self._publish(self._commit("two"), self._stage("s2", "v2"))

        self.assertEqual(second.status, "published")
        clone = self._clone_dist()
        _ = _git(clone, "fetch", "-q", "--deepen=1")
        self.assertEqual(_git(clone, "rev-parse", "HEAD^"), first.commit, "dist must fast-forward")
        changed = _git(clone, "show", "--name-only", "--format=", "HEAD").splitlines()
        self.assertEqual(sorted(changed), [ARCHIVE, "src.txt"])

    def test_republishing_the_same_source_adds_no_commit(self) -> None:
        sha = _git(self.repo, "rev-parse", "HEAD")
        first = self._publish(sha, self._stage("s1", "v1"))
        again = self._publish(sha, self._stage("s2", "v1"))

        self.assertEqual(again.status, "unchanged")
        self.assertEqual(again.commit, first.commit)

    def test_built_archives_replace_tracked_ones(self) -> None:
        """A stale tracked archive, or one for a removed target, never ships."""
        for relative, body in ((ARCHIVE, b"stale"), ("skills/gone/scripts/gone.pyz", b"gone")):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_bytes(body)
        _ = _git(self.repo, "add", "-f", "skills")
        sha = self._commit("tracked archives")

        _ = self._publish(sha, self._stage("s1", "v1"))

        clone = self._clone_dist()
        self.assertTrue((clone / ARCHIVE).read_bytes().endswith(b"# v1\n"))
        self.assertFalse((clone / "skills/gone").exists())

    def test_older_source_is_a_no_op(self) -> None:
        """A late run for an older commit succeeds without moving dist back."""
        old = _git(self.repo, "rev-parse", "HEAD")
        newest = self._publish(self._commit("two"), self._stage("s2", "v2"))

        late = self._publish(old, self._stage("s1", "v1"))

        self.assertEqual(late.status, "superseded")
        self.assertEqual(self._dist_tip(), newest.commit)

    def test_diverged_source_cannot_move_dist_back(self) -> None:
        base = _git(self.repo, "rev-parse", "HEAD")
        published = self._publish(self._commit("two"), self._stage("s1", "v1"))
        _ = _git(self.repo, "reset", "-q", "--hard", base)
        _ = (self.repo / "src.txt").write_text("rewritten")
        _ = _git(self.repo, "commit", "-q", "-am", "rewritten")
        _ = _git(self.repo, "push", "-q", "-f", "origin", "HEAD:main")
        _ = _git(self.repo, "fetch", "-q", "origin")

        with self.assertRaisesRegex(SystemExit, "refusing to move dist back"):
            _ = self._publish("HEAD", self._stage("s2", "v2"))
        self.assertEqual(self._dist_tip(), published.commit)

    def test_hand_pushed_tip_does_not_stall_publishing(self) -> None:
        """A dist commit with no Source-Commit trailer is published over, not a dead end."""
        first = self._publish(_git(self.repo, "rev-parse", "HEAD"), self._stage("s1", "v1"))
        hand = _git(self.repo, "commit-tree", f"{first.commit}^{{tree}}", "-p", first.commit, "-m", "hand")
        _ = _git(self.repo, "push", "-q", "origin", f"{hand}:refs/heads/dist")
        sha = self._commit("two")

        result = self._publish(sha, self._stage("s2", "v2"))

        self.assertEqual(result.status, "published")
        clone = self._clone_dist()
        self.assertEqual(_git(clone, "log", "-1", "--format=%(trailers:key=Source-Commit,valueonly)"), sha)
        self.assertTrue((clone / ARCHIVE).read_bytes().endswith(b"# v2\n"))

    def test_source_off_main_is_refused(self) -> None:
        _ = _git(self.repo, "switch", "-q", "-c", "feature")
        _ = (self.repo / "src.txt").write_text("unreviewed")
        _ = _git(self.repo, "commit", "-q", "-am", "unreviewed")

        with self.assertRaisesRegex(SystemExit, "is not on origin/main"):
            _ = self._publish("HEAD", self._stage("s1", "v1"))
        self.assertEqual(_git(self.repo, "ls-remote", "origin", "refs/heads/dist"), "")

    def test_stage_without_archives_is_refused(self) -> None:
        (self.tmp / "empty").mkdir()
        with self.assertRaisesRegex(SystemExit, "no staged archives"):
            _ = self._publish("HEAD", self.tmp / "empty")


class WorkflowTest(unittest.TestCase):
    def test_publish_builds_runs_then_publishes_the_main_tip(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        order = [text.index("wedge bundle skills/skillz"), text.index("--help"), text.index("publish_dist.py")]
        self.assertEqual(order, sorted(order))
        self.assertIn("ref: main", text)
        self.assertIn("--source HEAD", text)
        self.assertNotIn("GITHUB_SHA", text, "a run publishes the main tip, not its trigger commit")

    def test_publish_runs_one_at_a_time_and_reconciles_on_a_schedule(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("group: publish-dist", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("schedule:", text)

    def test_retarget_never_checks_out_pull_request_code(self) -> None:
        text = RETARGET.read_text(encoding="utf-8")
        self.assertIn("pull_request_target:", text)
        self.assertIn("--base main", text)
        self.assertNotIn("actions/checkout", text)


class MainTreeTest(unittest.TestCase):
    def test_main_tracks_no_archives(self) -> None:
        """Archives live only on dist; a branch cut from dist must not merge them back into main."""
        tracked = _git(ROOT, "ls-files", "--", publish_dist.ARCHIVES)
        self.assertEqual(tracked, "", "main must not track archives; branch from main, not dist, and drop: " + tracked)


if __name__ == "__main__":
    _ = unittest.main()

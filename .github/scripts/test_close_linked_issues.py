"""Test the close-linked-issues workflow and script against push payloads."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict, cast

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import close_linked_issues  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "workflows" / "close-linked-issues.yml"
SCRIPT = Path(close_linked_issues.__file__).resolve()
REPO = "paulnsorensen/skillz-that-grillz"

GH_SHIM = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state = Path(os.environ["GH_SHIM_STATE"])
items = json.loads(state.read_text())
if not items:
    sys.exit(f"unexpected extra gh call: {sys.argv[1:]}")
entry = items.pop(0)
state.write_text(json.dumps(items))
if sys.argv[1:] != entry["argv"]:
    sys.exit(f"unexpected gh call: {sys.argv[1:]} != {entry['argv']}")
print(entry.get("stdout", ""), end="")
if entry.get("stderr"):
    print(entry["stderr"], file=sys.stderr, end="")
sys.exit(entry.get("exit", 0))
"""


class GhCall(TypedDict):
    argv: list[str]
    stdout: str
    stderr: str
    exit: int


def gh_call(argv: list[str], stdout: str = "", stderr: str = "", exit_code: int = 0) -> GhCall:
    return {"argv": argv, "stdout": stdout, "stderr": stderr, "exit": exit_code}


def graphql_call(pr_number: int, nodes: list[dict[str, object]]) -> GhCall:
    owner, name = REPO.split("/", 1)
    payload = {"data": {"repository": {"pullRequest": {"closingIssuesReferences": {"nodes": nodes}}}}}
    return gh_call(
        [
            "api", "graphql",
            "-f", f"query={close_linked_issues.LINKED_ISSUES_QUERY}",
            "-f", f"owner={owner}",
            "-f", f"name={name}",
            "-F", f"number={pr_number}",
        ],
        stdout=json.dumps(payload),
    )


class CloseLinkedIssuesParserTest(unittest.TestCase):
    def test_extracts_all_github_closing_keywords_and_formats(self) -> None:
        text = "\n".join([
            "Close #1, closes #2, closed #3.",
            "Fix: #4, FIXES:#5, fixed #6.",
            "Resolve #7, resolves paulnsorensen/skillz-that-grillz#8.",
            "Resolved https://github.com/paulnsorensen/skillz-that-grillz/issues/9.",
            "Fixes https://github.com/PAULNSORENSEN/SKILLZ-THAT-GRILLZ/issues/10#issuecomment-5",
            "Closes #1 again (deduplicated).",
        ])
        self.assertEqual(close_linked_issues.extract_closing_issues(text, REPO), list(range(1, 11)))

    def test_ignores_code_comments_coderabbit_non_closing_and_other_repos(self) -> None:
        text = "\n".join([
            "Ref #101, Part of #102, Related to #103, fixes #104, #105.",
            "Fixes other-org/other-repo#106.",
            "Fixes https://github.com/other-org/other-repo/issues/107.",
            "Fixes https://github.com/paulnsorensen/skillz-that-grillz/issues/108/events.",
            "Inline `Fixes #109` and ``Closes #110`` stay ignored.",
            "<!-- Fixes #111 -->",
            "<!-- This is an auto-generated comment: release notes by coderabbit.ai -->",
            "## Summary by CodeRabbit",
            "* Fixes #112",
            "<!-- end of auto-generated comment: release notes by coderabbit.ai -->",
            "```markdown",
            "Fixes #113",
            "```",
            "~~~text",
            "Resolves #114",
            "~~~",
            "Unfixed #115 and prefix-fix #116.",
        ])
        self.assertEqual(close_linked_issues.extract_closing_issues(text, REPO), [104])


class CloseLinkedIssuesWorkflowTest(unittest.TestCase):
    def test_workflow_targets_main_with_minimal_permissions_and_pinned_checkout(self) -> None:
        workflow = cast(dict[str | bool, object], yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))
        triggers = cast(dict[str, object], workflow.get("on", workflow.get(True)))
        self.assertEqual(triggers, {"push": {"branches": ["main"]}})
        self.assertEqual(workflow["permissions"], {})
        concurrency = cast(dict[str, object], workflow["concurrency"])
        self.assertEqual(concurrency["group"], "close-linked-issues-${{ github.sha }}")
        self.assertFalse(concurrency["cancel-in-progress"])
        jobs = cast(dict[str, dict[str, object]], workflow["jobs"])
        self.assertEqual(set(jobs), {"close-issues"})
        job = jobs["close-issues"]
        self.assertEqual(job["permissions"], {"contents": "read", "issues": "write", "pull-requests": "read"})
        steps = cast(list[dict[str, object]], job["steps"])
        self.assertEqual(steps[0]["uses"], "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1")
        self.assertEqual(steps[1]["run"], "python3 .github/scripts/close_linked_issues.py")

    def _run_script(
        self, event: dict[str, object], calls: list[GhCall],
    ) -> tuple[subprocess.CompletedProcess[str], list[GhCall]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / "gh"
            _ = shim.write_text(GH_SHIM, encoding="utf-8")
            shim.chmod(0o755)
            state = root / "calls.json"
            _ = state.write_text(json.dumps(calls), encoding="utf-8")
            event_path = root / "event.json"
            _ = event_path.write_text(json.dumps(event), encoding="utf-8")
            env = os.environ | {
                "PATH": f"{root}:{os.environ['PATH']}",
                "GH_SHIM_STATE": str(state),
                "GH_TOKEN": "test-token",
                "GH_REPO": REPO,
                "GITHUB_EVENT_PATH": str(event_path),
            }
            result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, check=False)
            remaining = cast(list[GhCall], json.loads(state.read_text(encoding="utf-8")))
            return result, remaining

    def test_mergify_squash_merge_closes_keyword_and_sidebar_issues(self) -> None:
        sha = "49a9c54300000000000000000000000000000000"
        event: dict[str, object] = {
            "ref": "refs/heads/main",
            "commits": [{"id": sha, "message": "feat(skillz)!: baseline first (#758)\n\nTrimmed squash body."}],
        }
        prs = [
            # Mergify temporary queue draft PR must be ignored even if returned.
            {
                "number": 999,
                "base": {"ref": "main"},
                "merged_at": "2026-11-28T10:09:00Z",
                "user": {"login": "mergify[bot]"},
                "head": {"ref": "mergify/merge-queue/abc"},
                "body": "Fixes #888",
            },
            # Real merged PR targeting main.
            {
                "number": 758,
                "base": {"ref": "main"},
                "merged_at": "2026-11-28T10:09:59Z",
                "user": {"login": "paulnsorensen"},
                "head": {"ref": "t3/skillz-baseline-first-756"},
                "body": "Fixes #756. Also fixes #758 (self).",
            },
        ]
        sidebar: list[dict[str, object]] = [{"number": 760, "state": "OPEN", "repository": {"nameWithOwner": REPO}}]
        result, remaining = self._run_script(event, [
            gh_call(["api", f"repos/{REPO}/commits/{sha}/pulls"], stdout=json.dumps(prs)),
            graphql_call(758, sidebar),
            gh_call(["api", f"repos/{REPO}/issues/756"], stdout=json.dumps({"number": 756, "state": "open"})),
            gh_call(["api", "--method", "PATCH", f"repos/{REPO}/issues/756", "-f", "state=closed", "-f", "state_reason=completed"]),
            gh_call(["api", "--method", "POST", f"repos/{REPO}/issues/756/comments",
                     "-f", "body=Closed by #758 (`49a9c543`) merged into `main`."]),
            gh_call(["api", f"repos/{REPO}/issues/760"], stdout=json.dumps({"number": 760, "state": "open"})),
            gh_call(["api", "--method", "PATCH", f"repos/{REPO}/issues/760", "-f", "state=closed", "-f", "state_reason=completed"]),
            gh_call(["api", "--method", "POST", f"repos/{REPO}/issues/760/comments",
                     "-f", "body=Closed by #758 (`49a9c543`) merged into `main`."]),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(remaining, [])

    def test_skips_closed_issues_pull_requests_missing_issues_and_reverts(self) -> None:
        sha1 = "1111111100000000000000000000000000000000"
        sha2 = "2222222200000000000000000000000000000000"
        event: dict[str, object] = {
            "ref": "refs/heads/main",
            "commits": [
                {"id": sha1, "message": "fix: direct push\n\nFixes #10, closes #11, resolves #12, fixes #13."},
                {"id": sha2, "message": 'Revert "feat: something (#99)"\n\nFixes #14.'},
            ],
        }
        result, remaining = self._run_script(event, [
            gh_call(["api", f"repos/{REPO}/commits/{sha1}/pulls"], stdout="[]"),
            gh_call(["api", f"repos/{REPO}/commits/{sha2}/pulls"], stdout="[]"),
            # #10 is already closed.
            gh_call(["api", f"repos/{REPO}/issues/10"], stdout=json.dumps({"number": 10, "state": "closed"})),
            # #11 is a pull request, not an issue.
            gh_call(["api", f"repos/{REPO}/issues/11"], stdout=json.dumps({"number": 11, "state": "open", "pull_request": {}})),
            # #12 does not exist (404).
            gh_call(["api", f"repos/{REPO}/issues/12"], stderr="gh: Not Found (HTTP 404)\n", exit_code=1),
            # #13 is open and gets closed with commit attribution.
            gh_call(["api", f"repos/{REPO}/issues/13"], stdout=json.dumps({"number": 13, "state": "open"})),
            gh_call(["api", "--method", "PATCH", f"repos/{REPO}/issues/13", "-f", "state=closed", "-f", "state_reason=completed"]),
            gh_call(["api", "--method", "POST", f"repos/{REPO}/issues/13/comments",
                     "-f", "body=Closed by commit `11111111` pushed to `main`."]),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("issue #12: not found, skipping", result.stderr)
        self.assertEqual(remaining, [])

    def test_ignores_non_main_ref_without_api_calls(self) -> None:
        event: dict[str, object] = {
            "ref": "refs/heads/dist",
            "commits": [{"id": "abcdef1200000000", "message": "dist: main@49a9c543\n\nFixes #756"}],
        }
        result, remaining = self._run_script(event, [])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(remaining, [])

    def test_continues_after_single_issue_error_and_exits_nonzero(self) -> None:
        sha = "3333333300000000000000000000000000000000"
        event: dict[str, object] = {
            "ref": "refs/heads/main",
            "commits": [{"id": sha, "message": "fix: two issues\n\nFixes #20 and fixes #21."}],
        }
        result, remaining = self._run_script(event, [
            gh_call(["api", f"repos/{REPO}/commits/{sha}/pulls"], stdout="[]"),
            gh_call(["api", f"repos/{REPO}/issues/20"], stdout=json.dumps({"number": 20, "state": "open"})),
            gh_call(["api", "--method", "PATCH", f"repos/{REPO}/issues/20", "-f", "state=closed", "-f", "state_reason=completed"],
                    stderr="HTTP 500\n", exit_code=1),
            gh_call(["api", f"repos/{REPO}/issues/21"], stdout=json.dumps({"number": 21, "state": "open"})),
            gh_call(["api", "--method", "PATCH", f"repos/{REPO}/issues/21", "-f", "state=closed", "-f", "state_reason=completed"]),
            gh_call(["api", "--method", "POST", f"repos/{REPO}/issues/21/comments",
                     "-f", "body=Closed by commit `33333333` pushed to `main`."]),
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn("issue #20: close failed", result.stderr)
        self.assertEqual(remaining, [])


if __name__ == "__main__":
    _ = unittest.main()

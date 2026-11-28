#!/usr/bin/env python3
"""Close same-repo issues linked to commits or merged PRs pushed to `main`.

GitHub auto-closes issues only on merges into the default branch (`dist`).
This script runs on pushes to `main` (including Mergify squash merges) and
closes open same-repo issues referenced by GitHub closing keywords or
manually linked in the pull request Development sidebar.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

CODERABBIT_BLOCK = re.compile(
    r"<!--\s*This is an auto-generated comment:\s*release notes by coderabbit\.ai\s*-->"
    + r".*?"
    + r"<!--\s*end of auto-generated comment:\s*release notes by coderabbit\.ai\s*-->",
    re.DOTALL | re.IGNORECASE,
)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
CLOSING_KEYWORD = re.compile(
    r"(?<![A-Za-z0-9_-])"
    + r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"
    + r"(?::[ \t]*|[ \t]+)"
    + r"(?:"
    + r"#(?P<local>\d+)"
    + r"|(?P<full_repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(?P<full_num>\d+)"
    + r"|https://github\.com/(?P<url_repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(?P<url_num>\d+)(?![A-Za-z0-9_/])"
    + r")"
    + r"(?![0-9A-Za-z_])",
    re.IGNORECASE,
)
LINKED_ISSUES_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      closingIssuesReferences(first: 50) {
        nodes {
          number
          state
          repository { nameWithOwner }
        }
      }
    }
  }
}
""".strip()


class GhError(RuntimeError):
    """Raised when a `gh api` request fails."""


def _strip_fenced_code(text: str) -> str:
    kept: list[str] = []
    fence_char = ""
    fence_len = 0
    for line in text.splitlines(keepends=True):
        if fence_char:
            stripped = line.lstrip(" \t")
            closing = stripped.rstrip("\r\n \t")
            if len(closing) >= fence_len and set(closing) == {fence_char}:
                fence_char = ""
                fence_len = 0
            continue
        match = FENCE_OPEN.match(line)
        if match:
            fence = match.group(1)
            fence_char = fence[0]
            fence_len = len(fence)
            continue
        kept.append(line)
    return "".join(kept)


def _strip_inline_code(text: str) -> str:
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "`":
            out.append(text[index])
            index += 1
            continue
        run_end = index
        while run_end < length and text[run_end] == "`":
            run_end += 1
        ticks = text[index:run_end]
        close = text.find(ticks, run_end)
        if close == -1:
            out.append(ticks)
            index = run_end
        else:
            out.append(" ")
            index = close + len(ticks)
    return "".join(out)


def strip_ignored_markdown(text: str) -> str:
    """Remove CodeRabbit auto-blocks, HTML comments, fenced code, and inline code."""
    cleaned = CODERABBIT_BLOCK.sub(" ", text)
    cleaned = HTML_COMMENT.sub(" ", cleaned)
    cleaned = _strip_fenced_code(cleaned)
    return _strip_inline_code(cleaned)


def extract_closing_issues(text: str, repo: str) -> list[int]:
    """Return unique same-repo issue numbers referenced by GitHub closing keywords."""
    cleaned = strip_ignored_markdown(text)
    target = repo.lower()
    found: dict[int, None] = {}
    for match in CLOSING_KEYWORD.finditer(cleaned):
        local = match.group("local")
        if local is not None:
            found[int(local)] = None
            continue
        full_repo = match.group("full_repo")
        full_num = match.group("full_num")
        if full_repo is not None and full_num is not None:
            if full_repo.lower() == target:
                found[int(full_num)] = None
            continue
        url_repo = match.group("url_repo")
        url_num = match.group("url_num")
        if url_repo is not None and url_num is not None and url_repo.lower() == target:
            found[int(url_num)] = None
    return list(found)


def is_revert_commit(message: str) -> bool:
    """Return True when the commit subject indicates a revert commit."""
    first_line = message.splitlines()[0] if message else ""
    return first_line.startswith('Revert "')


def is_mergify_queue_pr(pr: dict[str, object]) -> bool:
    """Return True for Mergify's temporary merge-queue draft pull requests."""
    user = pr.get("user")
    head = pr.get("head")
    login = cast(dict[str, object], user).get("login") if isinstance(user, dict) else None
    ref = cast(dict[str, object], head).get("ref") if isinstance(head, dict) else None
    return login == "mergify[bot]" and isinstance(ref, str) and ref.startswith("mergify/merge-queue/")


def _as_dict(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


class GhClient:
    """Minimal wrapper around `gh api` for issue closing."""

    def __init__(self, repo: str) -> None:
        self.repo: str = repo
        self.owner: str
        self.name: str
        self.owner, self.name = repo.split("/", 1)

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["gh", "api", *args], capture_output=True, text=True, check=False)

    def _json(self, *args: str) -> object:
        result = self._run(*args)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise GhError(f"gh api {' '.join(args)} failed: {detail}")
        return cast(object, json.loads(result.stdout))

    def associated_prs(self, sha: str) -> list[dict[str, object]]:
        """Return merged pull requests targeting `main` for `sha`."""
        data = self._json(f"repos/{self.repo}/commits/{sha}/pulls")
        merged: list[dict[str, object]] = []
        for item in _as_list(data):
            pr = _as_dict(item)
            base = _as_dict(pr.get("base"))
            if base.get("ref") != "main" or not pr.get("merged_at") or is_mergify_queue_pr(pr):
                continue
            merged.append(pr)
        return merged

    def manually_linked_issues(self, pr_number: int) -> list[int]:
        """Return open same-repo issues linked in the PR Development sidebar."""
        data = self._json(
            "graphql",
            "-f", f"query={LINKED_ISSUES_QUERY}",
            "-f", f"owner={self.owner}",
            "-f", f"name={self.name}",
            "-F", f"number={pr_number}",
        )
        root = _as_dict(data)
        pr_node = _as_dict(_as_dict(_as_dict(root.get("data")).get("repository")).get("pullRequest"))
        nodes = _as_list(_as_dict(pr_node.get("closingIssuesReferences")).get("nodes"))
        issues: list[int] = []
        for raw in nodes:
            node = _as_dict(raw)
            node_repo = _as_dict(node.get("repository")).get("nameWithOwner")
            number = node.get("number")
            if (
                node.get("state") == "OPEN"
                and isinstance(node_repo, str)
                and node_repo.lower() == self.repo.lower()
                and type(number) is int
            ):
                issues.append(number)
        return issues

    def fetch_issue(self, number: int) -> dict[str, object] | None:
        """Fetch an issue by number, or return None on HTTP 404."""
        result = self._run(f"repos/{self.repo}/issues/{number}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            if "HTTP 404" in detail or "Not Found" in detail:
                return None
            raise GhError(f"issue #{number}: lookup failed: {detail}")
        return _as_dict(cast(object, json.loads(result.stdout)))

    def close_issue(self, number: int, comment: str) -> None:
        """Close an issue as completed and add a linking comment."""
        close_res = self._run(
            "--method", "PATCH",
            f"repos/{self.repo}/issues/{number}",
            "-f", "state=closed",
            "-f", "state_reason=completed",
        )
        if close_res.returncode != 0:
            detail = (close_res.stderr or close_res.stdout).strip()
            raise GhError(f"issue #{number}: close failed: {detail}")
        comment_res = self._run(
            "--method", "POST",
            f"repos/{self.repo}/issues/{number}/comments",
            "-f", f"body={comment}",
        )
        if comment_res.returncode != 0:
            detail = (comment_res.stderr or comment_res.stdout).strip()
            raise GhError(f"issue #{number}: comment failed: {detail}")


def _push_commits(event: dict[str, object]) -> list[tuple[str, str]]:
    raw_commits = _as_list(event.get("commits"))
    head_commit = _as_dict(event.get("head_commit"))
    commits: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in raw_commits:
        item = _as_dict(raw)
        sha = item.get("id")
        message = item.get("message")
        if isinstance(sha, str) and sha and sha not in seen:
            seen.add(sha)
            commits.append((sha, message if isinstance(message, str) else ""))
    head_sha = head_commit.get("id")
    if isinstance(head_sha, str) and head_sha and head_sha not in seen:
        head_msg = head_commit.get("message")
        commits.append((head_sha, head_msg if isinstance(head_msg, str) else ""))
    return commits


def collect_targets(
    event: dict[str, object], repo: str, client: GhClient,
) -> tuple[dict[int, tuple[int | None, str]], list[str]]:
    """Collect issue numbers to close and any lookup errors from a push event."""
    if event.get("ref") != "refs/heads/main":
        return {}, []
    targets: dict[int, tuple[int | None, str]] = {}
    errors: list[str] = []
    seen_prs: set[int] = set()
    for sha, message in _push_commits(event):
        short_sha = sha[:8]
        prs: list[dict[str, object]] = []
        try:
            prs = client.associated_prs(sha)
        except GhError as error:
            errors.append(str(error))
        pr_numbers: list[int] = []
        for pr in prs:
            number = pr.get("number")
            if type(number) is not int:
                continue
            pr_numbers.append(number)
            if number in seen_prs:
                continue
            seen_prs.add(number)
            body = pr.get("body")
            if isinstance(body, str):
                for issue_num in extract_closing_issues(body, repo):
                    if issue_num != number:
                        _ = targets.setdefault(issue_num, (number, short_sha))
            try:
                for issue_num in client.manually_linked_issues(number):
                    if issue_num != number:
                        _ = targets.setdefault(issue_num, (number, short_sha))
            except GhError as error:
                errors.append(str(error))
        if not is_revert_commit(message):
            attributed_pr = pr_numbers[0] if len(pr_numbers) == 1 else None
            for issue_num in extract_closing_issues(message, repo):
                if issue_num != attributed_pr:
                    _ = targets.setdefault(issue_num, (attributed_pr, short_sha))
    return targets, errors


def process_push(event: dict[str, object], repo: str, client: GhClient) -> int:
    """Close each open same-repo issue linked to a push on `main`. Return 0 or 1."""
    targets, errors = collect_targets(event, repo, client)
    failed = 1 if errors else 0
    for message in errors:
        print(message, file=sys.stderr)
    for number, (pr_number, short_sha) in sorted(targets.items()):
        try:
            issue = client.fetch_issue(number)
        except GhError as error:
            print(error, file=sys.stderr)
            failed = 1
            continue
        if issue is None:
            print(f"issue #{number}: not found, skipping", file=sys.stderr)
            continue
        if "pull_request" in issue:
            continue
        if issue.get("state") != "open":
            continue
        comment = (
            f"Closed by #{pr_number} (`{short_sha}`) merged into `main`."
            if pr_number is not None
            else f"Closed by commit `{short_sha}` pushed to `main`."
        )
        try:
            client.close_issue(number, comment)
            print(f"closed #{number}: {comment}")
        except GhError as error:
            print(error, file=sys.stderr)
            failed = 1
    return failed


def main() -> int:
    repo = os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not repo or "/" not in repo:
        print("close_linked_issues: GH_REPO or GITHUB_REPOSITORY must be owner/repo", file=sys.stderr)
        return 1
    if not event_path:
        print("close_linked_issues: GITHUB_EVENT_PATH is required", file=sys.stderr)
        return 1
    event = _as_dict(cast(object, json.loads(Path(event_path).read_text(encoding="utf-8"))))
    return process_push(event, repo, GhClient(repo))


if __name__ == "__main__":
    sys.exit(main())

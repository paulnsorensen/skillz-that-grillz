---
name: gh
model: haiku
context: fork
allowed-tools: mcp__plugin_github_github__*, Bash(git:*), Bash(gh:*)
description: >
  Complete GitHub tasks using the GitHub MCP plugin. Use for any GitHub operation —
  PRs, issues, CI checks, repo management, releases, code search.
  Use git commands (log, diff, status) for local context.
  Prefer MCP tools over gh CLI — they bypass sandbox/TLS issues.
  Use when the user says "create PR", "merge PR", "check CI", "list issues", "review PR",
  "PR status", "close issue", or invokes /gh. Do NOT use for local git operations like
  commit, stage, or push — use /commit for those. Do NOT use for code quality
  review — use a dedicated review skill or tool.
license: MIT
---

# gh

GitHub operations via **GitHub MCP plugin** (`mcp__plugin_github_github__*`). MCP is the default — it works reliably in sandbox with no TLS issues.

**Default**: GitHub MCP tools for all supported operations.
**Fallback**: `gh` CLI only for operations MCP doesn't cover (see table below).
**Rule**: `git` is read-only here — log, diff, status. No commits, no push via git.

---

## MCP Tool Reference

For the full MCP tool catalog (PRs, issues, repos, releases, Copilot), read
`references/github-mcp.md`. Key tools for common operations:

- **PRs**: `create_pull_request`, `pull_request_read`, `merge_pull_request`, `add_reply_to_pull_request_comment`
- **Issues**: `issue_read`, `issue_write`, `add_issue_comment`, `list_issues`
- **Code**: `search_code`, `get_file_contents`, `push_files`
- **Copilot**: `assign_copilot_to_issue`, `request_copilot_review`

---

## CLI Rules

**Never pipe `gh` output.** The `gh` CLI has `--json`, `--jq`, and `--template` flags built in:

```bash
# WRONG — pipe triggers compound command detection + needs jq binary
gh pr list --json number | jq '.[].number'

# RIGHT — inline jq, no pipe, embedded interpreter
gh pr list --json number --jq '.[].number'

# Complex filtering
gh pr list --json number,title,state --jq '.[] | select(.state == "OPEN") | .title'

# Go template alternative
gh pr view 42 --json title --template '{{.title}}'
```

**Never use heredoc `--body` with `gh pr create`.** The `$(cat <<'EOF' ... EOF)` pattern triggers Claude Code's "hides arguments" heuristic when the body contains `#`-prefixed lines (markdown headers).

Instead:

1. **MCP** (preferred): `create_pull_request` — no shell involved
2. **`--body-file`** (CLI fallback): Write body with the Write tool to `$TMPDIR/pr-body.md`, then `gh pr create --title "..." --body-file "$TMPDIR/pr-body.md"`

**Prefer MCP over `gh api`.** Raw API calls can hit TLS issues in sandboxed environments. Most `gh api` calls have an MCP equivalent:

```bash
# PREFER — MCP tool, runs in host process, no sandbox issues
pull_request_read(method: "get_review_comments", owner, repo, pullNumber: 78)

# FALLBACK — gh api, only when MCP doesn't cover the endpoint
gh api repos/owner/repo/actions/runs/123/logs
```

---

## CLI Fallback (only when MCP can't do it)

These operations have no MCP equivalent — use `gh` CLI:

| Operation | Command |
|-----------|---------|
| PR diff | `gh pr diff <number>` |
| PR checks / CI status | `gh pr checks <number>` |
| Run logs | `gh run view <id> --log-failed` |
| Watch a run | `gh run watch <id>` |
| Trigger workflow | `gh workflow run <workflow>` |
| Create release | `gh release create <tag>` |
| Delete release | `gh release delete <tag>` |
| Re-run failed CI | `gh run rerun <id> --failed` |

---

## Git Context (read-only)

Before creating PRs or writing descriptions, use git for local context:

```bash
git log --oneline origin/main..HEAD   # commits going into the PR
git diff origin/main...HEAD           # full diff for PR body
git status                            # working tree state
```

## What You Don't Do

- Commit, push, rebase, or modify the local working tree — use /commit for git write operations
- Review code quality — out of scope for this skill
- Create worktrees — out of scope for this skill

## Gotchas

- MCP auth tokens can expire mid-session — if MCP calls start failing, restart Claude Code
- `gh` CLI has TLS issues in sandboxed environments — prefer MCP tools
- Heredoc `--body` with markdown headers triggers the "# hides arguments" safety heuristic — use `--body-file` or MCP instead
- Rate limits hit harder on large repos with many PRs — batch operations where possible

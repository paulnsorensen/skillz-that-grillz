---
name: gh
model: haiku
description: >
  Complete GitHub tasks using the `gh` CLI — pull requests, issues, CI checks,
  releases, workflow runs, code search, repo and label management.
  Use when the user says "create PR", "merge PR", "check CI", "list issues",
  "review PR", "PR status", "close issue", "trigger workflow", "view release",
  "search repos", or invokes /gh. Use `git` (log, diff, status) for read-only
  local context only. Do NOT use for committing, staging, or pushing — use
  /commit for those. Do NOT use for code-quality review — use a dedicated
  review skill.
license: MIT
---

# gh

GitHub operations via the [`gh`](https://cli.github.com) CLI. Wraps every
common operation — PRs, issues, releases, workflow runs, repos, search — in
idiomatic, token-efficient invocations.

`git` is read-only here — log, diff, status. No commits, no pushes through
this skill. Pair with `/commit` for git writes.

---

## CLI rules

**Don't pipe `gh` into a separate `jq` binary.** `gh` ships with `--jq`
and `--template` flags; inlining them avoids spawning `jq` and triggering
any compound-command sandbox heuristics some harnesses apply. Piping
`gh ... --jq` into downstream consumers like `xargs` or `sed` is fine —
the goal is to keep JSON extraction inside `gh` itself:

```bash
# wrong — needs jq binary, triggers a compound command
gh pr list --json number | jq '.[].number'

# right — inline jq, no pipe
gh pr list --json number --jq '.[].number'

# also fine — --jq inlined, then piped to xargs for a bulk op
gh pr list --json number --jq '.[].number' | xargs -I{} gh pr view {}

# Go template alternative
gh pr view 42 --json title --template '{{.title}}'
```

**Never use heredoc `--body` for PR or issue creation.** The
`$(cat <<'EOF' ... EOF)` pattern can trip "# hides arguments" sandbox
heuristics when the body contains markdown headers. Write the body to a
file and pass `--body-file`:

```bash
gh pr create --title "feat(api): add health endpoint" \
  --body-file "$TMPDIR/pr-body.md" \
  --base main --head feature/health
```

**Always check JSON field names with `--help`.** They differ from the GitHub
REST API names:

```bash
gh pr list --help | sed -n '/JSON FIELDS/,/^$/p'
```

Common gotchas: `stargazerCount` (not `stargazersCount`), `forkCount`
(not `forksCount`), `watchers` (not `watchersCount`).

---

## Pull requests

```bash
# Create
gh pr create --title "..." --body-file body.md --base main --head feature
gh pr create --fill                              # title/body from commits
gh pr create --draft
gh pr create --reviewer @copilot                 # request Copilot review

# View
gh pr list                                       # repo PRs
gh pr list --author @me                          # mine
gh pr list --search "is:open label:bug"          # full search syntax
gh pr view 123
gh pr view 123 --web
gh pr diff 123
gh pr diff 123 --exclude '*.lock'                # skip lockfile noise

# Status & checks
gh pr status                                     # PRs touching you
gh pr checks 123                                 # CI checks for the PR
gh pr checks 123 --watch                         # block until done

# Review & merge
gh pr review 123 --approve --body "LGTM"
gh pr review 123 --request-changes --body-file review.md
gh pr merge 123 --squash --delete-branch
gh pr merge 123 --auto --squash                  # auto-merge when checks pass
gh pr update-branch 123                          # bring up to date with base

# Lifecycle
gh pr close 123
gh pr reopen 123
gh pr ready 123                                  # un-draft
gh pr revert 123                                 # creates new revert PR
gh pr checkout 123                               # check out PR branch locally
```

For `--jq` filter patterns and bulk operations, see
[`references/jq-recipes.md`](references/jq-recipes.md).
For end-to-end PR / release / CI scripts, see
[`references/automation.md`](references/automation.md).

---

## Issues

```bash
gh issue create --title "..." --body-file body.md --label bug
gh issue create --title "..." --assignee @me

gh issue list                                    # repo issues
gh issue list --label bug --state open
gh issue list --search "is:open label:bug sort:created-desc"

gh issue view 456
gh issue view 456 --web

gh issue edit 456 --add-label needs-triage
gh issue edit 456 --add-assignee @user
gh issue comment 456 --body-file comment.md

gh issue close 456
gh issue close 456 --duplicate-of 123
gh issue reopen 456
gh issue develop 456 --checkout                  # branch off the issue
```

---

## CI / workflows / runs

```bash
gh workflow list
gh workflow run ci.yml --ref feature-branch
gh workflow run deploy.yml -f environment=production -f version=v1.2.3

gh run list                                      # all recent runs
gh run list --workflow=ci.yml --status=failure
gh run list --branch=main --limit 10

gh run view 789
gh run view 789 --log                            # full logs
gh run view 789 --log-failed                     # only failed jobs
gh run view 789 --web

gh run watch 789                                 # block until run completes
gh run rerun 789 --failed                        # re-run failed jobs only
gh run cancel 789

# Inspect a run programmatically
gh run view 789 --json status,conclusion,jobs \
  --jq '.jobs[] | select(.conclusion=="failure") | .name'
```

---

## Releases

```bash
gh release create v1.0.0                         # interactive
gh release create v1.0.0 --notes-file NOTES.md
gh release create v1.0.0 --generate-notes        # auto-generate from PRs
gh release create v1.0.0 --draft
gh release create v1.0.0 dist/*.tar.gz           # upload assets at create time
gh release upload v1.0.0 dist/*.tar.gz           # add assets later

gh release list
gh release view v1.0.0
gh release download v1.0.0
gh release verify v1.0.0                         # supply-chain attestation
```

---

## Repos & search

```bash
gh repo view                                     # current repo
gh repo view owner/repo --json description,stargazerCount,defaultBranchRef
gh repo clone owner/repo
gh repo fork owner/repo --remote
gh repo set-default                              # disambiguate origin

gh search repos "machine learning" --language=python --stars=">1000"
gh search code "TODO" --owner=myorg --language=rust
gh search issues "memory leak" --state=open
gh search prs "refactor" --created=">2024-01-01"
```

For label / codespace / gist / secret operations, see
[`references/extras.md`](references/extras.md).

---

## Auth

```bash
gh auth login                                    # interactive web login
gh auth login --clipboard                        # auto-copy OAuth code
gh auth status                                   # current identity
gh auth refresh -h github.com -s repo,workflow   # add scopes
gh auth setup-git                                # use gh as git credential helper
```

Common scopes: `repo` (private repo write), `workflow` (Actions),
`admin:org` (org admin), `write:packages` (registry).

If a call returns `HTTP 401`, run `gh auth refresh`. If it returns
`HTTP 403 Resource not accessible by personal access token`, scopes are
missing — re-run `gh auth refresh -s <scope>`. For deeper diagnosis see
[`references/troubleshooting.md`](references/troubleshooting.md).

---

## Local git context (read-only)

Before drafting a PR description, gather diff context with git directly:

```bash
git log --oneline origin/main..HEAD              # commits going into the PR
git diff origin/main...HEAD                      # full diff
git status                                       # working tree state
```

Hand off any push or commit work to `/commit`.

---

## What you don't do

- Stage, commit, push, rebase, or otherwise mutate the working tree —
  that's `/commit`'s job
- Code-quality review — use a dedicated review skill
- Worktree creation — out of scope
- **Destructive operations.** `gh repo delete`, `gh release delete`,
  `gh secret delete`, `gh ssh-key delete`, `gh codespace delete`, and
  similar irreversible commands stay outside this skill. Run them only
  with explicit user confirmation; never bake them into automation.

## Gotchas

- **Compound `cd <dir> && git ...`** can be blocked by a harness's
  bare-repo sandbox heuristic. Use git's `-C <dir>` flag instead, or run
  from the worktree root.
- **Heredoc `--body` with markdown headers** trips the
  "# hides arguments" guard — always use `--body-file`.
- **`gh api` raw calls** are flakier than the named subcommands and
  rarely needed. Prefer `gh pr ...`, `gh issue ...`, `gh run ...` over
  `gh api` whenever a subcommand exists; reach for `gh api` only for
  endpoints that have no subcommand wrapper.
- **Rate limits**: `gh api rate_limit` shows current quota.
  Authenticated requests get 5000/hr, unauthenticated 60/hr.
- **Default repo ambiguity**: when a clone has multiple remotes, run
  `gh repo set-default` once or pass `--repo owner/name` per call.

## See also

- [`references/jq-recipes.md`](references/jq-recipes.md) — token-efficient
  `--jq` patterns for PR / run / issue queries
- [`references/automation.md`](references/automation.md) — release flow,
  CI monitor, auto-PR, bulk operations
- [`references/extras.md`](references/extras.md) — labels, codespaces,
  gists, secrets/variables, projects, aliases
- [`references/troubleshooting.md`](references/troubleshooting.md) — auth,
  permissions, rate limits, common error codes
- Official manual: <https://cli.github.com/manual>

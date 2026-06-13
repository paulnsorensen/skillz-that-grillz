---
allowed-tools: Read, Write, Edit, Grep, Glob, Bash(gh:*), Bash(git:*), Bash(sleep:*)
license: MIT
name: copilot
description: >
  Use when the user says "copilot review", "review with copilot", "delegate
  to copilot", "have copilot fix this", "copilot agent task", "set up
  copilot", "generate copilot instructions", "bootstrap copilot for this
  repo", or invokes `/copilot`. Three modes: `review` (run a code review
  on a PR and route fixes to Copilot via inline `@copilot fix this`
  comments), `delegate` (create a `gh agent-task`, poll until the coding
  agent opens a PR, optionally hand off to review), and `setup` (one-time
  bootstrap that writes `.github/copilot-instructions.md` and the per-role
  `.github/instructions/*.instructions.md` files). `review` and `delegate`
  are routine; `setup` is a one-time repo bootstrap loaded on demand from
  `references/setup.md` only when the user explicitly asks. Do NOT use for
  plain GitHub PR work — that is `/gh`'s job.
---

# copilot

Drive the GitHub Copilot CLI and coding agent. Three modes:

| Mode | Invocation | Purpose |
| --- | --- | --- |
| `review` | `/copilot review <pr>` | Review a PR, route fixes to Copilot via inline comments. |
| `delegate` | `/copilot delegate <task>` | Create a `gh agent-task`, monitor until it produces a PR. |
| `setup` | `/copilot setup` | One-time bootstrap of `.github/copilot-instructions.md` and friends. |

Routine work is `review` and `delegate`. Read
[references/setup.md](references/setup.md) **only** when the user
explicitly says "copilot setup", "generate copilot instructions", or
"bootstrap copilot for this repo" — never for routine review/delegate
work, since it adds ~12 KB of repo-bootstrap content irrelevant to those
modes.

## Mode selection

Parse the first positional argument as the mode. If no mode is given, ask
the user which mode they want via your harness's interactive-prompt
mechanism; if none, ask in plain text and stop for the answer. Never
silently default — each mode has very different side effects.

| First arg | Mode |
| --- | --- |
| `review` (with optional PR number / URL after) | `review` |
| `delegate` followed by a task description | `delegate` |
| `setup` | `setup` (load `references/setup.md`) |
| Bare PR URL (`https://github.com/...`) | `review` |
| Anything else (bare integer, ambiguous, missing) | ask via your harness's prompt |

A bare integer is **ambiguous** — it could be a PR number, a `gh
agent-task` ID, or noise. Always ask before assuming review mode.

## Mode: review

Review a PR and post Copilot-actionable fixes as inline review comments.

### Phase 1: fetch PR context

Determine the PR number. If the argument is a PR URL, extract the number.
If a bare integer was passed, confirm with the user it is a PR number (it
could equally be a `gh agent-task` ID). If no number was provided, run
`gh pr list` and ask which PR.

```bash
PR=<number>
gh pr view "$PR" --json title,body,author,baseRefName,headRefName,url,number,additions,deletions,changedFiles
gh pr diff "$PR"
OWNER_REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
gh api "repos/${OWNER_REPO}/pulls/${PR}/files" \
  --jq '.[] | {filename, status, additions, deletions, patch}'
```

Present a brief summary (title, author, base ← head, files changed,
additions/deletions) before continuing.

### Phase 2: run the review

Invoke whatever code-review skill your harness provides (e.g. `age`,
`code-review`, or similar). The review must surface findings with
confidence scores so
phase 3 can triage. If no review skill is available, ask the user to point
at one before continuing — do not guess at findings.

### Phase 3: triage findings for Copilot

For each finding, assign a disposition:

- `COPILOT_FIX` — straightforward fix Copilot can handle (add validation,
  fix logic, delete dead code, inline a wrapper, remove a docstring that
  restates the function name).
- `FUTURE_TASK` — broader context needed, architectural decision, or a
  multi-file refactor.

Present a per-file table:

```text
### path/to/file.ts

| # | Score | Line | Category | Issue                       | Disposition |
|---|-------|------|----------|-----------------------------|-------------|
| 1 | 95    | 42   | BUG      | Null check missing          | COPILOT_FIX |
| 2 | 80    | 78   | COUPLING | Domain imports HTTP client  | FUTURE_TASK |
```

Then show the full comment body for each item.

**`COPILOT_FIX` body**:

```text
**[CATEGORY]**: <issue description>

**Why this matters:** <teaching moment — the principle behind the fix>

@copilot fix this
```

**`FUTURE_TASK` body**:

```text
**[CATEGORY]**: <issue description>

**Why this matters:** <teaching moment — the principle behind the suggestion>

_Noted for future work — not a Copilot fix._
```

Ask the user which to post, which to skip, which to edit, and whether any
disposition should change. **Never post without explicit approval.**

### Phase 4: post the review

Build a single review payload and submit it:

```bash
cat > /tmp/copilot-review-payload.json <<'JSON'
{
  "body": "Automated review — items marked for @copilot are actionable fixes.",
  "event": "COMMENT",
  "comments": [
    {
      "path": "path/to/file.ts",
      "line": 42,
      "side": "RIGHT",
      "body": "**BUG**: Null check missing...\n\n@copilot fix this"
    }
  ]
}
JSON
gh api --method POST \
  "repos/${OWNER_REPO}/pulls/${PR}/reviews" \
  --input /tmp/copilot-review-payload.json
rm -f /tmp/copilot-review-payload.json
```

Print a summary: total posted, `COPILOT_FIX` count, `FUTURE_TASK` count,
PR link.

### Review-mode rules

- Never post without user approval.
- Frame in business terms — never "this function processes data".
- One comment per issue — note "same pattern at lines X, Y, Z" for repeats.
- Respect the codebase — do not flag consistent existing patterns.
- All review intelligence comes from the review sub-skill; this mode handles
  PR fetch + Copilot formatting only.

## Mode: delegate

Create a `gh agent-task`, poll until the Copilot coding agent opens a PR,
then optionally hand off to review mode.

### Copilot model constraints

The coding agent runs on Auto model selection. The CLI has no `--model`
flag. Optimize the task description:

- Be explicit and concrete. Spell out expected behavior.
- Scope tightly. One focused task per delegation.
- Name files and symbols. "In `src/auth/login.ts`, `validateToken` should…"
- State acceptance criteria. "Return 400 for empty email, 422 for malformed."
- Include constraints. Reference existing patterns (e.g., `AppError`).
- Skip the why. The coding agent needs what and where, not motivation.

When confirming the task with the user (phase 1 step 3), check the
description against these criteria. Suggest concrete improvements if it is
vague or under-specified.

### Phase 1: create the agent task

1. If no task description was passed, ask the user what to delegate.
2. Capture repo context: `gh repo view --json nameWithOwner --jq '.nameWithOwner'`.
3. Confirm with the user before creating: show the task description, target
   repo, and ask whether a non-default base branch is needed.
4. Create the task: `gh agent-task create "<task description>"`.
5. Capture the task reference with `gh agent-task list --limit 1`. Parse
   the tab-separated row (`description \t #PR \t repo \t status \t timestamp`)
   and extract the **PR number** and **status**.
6. Confirm to the user: task description, PR number, status, and link
   (`https://github.com/<repo>/pull/<number>`).

### Phase 2: monitor for completion

Poll every **90 seconds**, up to a **15-minute** timeout.

```bash
sleep 90
gh agent-task list --limit 5
```

Find the row matching the captured PR number. Branch on status:

- **In progress / Working** — print a one-line status update with elapsed
  time. Continue.
- **Ready for review / Completed** — break and proceed to phase 3.
- **Failed / Error** — show details via `gh agent-task view <PR#>` and stop.

On 15-minute timeout, tell the user the task is still running, give the PR
link, and ask whether to wait another 10 minutes or stop and review later.

### Phase 3: review the PR

Print a completion summary (PR number, link, final status, elapsed time)
and ask "Run `/copilot review` on PR #N?". On yes, invoke this skill in
review mode with the PR number. On no, print the link and exit.

### Delegate-mode rules

- Always confirm before creating. The user must approve the task and repo.
- Keep polling updates minimal — one line per check.
- Fail fast on errors. If `gh agent-task create` fails, surface the error
  and stop.
- Never guess PR numbers — always parse `gh agent-task list` output.

## Mode: setup

`setup` is a one-time bootstrap that writes `.github/copilot-instructions.md`
and the per-language / per-role files under `.github/instructions/`.

For pure instruction-file authoring or auditing — adding or refining
`.github/copilot-instructions.md` and `.github/instructions/*.instructions.md`
without driving a review or delegation — use
`/github-copilot-repo-instructions` instead; this `setup` mode is the
bootstrap-from-scratch path.

See [references/setup.md](references/setup.md) — read **only** when the
user explicitly says "copilot setup", "generate copilot instructions", or
"bootstrap copilot for this repo". It contains the full walkthrough:
project-type detection, tooling detection, overwrite gating, the global
instructions template, the code-review instructions template,
language-specific stubs, and the coding-agent instructions. Loading it
costs ~12 KB of context and is irrelevant to routine `review` / `delegate`
work.

## What this skill never does

- Run `git push`, open PRs, or merge — that is `/gh`'s job.
- Commit code — that is `/commit`'s job.
- Generate a code review on its own — `review` mode delegates to whatever
  review skill the environment provides (e.g. `age`, `code-review`).
- Modify any file outside `.github/` during `setup`.
- Post review comments without explicit user approval.

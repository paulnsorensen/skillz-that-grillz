---
name: gt
model: haiku
allowed-tools: Bash(gt:*), Bash(git:*)
description: >
  Manage stacked branches with the Graphite (gt) CLI. Use when the user asks
  to "create a stack", "stack a branch", "restack", "submit a stacked PR",
  "rebase the stack", "graphite sync", "track this branch", "gt log", or
  invokes /gt. Covers gt create, gt modify, gt sync, gt submit --stack,
  gt track, and gt log short. Also covers the bootstrap retrofit where iter
  1 of a ralph is a plain git branch and gt track adopts it later. Do NOT
  use for staging or committing — hand off to /commit. Do NOT use for PR
  review traffic — hand off to /gh.
license: MIT
---

# gt

Stacked branches with Graphite. The stack is a chain of small branches each
opening one PR. `gt` keeps them in order across rebases and pushes them as a
group.

## Mental model

- **Trunk**: `main` (or whatever the repo calls it).
- **Stack**: a chain of branches, each parented on the previous one. The
  bottom of every stack is trunk.
- **One PR per branch**, opened all at once with `gt submit --stack`. Each
  PR targets its branch's parent, so the chain is reviewable bottom-up.

`gt` orchestrates branch parents and rebases. `git` still owns commits and
working tree. `gh` (or the GitHub MCP plugin) still owns PR review traffic.

## Core workflow

```bash
# 1. Start from trunk
git checkout main && git pull

# 2. First branch in the stack — stage changes, then create
git add path/to/file
gt create feat/first -m "feat: first slice"

# 3. Next branch stacks on top of the previous one
git add path/to/other
gt create feat/second -m "feat: second slice"

# 4. Inspect the stack
gt log short

# 5. Push every branch and open one PR per branch
gt submit --stack
```

`gt create` requires staged changes (or `--no-interactive` to make an empty
branch) and uses `-m` for the commit message — same shape as `git commit`.

## Amending the current branch

```bash
gt modify              # amend the tip with currently-staged changes
gt modify -a           # stage every change first, then amend (-a == git commit -a)
gt modify -u           # stage every tracked-file change, then amend
gt modify -c -m "msg"  # add a NEW commit on this branch instead of amending
```

`gt modify` amends the current branch's tip commit (or with `-c`, adds a new
commit) and automatically restacks every descendant branch onto the result.
Prefer this over `git commit --amend` inside a stack — bare git won't fix the
children.

## Sync with trunk

```bash
gt sync
```

`gt sync` pulls the latest trunk, deletes branches whose PRs are merged or
closed, and restacks every surviving branch onto the new trunk tip. Run it
before starting new work and after a teammate merges anything upstream.

If sync hits rebase conflicts, see `references/restack-recipes.md`.

## Adopt an existing plain branch

```bash
gt track --parent main             # current branch, parent = main
gt track feat/foo --parent feat/bar # named branch, explicit parent
```

`gt track` brings a plain `git checkout -b` branch into the graphite stack
graph without rewriting commits. Use when:

- The first branch of a stack was created with plain git (e.g. iter 1 of a
  ralph that bootstraps before `gt` is available).
- A teammate handed you a branch and you want to stack on top of it.

After tracking, `gt log short` shows the branch in the stack and
`gt submit --stack` will push it.

## Inspect the stack

```bash
gt log short      # one-line per branch, current branch marked ◉
gt log            # full per-branch detail
gt ls             # default alias for gt log short
gt ll             # default alias for gt log long
```

`gt log short` is the cheap diagnostic — read it before any restack or
submit to confirm the stack looks how you expect.

## Submit a stack as PRs

```bash
gt submit --stack             # push every branch in the current stack
gt submit --stack --draft     # open all PRs as drafts
gt submit --stack --dry-run   # show what would be submitted, don't push
gt ss                         # default alias for gt submit --stack
```

`gt submit --stack` pushes every branch from trunk up to the current branch
tip and opens one PR per branch, each targeting its parent. PR titles default
to the commit subject; PR bodies default to the commit body. The first run
prompts interactively for any missing PR metadata; pass `--no-edit` to skip
the prompts and let existing commit messages drive title and description.

## Bootstrap retrofit (iter 1 plain branch)

When a workflow starts before `gt` is even installed (or, like this repo's
ralphs, before the `gt` skill itself exists), use plain git for the first
branch and adopt it later:

```bash
# Iteration 1 — gt may not be present yet
git checkout -b feat/skill-first main
# … work, commit …

# Iteration 2+ — gt is now available; make the iter 1 branch part of the stack
git checkout feat/skill-first
gt track --parent main
gt create feat/skill-second -m "feat: second slice"
# … and so on
```

The closer (when all queue items are done) verifies the stack with
`gt log short` and runs `gt submit --stack`. The retrofit costs nothing —
`gt track` only edits graphite metadata, not commits.

## Rules

- **Don't reach into `git rebase` mid-stack.** Use `gt sync`, `gt restack`,
  or `gt modify` so children stay parented correctly.
- **Don't push without `gt submit`** in a stack — `git push` skips the
  parent-target wiring `gt` does for you.
- **Stage explicitly** — `gt create` and `gt modify` follow the staged set
  the same way `git commit` does. Stage by name; avoid `git add -A`.
- **Don't amend a published branch** without `gt modify` — bare amends
  break the stack. Use `gt modify` so descendants restack.
- **One concern per branch.** That's the whole point of stacking — keep
  each PR reviewable in isolation.

## Handoffs

- Staging and crafting commit messages → `/commit`
- PR review, CI checks, merge, comments → `/gh`
- Pre-commit hooks complaining → `/prek`

## When to read references

- `references/restack-recipes.md` — rebase conflicts during `gt sync`,
  splitting a branch in the middle of a stack, abandoning a stack tip,
  recovering from an interrupted `gt submit`.
- `references/auth-and-setup.md` — first-time `gt auth`, repo init,
  `gt config` knobs (trunk name, draft default, submit-on-create). Rarely
  needed once the repo is set up.

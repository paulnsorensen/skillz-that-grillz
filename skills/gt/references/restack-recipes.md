# Restack recipes

Less-common stack operations. Read when `gt sync` complains, a branch needs
splitting, or a stack needs to be partially abandoned. The happy path lives
in `../SKILL.md`.

## Conflicts during `gt sync`

`gt sync` runs `git rebase` for every branch in the stack against the new
trunk tip. If any branch hits a conflict, the rebase pauses on that branch.

```bash
# 1. See where you are
gt log short

# 2. Resolve the conflict in the working tree
git status                  # shows conflicted paths
# … edit files, remove <<<<<<< markers …
git add path/to/resolved

# 3. Resume the gt command (handles git rebase --continue + restack)
gt continue                 # or: gt continue -a to stage all changes first
```

If you cannot resolve and want to back out:

```bash
git rebase --abort     # halts the in-flight rebase; gt sync stops with it
```

After abort, the in-flight branch returns to its pre-sync state. Other
branches in the stack that were already restacked stay restacked. Investigate
the conflict, fix the underlying cause (often a merged branch that should be
dropped first), then re-run `gt sync`.

## Conflicts during `gt restack`

`gt restack` is the same machinery as `gt sync` minus the trunk pull. Same
recipe — resolve conflicts, `git add` resolved files, then `gt continue`.

## Splitting a branch

Sometimes a branch grew too big and you want to split its commits across two
stacked branches. The cleanest path uses `gt absorb` or interactive rebase:

```bash
# On the over-stuffed branch
gt log short
git log --oneline                  # identify the keep-here vs move-up commits

# Mixed-reset to the boundary commit so later commits become unstaged changes
git reset <boundary-commit-sha>    # mixed (default): keep in working tree, unstaged

# Lower branch is now exactly the keep-here commits; nothing to do here.
# Stack a new branch on top, then commit the moved-up changes:
gt create feat/upper -m "feat: upper half"
git add path/to/moved/files
gt modify                          # amend feat/upper with the staged changes
gt log short                       # confirm the chain
```

If the boundary lands inside a single commit (you want to split *one* commit
into two), use `git rebase -i <boundary>~1` and `edit` that commit, then
`git reset HEAD^` and re-stage in two passes — see
<https://graphite.dev/docs> for the full recipe.

The original branch's PR (if already submitted) will keep its number; the new
top branch will open a fresh PR on the next `gt submit --stack`.

## Abandoning a stack tip

Drop the top branch without affecting its parents:

```bash
gt down                            # move to the parent
git branch -D feat/topmost         # delete the abandoned tip
gt log short                       # confirm
```

If the tip already has an open PR, close it on GitHub first (via `/gh`) so
graphite doesn't try to update it on the next `gt submit`.

## Abandoning the whole stack

```bash
git checkout main
git branch -D feat/foo feat/bar feat/baz
```

`gt` only stores parent metadata — deleting the branches with `git branch -D`
cleans up the stack. Run `gt log short` after to confirm only `main` remains.

## Recovering from an interrupted `gt submit`

If `gt submit --stack` died mid-push (network blip, auth expiry, sandbox
killed the process), the stack state is fine — only some branches were
pushed. Re-run:

```bash
gt submit --stack
```

`gt` is idempotent: branches already pushed will be no-ops, the rest will
push and open PRs. Already-open PRs get their bodies updated; new PRs are
created for branches that didn't have one yet.

If a partial push left a branch in a weird state (rare), force-restack and
retry:

```bash
gt restack
gt submit --stack
```

## Picking up someone else's stack

Teammate hands you a stack to take over (e.g. they're out, PRs need
follow-up):

```bash
git fetch
git checkout feat/their-bottom
gt track --parent main
git checkout feat/their-middle
gt track --parent feat/their-bottom
git checkout feat/their-top
gt track --parent feat/their-middle
gt log short                       # full chain visible now
```

`gt track` per branch in bottom-up order. Once the chain is in graphite's
metadata, `gt sync` / `gt modify` / `gt submit --stack` work normally.

## Reordering branches mid-stack

Rare. Use `gt move --onto <new-parent>` to re-parent the current branch.
Conflicts during the move follow the `git rebase --continue` →
`gt continue` recipe above.

```bash
git checkout feat/middle
gt move --onto feat/new-parent
gt log short
```

If you find yourself reaching for this often, the work probably wants two
separate stacks rather than one tangled one.

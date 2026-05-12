---
name: pr-stack
model: haiku
allowed-tools: Bash(gt:*), Bash(gh:*), Bash(git:*)
description: >
  Manage stacked PRs using whichever stacking tool is installed: Graphite
  (`gt`) or GitHub's native `gh stack` extension. Detects which is available
  in the current repo, then drives the matching one via its per-tool
  reference (`references/gt.md` or `references/gh-stack.md`). Use when the
  user asks to "create a stack", "stack a branch", "restack", "submit a
  stacked PR", "rebase the stack", "sync the stack", "track this branch",
  or invokes /pr-stack. If neither tool is available, stop and tell the
  user — never fall back to ad-hoc `git push` chains. Do NOT use for
  staging or committing — hand off to /commit. Do NOT use for PR review
  traffic, comments, or CI checks — hand off to /gh.
license: MIT
---

# pr-stack

Stacked branches: a chain of small branches each opening one PR, kept in
order across rebases and pushed as a group.

Two tools implement this workflow today. They share the same mental model
but live in different ecosystems:

- **Graphite (`gt`)** — third-party CLI, mature, requires a Graphite account
  (`gt auth`). Stack metadata lives in `.git/.graphite_repo_config`.
- **GitHub `gh stack`** — first-party `gh` extension (`github/gh-stack`),
  private preview as of 2026-05; repos must be allow-listed via
  [gh.io/stacksbeta](https://gh.io/stacksbeta). Stack metadata lives in
  `.git/gh-stack`. Uses standard `gh auth login` — no third-party account.

This skill picks whichever one the user has and drives it. It does not
emulate stacking with plain `git`.

## Detection — run before anything else

```bash
# gt available?
command -v gt >/dev/null 2>&1 && gt --version >/dev/null 2>&1

# gt initialized in this repo?
test -f "$(git rev-parse --git-dir)/.graphite_repo_config"

# gh stack extension installed?
gh extension list 2>/dev/null | grep -q '^github/gh-stack\b'
```

Decision table:

| `gt` present + repo init | `gh stack` ext present | Action |
| --- | --- | --- |
| yes | no | Use `gt`. Read `references/gt.md`. |
| no | yes | Use `gh stack`. Read `references/gh-stack.md`. |
| yes | yes | Prefer `gt` (mature, generally available). Mention `gh stack` is also present and offer to switch. |
| `gt` installed, repo NOT init | no | Offer to run `gt init` (one-time, see `references/gt.md`). |
| no | no | **Stop. Tell the user neither tool is available.** Don't reach for `git push` chains. See "Neither installed" below. |

The detection itself takes three short commands. Run them every invocation —
the user's environment can change between sessions.

## Neither installed

If detection finds neither `gt` nor the `gh stack` extension, surface this
verbatim and stop:

> Stacked-PR tooling isn't available on this machine / for this repo. Install
> one of:
>
> - **Graphite CLI** — `brew install withgraphite/tap/graphite` (or
>   `npm install -g @withgraphite/graphite-cli@stable`), then
>   `gt auth --token <token from https://app.graphite.com/activate>` and
>   `gt init` inside this repo.
> - **GitHub native `gh stack`** — `gh extension install github/gh-stack`,
>   then make sure the repo is allow-listed at
>   [gh.io/stacksbeta](https://gh.io/stacksbeta) (private preview as of
>   2026-05).
>
> Once one of those is installed, re-invoke `/pr-stack` and I'll drive it.

Do not improvise with plain `git push` chains, parent-target wiring, or
manual PR linking — the value of this skill is the stack-aware machinery,
and faking it produces a worse outcome than telling the user the tool is
missing.

## Mental model (shared)

- **Trunk** is `main` (or whatever the repo calls it).
- **Stack** is a chain of branches, each parented on the previous one. The
  bottom of every stack is trunk.
- **One PR per branch**, each PR targeting its parent branch. The chain
  reviews bottom-up.
- **Submit pushes the whole chain** and opens / updates one PR per branch.
- **Sync** pulls trunk, restacks every surviving branch onto the new trunk
  tip, and prunes branches whose PRs have merged.

Both tools implement this model. Command names diverge — see the per-tool
reference.

## Tool-equivalence cheat sheet

| Action | `gt` | `gh stack` |
| --- | --- | --- |
| Initialize stack metadata | `gt init` (repo-level) | `gh stack init` (per-stack) |
| Create branch on top | `gt create -am "msg"` | `gh stack add -Am "msg"` |
| Amend tip + restack children | `gt modify -a` | (no direct equivalent — edit, then `gh stack push`) |
| New commit + restack children | `gt modify -cam "msg"` | (commit normally, then `gh stack push`) |
| Inspect stack | `gt log short` (`gt ls`) | `gh stack view` (`-s` short) |
| Pull trunk + restack | `gt sync` | `gh stack sync` |
| Cascade restack only | `gt restack` | `gh stack rebase` |
| Submit / update PRs | `gt submit --stack` (`gt ss`) | `gh stack submit` |
| Adopt a plain-git branch | `gt track` | `gh stack init --adopt` |
| Move up / down in stack | `gt up` / `gt down` | `gh stack up` / `gh stack down` |
| Continue after conflict | `gt continue` | `gh stack rebase --continue` |
| Abort halted op | `gt abort` | `gh stack rebase --abort` |
| Open existing PRs as stack | n/a | `gh stack link <PRs...>` |

When in doubt about flags, **always defer to the per-tool reference**, not
to memory — both CLIs drift between versions.

## Rules (apply to both tools)

- **Don't reach into `git rebase` mid-stack.** Use the tool's sync / restack /
  modify commands so children stay parented correctly.
- **Don't `git push` a single branch in a stack.** Use the tool's submit /
  push command — bare push skips the parent-target wiring and leaves the
  rest of the stack stale.
- **Stage explicitly.** Both tools follow the staged set the way
  `git commit` does. Stage by name; avoid `git add -A`.
- **One concern per branch.** That's the whole point of stacking — keep each
  PR reviewable in isolation.
- **After a rebase conflict, use the tool's `continue` / `abort`**, never
  bare `git rebase --continue` / `--abort` — the tool's metadata won't
  update otherwise.

## Handoffs

- Staging and crafting commit messages → `/commit`
- PR review, CI checks, merge, comments → `/gh`
- Pre-commit hooks complaining → `/prek`

## When to read references

- `references/gt.md` — full `gt` command surface (create / modify / sync /
  submit / track / log), install + auth, restack recipes, conflict
  recovery, monorepo / multi-trunk caveats. Read when detection picks `gt`.
- `references/gh-stack.md` — full `gh stack` command surface, install +
  waitlist, exit codes, conflict recovery, divergence from `gt`. Read when
  detection picks `gh stack`.

---
name: gt
description: >
  RESERVED SLOT — not yet implemented. Will cover stacked PR workflows with
  the Graphite (gt) CLI: creating, restacking, syncing, and submitting branches
  in a stack. Use when the user asks to "create a stack", "restack", "submit a
  stacked PR", "graphite sync", or invokes /gt. Until the protocol is written,
  invocations should announce the reserved-slot status and fall back to manual
  gt CLI guidance or the commit / gh skills.
license: MIT
---

# gt

**🚧 Reserved slot — not yet implemented.** *(This banner intentionally lives
outside the GitHub-flavored alert below so it survives non-GFM renderers.)*

> [!IMPORTANT]
> **🚧 Reserved slot — not yet implemented.** The frontmatter reserves the
> `gt` name and the directory shape so future work can fill in the protocol
> without renames or migrations. **If you invoke `/gt` today, announce this
> banner first** and then fall back to the guidance in **Until this is filled
> in** below. Track real-implementation work in the project tracker before
> editing this file.

## Intended scope

A focused skill for [Graphite CLI (`gt`)](https://graphite.dev/docs/graphite-cli)
stacked-PR workflows:

- Create and modify branches in a stack (`gt create`, `gt modify`).
- Restack after rebasing onto trunk (`gt restack`, `gt sync`).
- Submit a stack as a chain of PRs (`gt submit`).
- Navigate a stack (`gt up`, `gt down`, `gt log`).
- Recover from common stack-state mistakes (orphaned branches, divergent trunk).

Out of scope: local git plumbing (delegate to the `commit` skill) and GitHub
PR review work (delegate to the `gh` skill). `gt` orchestrates branches; the
sibling skills cover commits and review traffic.

## Until this is filled in

If a user invokes `/gt`, acknowledge the reserved-slot status, and either:

1. Walk them through the equivalent `gt` CLI command directly, or
2. Hand off to `commit` (for staging/committing) or `gh` (for PRs) as the
   nearest implemented neighbor.

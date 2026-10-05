---
name: skillz-self-update
description: >-
  Refresh the cross-harness research and maintain this repository's published skillz skill.
  Use when a maintainer asks to "self-update skillz", "refresh the harness layout",
  or invokes /skillz-self-update. Do NOT use for audits of other skills.
disable-model-invocation: true
license: MIT
metadata:
  internal: true
---

# skillz self-update

Maintain `skills/skillz/references/harness-layout.md` and `skills/skillz/SKILL.md` in this repository.
Do not treat this as a published `/skillz` mode.

1. Resolve the canonical source. Run `git rev-parse --show-toplevel` and `realpath skills/skillz/SKILL.md`.
   Print the real path.
   Confirm the checkout is this repository: a remote named `skillz-that-grillz`, or a `README.md` with the `## Skills` table.
   Refuse a real path under `~/.agents/skills`, `~/.claude/skills`, or `~/.omp/agent/skills`.
   Refuse a real path in a dotfiles-managed or `npx skills`-managed tree.
   Continue on such a copy only when the user selects it explicitly after seeing the real path.
2. Read `skills/skillz/references/harness-layout.md`, including `## Sources` and `## Rejected`.
3. Check every source in `## Sources` for changes since `Checked:`. Use a fresh-context research agent when the host offers one.
   Cover frontmatter fields, discovery paths, invocation policy, argument handling, and body budgets.
   Cover Claude Code, the Agent Skills spec, Codex, OMP, Pi, Zed, and the `skills` CLI.
4. Compare the research digest with the reference. Update its matrix, rules, template, and `Checked:` date.
   Record each rejected claim and its reason under `## Rejected`.
5. Run `/skillz audit skills/skillz/SKILL.md`, then `/skillz improve skills/skillz/SKILL.md`.
   Keep the published modes and the `/skillz wedge` and `autoimprove` boundaries unchanged.
6. Look for a repository deploy step in `justfile`, scripts, `README.md`, and docs.
   Run it when it exists, and name it in the report.
   Otherwise state that none exists. This step never touches an installed copy.
7. Run `just build` and fix any failure.

Done means: `Checked:` is today, every source was queried, and the published skill passes its own rubric.
Report the research delta, applied fixes, rejected claims, and verification result.

## What this skill never does

- It never runs `npx` or `skills add` against the working copy.
- It does not upgrade GEPA, `skillz-experiment.pyz`, the analytics engine snapshot, Python dependencies, `/wedge`, or releases.
- It does not sync the result to other harnesses. The repository deploy step does that, when one exists.

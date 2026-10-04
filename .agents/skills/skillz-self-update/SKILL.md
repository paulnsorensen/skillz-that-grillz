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

1. Read `skills/skillz/references/harness-layout.md`, including `## Sources` and `## Rejected`.
2. Check every source in `## Sources` for changes since `Checked:`. Use a fresh-context research agent when the host offers one.
   Cover frontmatter fields, discovery paths, invocation policy, argument handling, and body budgets.
   Cover Claude Code, the Agent Skills spec, Codex, OMP, Pi, Zed, and the `skills` CLI.
3. Compare the research digest with the reference. Update its matrix, rules, template, and `Checked:` date.
   Record each rejected claim and its reason under `## Rejected`.
4. Run `/skillz audit skills/skillz/SKILL.md`, then `/skillz improve skills/skillz/SKILL.md`.
   Keep the published modes and the `/skillz wedge` and `autoimprove` boundaries unchanged.
5. Run the repository deploy step when one exists. Run `just build` and fix any failure.

Done means: `Checked:` is today, every source was queried, and the published skill passes its own rubric.
Report the research delta, applied fixes, rejected claims, and verification result.

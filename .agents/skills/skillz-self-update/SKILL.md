---
name: skillz-self-update
description: >-
  Refresh the cross-harness research, then audit, improve, and autoimprove this repository's published skillz skill.
  Use when a maintainer asks to "self-update skillz", "update the published skillz skill",
  "refresh the harness layout", or invokes /skillz-self-update.
  Do NOT use for audits of other skills or as a /skillz mode.
disable-model-invocation: true
license: MIT
metadata:
  internal: true
---

# skillz self-update

Maintain `skills/skillz/references/harness-layout.md` and `skills/skillz/SKILL.md` in this repository.
Do not treat this as a published `/skillz` mode.

1. Resolve the canonical source. Run `realpath "$(git rev-parse --show-toplevel)/skills/skillz/SKILL.md"`.
   Refuse to continue when the directory is not a git checkout.
   Print the real path.
   Refuse when the real path is outside the toplevel.
   Confirm the checkout is this repository: `git remote get-url origin` matches `paulnsorensen/skillz-that-grillz`.
   Refuse a real path under `~/.agents/skills`, `~/.claude/skills`, `~/.omp/agent/skills`, `~/.codex/skills`, `~/.cursor/skills`, or `~/.copilot/skills`.
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
6. Run `/skillz autoimprove skills/skillz` after the improve fixes land.
   Follow `skills/skillz/references/experiments.md` for the questions, the run, the gate, and the export.
   Run the checkout's `skills/skillz/scripts/skillz-experiment.pyz`, not an installed copy.
   Pass `--edit prose+cli` so the search can also change `scripts/inspect_skill.py`.
   Draft `task` cases that each audit one skill fixture named `fixture.md`.
   Put the expected findings in `expected`. The shipped contract judges them with its `task` rubric.
   Never skip the case or budget approval.
   When the user declines a question, skip this step and report the skip.
   Apply the patch only on a `promote` verdict and the user's approval. Then run the step 5 audit again.
7. This repository has no deploy step. Do not install or sync the result to any installed copy.
8. Run `just build` and fix any failure.

Done means: `Checked:` is today, every source was queried, and the published skill passes its own rubric.
Autoimprove also reports a gate verdict or a recorded skip.
Report the research delta, applied fixes, rejected claims, the autoimprove verdict, and the verification result.

## What this skill never does

- It never runs `npx` or `skills add` against the working copy.
- It never applies an autoimprove patch without a `promote` verdict and the user's approval.
- It does not upgrade GEPA, `skillz-experiment.pyz`, the analytics engine snapshot, Python dependencies, `skills/skillz/wedge/scripts/wedge.pyz`, or releases.
- It does not sync the result to other harnesses. That is out of scope for this skill.

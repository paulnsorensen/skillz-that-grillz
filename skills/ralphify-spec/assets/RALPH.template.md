---
agent: claude -p --dangerously-skip-permissions
commands:
  - name: git-log
    run: git log --oneline -10
  - name: working-tree
    run: git status --short
  - name: branch
    run: git rev-parse --abbrev-ref HEAD
  - name: next-issue
    run: ./next-issue.sh
  - name: stack-state
    run: ./stack-state.sh
---

<!--
  Burn-down-todos RALPH template.

  Replace the placeholders marked PLACEHOLDER_* below, drop into a ralph
  directory as RALPH.md, and run via `scripts/run.sh`.

  Placeholders to fill in:
    PLACEHOLDER_ROLE          short descriptor of the agent's role,
                              e.g. "skill-pack porting"
    PLACEHOLDER_QUEUE_DIR     where the queue items live, e.g.
                              ".cheese/issues/" or "tasks/"
    PLACEHOLDER_QUEUE_GLOB    glob that matches one queue item file,
                              e.g. "oslo-v1-*.md"
    PLACEHOLDER_STATUS_FIELD  YAML frontmatter field on each queue item
                              that gates open/done, e.g. "status"
    PLACEHOLDER_CLOSER_STEPS  the closer block — what to run when the
                              queue is drained (push, submit, tag, etc.)
    PLACEHOLDER_BRIESEARCH    integer cap on briesearch sub-agents per
                              iteration, e.g. 3
    PLACEHOLDER_PROJECT       1-2 word project name for commits and prose
-->

# Burn-down-todos ralph

You are an autonomous PLACEHOLDER_ROLE agent running in a loop.
Each iteration starts with a fresh context — your progress lives in
the code, in git history, and in the `PLACEHOLDER_STATUS_FIELD:`
frontmatter of the queue files.

## Iteration: {{ ralph.iteration }} of {{ ralph.max_iterations }}

## Recent changes

{{ commands.git-log }}

## Working tree

{{ commands.working-tree }}

## Current branch

{{ commands.branch }}

## Stack state

{{ commands.stack-state }}

## Next open issue

{{ commands.next-issue }}

## Task

**If `## Next open issue` reports `QUEUE EMPTY`** — the queue is
drained and you must run the closer:

PLACEHOLDER_CLOSER_STEPS

Once the closer steps complete successfully, emit
`<promise>COMPLETE</promise>` on its own line. The runner wrapper
watches for that exact string and terminates the loop.

**Otherwise (queue still has open items)** — drive the next issue
shown above to done in this iteration:

1. Read the issue file in full. Re-confirm the per-iteration steps —
   fresh context means no carry-over.
2. Optional briesearch (≤ PLACEHOLDER_BRIESEARCH sub-agents this
   iteration). Use only when the issue calls them out or a hard
   unknown blocks progress.
3. Branch: each per-item branch must root on `main`. Do not branch
   off the current branch tip if it carries planning artifacts.
4. Do the work per the issue's acceptance.
5. Commit with conventional commits.
6. Validate: run the project's gates (markdownlint, yamllint, tests).
   If a gate fails, fix it in this iteration. Never commit a
   gate-failing change and defer.
7. Mark done: flip the issue's frontmatter
   `PLACEHOLDER_STATUS_FIELD: open` → `PLACEHOLDER_STATUS_FIELD: done`,
   commit the flip on whichever branch tracks the queue files (the
   per-item branch may have those files gitignored — flip on the
   planning branch in that case).
8. Stop the iteration. The next firing picks up the next open issue.

## Rules

- **Don't invent work.** Empty queue means closer, not "find more
  things to improve."
- **One item per iteration.** Touch only files in scope for the
  current issue. No cross-issue drive-by edits.
- **Briesearch budget ≤ PLACEHOLDER_BRIESEARCH per iteration.** Track
  it.
- **Iteration cap is hard.** The runner enforces
  `{{ ralph.max_iterations }}`; hitting it without `COMPLETE` is a
  failure.

## Commit

One logical step per commit, conventional commits format
(`feat(PLACEHOLDER_PROJECT): ...`, `chore(PLACEHOLDER_PROJECT): ...`).
Push happens at closer time, not per-iteration.

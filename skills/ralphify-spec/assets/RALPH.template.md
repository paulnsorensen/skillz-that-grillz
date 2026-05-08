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

  Required sibling scripts in the same ralph directory:
    - next-issue.sh    prints the next open issue, `QUEUE EMPTY`, or
                       `QUEUE READ ERROR` (see Guard 4 in references).
    - closer-gate.sh   verdict gate the closer iteration must pass before
                       any push/submit/COMPLETE step (Guard 4).

  Placeholders to fill in:
    PLACEHOLDER_ROLE             short descriptor of the agent's role,
                                 e.g. "skill-pack porting"
    PLACEHOLDER_QUEUE_DIR        where the queue items live, e.g.
                                 ".cheese/issues/" or "tasks/"
    PLACEHOLDER_QUEUE_GLOB       glob that matches one queue item file,
                                 e.g. "oslo-v1-*.md"
    PLACEHOLDER_STATUS_FIELD     YAML frontmatter field on each queue
                                 item that gates open/done, e.g. "status"
    PLACEHOLDER_PLANNING_BRANCH  branch that carries the queue files
                                 (the planning artifacts are gitignored
                                 elsewhere). Per-item branches root on
                                 main, never on this one.
    PLACEHOLDER_CLOSER_STEPS     project-specific closer steps that run
                                 AFTER the closer-gate verdict (push,
                                 submit, tag, etc.). The closer gate is
                                 always step 1 — these come after.
    PLACEHOLDER_BRIESEARCH       integer cap on briesearch sub-agents
                                 per iteration, e.g. 3
    PLACEHOLDER_PROJECT          1-2 word project name for commits
                                 and prose
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

This iteration is **either** per-item work **or** the closer **or** a
recovery — never two at once. The decision is made entirely by the
`## Next open issue` section above:

| `## Next open issue` shows… | Branch you take |
|---|---|
| Real issue content (`Path: …` + body) | **Per-item iteration** |
| `QUEUE EMPTY` | **Closer iteration** |
| `QUEUE READ ERROR` | **Recovery iteration** (do NOT run the closer) |

The mutual-exclusion rule above and the closer gate (Guard 4) exist
because the original failure mode for this kind of ralph is firing
the closer in the same iteration as per-item work and emitting
`COMPLETE` while items are still open. Per-item step 9 STOPS the
iteration so a single firing cannot cross into the closer.

### Per-item iteration (when `## Next open issue` shows an issue)

1. Read the issue file in full. Re-confirm the per-iteration steps —
   fresh context means no carry-over.
2. Optional briesearch (≤ PLACEHOLDER_BRIESEARCH sub-agents this
   iteration). Use only when the issue calls them out or a hard
   unknown blocks progress.
3. Branch: each per-item branch must root on `main`, never on
   `PLACEHOLDER_PLANNING_BRANCH` (which carries planning artifacts
   that must not appear in any PR diff).
4. Do the work per the issue's acceptance.
5. Commit with conventional commits.
6. Validate: run the project's gates (markdownlint, yamllint, tests).
   If a gate fails, fix it in this iteration. Never commit a
   gate-failing change and defer.
7. Mark done: switch to `PLACEHOLDER_PLANNING_BRANCH` (where the
   queue files live — they are gitignored on per-item branches), flip
   the issue's frontmatter `PLACEHOLDER_STATUS_FIELD: open` →
   `PLACEHOLDER_STATUS_FIELD: done`, commit the flip on the planning
   branch.
8. **STOP THIS ITERATION.** Print `ITERATION DONE: <issue-id>` on its
   own line and exit. Do **not** run any closer step. Do **not** open
   PRs. Do **not** emit `<promise>COMPLETE</promise>`. The next firing
   renders a fresh prompt; if the queue is then empty, that firing
   handles the closer.

### Closer iteration (when `## Next open issue` shows `QUEUE EMPTY`)

The closer is a dedicated iteration. It does not do per-item work.

1. **Run the closer gate first.** From `PLACEHOLDER_PLANNING_BRANCH`,
   execute `./closer-gate.sh`. The verdict line on stdout must read
   `CLOSER GATE PASS`. If it reads `CLOSER GATE FAIL`, abort the
   closer, print the gate output verbatim, and stop the iteration —
   investigation is required, not a closer run. **No push, submit, PR
   creation, or COMPLETE sentinel may happen unless the verdict is
   PASS.**
2. PLACEHOLDER_CLOSER_STEPS
3. Once the closer steps complete successfully, emit
`<promise>COMPLETE</promise>` on its own line and stop work for this
iteration. After `ralph run` exits, the runner wrapper scans the log
for this marker — finding it makes the run a clean success, otherwise
the wrapper exits as a cap-hit failure. If the loop fires another
iteration before exiting, recognise the closer is already done and
emit `<promise>COMPLETE</promise>` again without redoing the work.

### Recovery iteration (when `## Next open issue` shows `QUEUE READ ERROR`)

`next-issue.sh` could not read the queue, almost always because the
working tree is checked out on a per-item branch where the queue
directory is gitignored and absent. **Do not run the closer.** A
closer run here would emit COMPLETE on a phantom-empty queue.

1. Switch to `PLACEHOLDER_PLANNING_BRANCH` (the branch carrying the
   planning artifacts).
2. Print `ITERATION SKIPPED: queue read error from <prior-branch>`
   and stop. The next firing will render a valid queue read.

## Rules

- **Mutual exclusion.** An iteration is per-item OR closer OR
  recovery — never two at once. Per-item step 8 STOPS the iteration.
  The closer iteration does no per-item work.
- **Closer requires gate.** The closer iteration begins with
  `./closer-gate.sh`. No push, submit, PR creation, or COMPLETE
  sentinel unless the verdict is `CLOSER GATE PASS`.
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

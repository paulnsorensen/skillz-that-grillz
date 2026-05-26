# Guard contracts

Every ralph this skill generates ships with the four guards below.
Burn-down-todos ralphs add Guard 4 on top of the first three. They are
not optional — bare `ralph run` is too lenient for unattended work, and
the guards close the holes that produce silent runaway loops.

## Guard 1 — Iteration cap (refuses to start without `-n`)

`ralph run` accepts unbounded iteration if `-n` is missing. That mode burns
tokens forever and is wrong for almost every real ralph. The runner wrapper
`scripts/run.sh` checks the args before launching `ralph run` and exits
non-zero if no `-n` / `--max-iterations` is present.

The wrapper also writes the cap into the environment as `RALPH_CAP` so the
guard agent script and the prompt body can both see it. Generated
`RALPH.md` files reference the cap explicitly in their rules section so the
agent knows the ceiling.

## Guard 2 — Cap exit is loud

The wrapper prints a startup banner with the cap value (`>>> ralph run
with cap=N`) and, after `ralph run` exits, scans the captured log for
the COMPLETE sentinel. If the cap was hit before the agent printed
COMPLETE, the wrapper exits non-zero with a `CAP HIT WITHOUT COMPLETE`
banner. Silent rollover is forbidden — if the loop ran out the clock
without finishing, the human needs to see it.

Implementation lives in `scripts/run.sh`. The wrapper pipes `ralph run`
output through `tee` to both the terminal (live human visibility) and a
log file, then post-processes the log to choose the right exit code.
There is no mid-run early termination on COMPLETE — ralph runs to its
natural end (cap, error, or `--stop-on-error`), and the wrapper makes
the success/failure call afterwards.

## Guard 3 — `<promise>COMPLETE</promise>` sentinel

Every generated `RALPH.md` body declares an explicit done condition:

```markdown
**If the work is finished**, emit `<promise>COMPLETE</promise>` on its own
line as your final output. After `ralph run` exits, the runner wrapper
scans the captured log for this exact string and treats the run as a
success if it finds it; otherwise the run is flagged as a cap-hit
failure.
```

`<promise>...</promise>` is unambiguous: the agent cannot accidentally
print it, the wrapper greps for the literal string, and reviewers reading
the log can see the moment the loop terminated cleanly. The check is
post-run, not streaming — see Guard 2 for the exit-code contract.

## Guard 4 — Closer gate (`closer-gate.sh`)

Burn-down-todos ralphs need a closer that runs once the queue is drained
(open PRs, push the stack, tag a release, etc.). Without a guard, the
agent can fire that closer in the same iteration as per-item work — and
emit `COMPLETE` while items are still open. Guard 4 makes that
impossible.

The skill ships `assets/closer-gate.sh.template`. For every
burn-down-todos ralph, copy it into the ralph directory as
`closer-gate.sh`, fill the placeholders (queue dir, glob, status field,
planning branch), `chmod +x` it, and reference it from the closer block
in `RALPH.md`.

Contract:

- stdout begins with **exactly** `CLOSER GATE PASS` (every queue item
  is `status: done`) or `CLOSER GATE FAIL` (one or more items still
  open, or the queue directory is missing — almost always wrong
  branch).
- exit `0` on PASS, exit `1` on any FAIL. Stop-on-error in `run.sh`
  picks the failure up; the agent must also read the verdict line and
  refuse to proceed.
- Runs as **step 1** of the closer block. No push, submit, PR
  creation, or COMPLETE sentinel may happen unless the verdict is
  PASS. The generated `RALPH.md` template (`assets/RALPH.template.md`)
  encodes this ordering.

## Optional guard — Pre-agent short-circuit

Independent of the cap and the COMPLETE sentinel, a ralph can short-circuit
**before** spawning the agent at all by pointing `agent:` at a bash script
that checks a precondition and exits non-zero if there is no work:

```bash
#!/usr/bin/env bash
# guard.sh — exit 1 to stop ralphify before wasting an agent iteration.
set -euo pipefail

QUEUE="$(dirname "$0")/QUEUE.md"
if ! grep -q '^- \[ \]' "$QUEUE" 2>/dev/null; then
  printf 'No unchecked items — stopping.\n' >&2
  exit 1
fi

exec claude -p --dangerously-skip-permissions "$@"
```

```yaml
agent: ./guard.sh
```

Use this when a `check-done.sh` command would still burn an agent
invocation just to see "nothing to do". Common preconditions: unchecked
queue items, coverage threshold, lint error count.

The pre-agent guard composes with the iteration cap and COMPLETE sentinel —
all four guards can be active at once.

## Picking the right guard

| Condition you can detect | Best guard |
|---|---|
| Queue file empty / threshold reached *before* agent runs | Guard script (`agent: ./guard.sh`) |
| Agent decides at the end of an iteration that work is done | `<promise>COMPLETE</promise>` sentinel |
| Belt-and-braces ceiling on tokens / time | Iteration cap (`run.sh` enforces) |
| Closer must not fire while queue items are still open | Closer gate (Guard 4, `closer-gate.sh`) |

Generated ralphs use the cap + sentinel pair by default. Burn-down-todos
ralphs add the closer gate. Add a pre-agent guard when the precondition
is cheap to check and the agent has nothing useful to do when it fails.

## Queue-read script (the canonical `next-issue.sh`)

Burn-down-todos ralphs ship a `next-issue.sh` whose stdout drives the
mutual-exclusion decision in `RALPH.md`. The contract is:

| stdout starts with | Iteration should… |
|---|---|
| `Path: …` + issue body | run the **per-item** branch |
| `QUEUE EMPTY` | run the **closer** branch (which begins with `closer-gate.sh`) |
| `QUEUE READ ERROR` | run the **recovery** branch — switch to the planning branch and stop |

The implementation ships in `assets/next-issue.sh.template`. Copy it
alongside `RALPH.md` as `next-issue.sh`, fill the `PLACEHOLDER_*`
markers (queue dir, glob, status field, planning branch), and
`chmod +x` it.

A missing queue directory MUST print `QUEUE READ ERROR`, not
`QUEUE EMPTY`. Treating "directory absent" as "queue drained" is the
silent-queue-empty footgun documented under Common bugs below — it
lets the closer fire on a phantom-empty queue when the working tree
is on a per-item branch where the queue dir is gitignored and absent.

A `QUEUE EMPTY` output is the **only** signal that authorizes a closer
iteration; a `QUEUE READ ERROR` output must be treated as "wrong
branch", not as drained.

## Common bugs

These are the failure modes burn-down-todos ralphs trip on if Guard 4
and the queue-read contract are missing or weakened. The hardening
above exists because each one has been observed in practice.

### Premature closer (closer + per-item in the same iteration)

**Symptom.** A single iteration runs per-item work *and* the closer.
The agent commits issue work, then immediately runs `gt submit --stack`
or `gh pr create` and emits `<promise>COMPLETE</promise>` while later
items are still `status: open`.

**Defense.** Guard 4 (closer gate) plus the mutual-exclusion Task
block in `assets/RALPH.template.md`. Per-item step 8 STOPS the
iteration with `ITERATION DONE: <id>` and a "do not chain into the
closer" instruction. The closer block runs only when the prompt's
`## Next open issue` section shows `QUEUE EMPTY` — and even then,
must wait for `CLOSER GATE PASS` before any push/submit step.

**How to detect in logs.** Search the per-iteration log for the
literal string `CLOSER GATE` (must appear before any push/submit
verb), the `<promise>COMPLETE</promise>` sentinel (must not coexist
with `ITERATION DONE: …`), and any `gh pr create` / `gt submit` lines
in iterations whose `## Next open issue` was a real issue.

### Silent queue-empty footgun (missing dir → empty queue)

**Symptom.** `next-issue.sh` prints `QUEUE EMPTY` even though the
queue directory simply does not exist on the current branch. The
agent treats that as "queue drained" and runs the closer.

**Defense.** The queue-read script (above) returns `QUEUE READ ERROR`
when the queue directory is absent. The Task block in the RALPH
template routes that signal to the recovery branch — switch to the
planning branch and stop, do not run the closer.

**How to detect in logs.** Search the per-iteration log for
`QUEUE EMPTY` and cross-reference with the iteration's branch. If the
branch is a per-item branch where the queue dir is gitignored, the
script lied — replace it with the hardened version.

### Branch-context confusion (queue files invisible)

**Symptom.** The agent thinks the queue is empty because it is
checked out on a per-item branch (rooted on `main`) where the queue
directory is gitignored and absent. Per-item branches are intentional
— they keep planning artifacts out of PR diffs — but they make the
queue invisible.

**Defense.** Two parts. (1) `next-issue.sh` and `closer-gate.sh` both
print the current branch in their failure output, so the human reading
the log can see the wrong-branch fingerprint. (2) The RALPH template
names the planning branch (`PLACEHOLDER_PLANNING_BRANCH`) explicitly
and per-item step 7 instructs the agent to switch to it before
flipping queue state — flipping a queue file on a per-item branch
silently no-ops because the file isn't tracked there.

**How to detect in logs.** Cross-reference the iteration's `branch`
output with the planning branch named in the prompt. Any iteration
that tried to read or write queue state from a per-item branch is a
candidate for this bug.

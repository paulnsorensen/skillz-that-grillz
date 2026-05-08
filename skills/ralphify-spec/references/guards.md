# Guard contracts

Every ralph this skill generates ships with three guards. They are not
optional — bare `ralph run` is too lenient for unattended work, and the
guards close the holes that produce silent runaway loops.

## Guard 1 — Iteration cap (refuses to start without `-n`)

`ralph run` accepts unbounded iteration if `-n` is missing. That mode burns
tokens forever and is wrong for almost every real ralph. The runner wrapper
`scripts/run.sh` checks the args before launching `ralph run` and exits
non-zero if no `-n` / `--max-iterations` is present.

The wrapper also writes the cap into the environment as `RALPH_CAP` so the
guard agent script and the prompt body can both see it. Generated
`RALPH.md` files reference the cap explicitly in their rules section so the
agent knows the ceiling.

## Guard 2 — Iteration count surfaced and cap exit is loud

The wrapper prints a banner each iteration (`>>> iteration N of CAP`) and,
when the cap is hit before the agent prints the COMPLETE sentinel, exits
non-zero with a `CAP HIT WITHOUT COMPLETE` banner. Silent rollover is
forbidden — if the loop ran out the clock without finishing, the human
needs to see it.

Implementation lives in `scripts/run.sh`. The wrapper consumes `ralph run`
output in a streaming fashion, watches for the COMPLETE marker, and chooses
the right exit code based on whether the marker was seen.

## Guard 3 — `<promise>COMPLETE</promise>` sentinel

Every generated `RALPH.md` body declares an explicit done condition:

```markdown
**If the work is finished**, emit `<promise>COMPLETE</promise>` on its own
line as your final output. The runner wrapper watches for this exact string
and stops the loop.
```

`<promise>...</promise>` is unambiguous: the agent cannot accidentally
print it, the wrapper greps for the literal string, and reviewers reading
the log can see the moment the loop terminated cleanly.

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
all three can be active at once.

## Picking the right guard

| Condition you can detect | Best guard |
|---|---|
| Queue file empty / threshold reached *before* agent runs | Guard script (`agent: ./guard.sh`) |
| Agent decides at the end of an iteration that work is done | `<promise>COMPLETE</promise>` sentinel |
| Belt-and-braces ceiling on tokens / time | Iteration cap (`run.sh` enforces) |

Generated ralphs use the cap + sentinel pair by default. Add a pre-agent
guard when the precondition is cheap to check and the agent has nothing
useful to do when it fails.

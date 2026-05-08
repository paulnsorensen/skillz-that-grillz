---
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
license: MIT
name: ralphify-spec
description: Generate a ralphify-approved ralph directory (RALPH.md + optional scripts) from a plain-English description of repetitive or iterative work. Use this skill whenever the user says "ralphify", "create a ralph", "ralph wiggum", "autonomous loop", "/ralphify", references Geoffrey Huntley's Ralph Wiggum method, or asks to wrap iterative work in ralphify (test-until-green, refactor-until-done, lint-until-clean, coverage-until-90, burn-down-todos, resolve-review-comments). Trigger even when the user does not explicitly name ralphify but describes an open-ended loop ("keep fixing tests until they pass", "port files one by one until the directory is done"). Do not trigger for one-shot tasks — ralphs exist for work that benefits from running N times against a stop condition.
---

# ralphify-spec

Translate a plain-English iterative task into a valid, runnable ralphify ralph
directory — a `RALPH.md` with well-formed YAML frontmatter, useful command
blocks, and a prompt body that follows the Ralph Wiggum method.

The user does not need to know how ralphify works. Do not explain frontmatter,
placeholders, or shlex quirks to them. Translate their goal into a working
ralph and hand them the suggested run command.

## When this fits

Ralphs pay off for work where each iteration makes incremental progress and a
stop condition tells the loop when to halt:

- climb test coverage to a threshold
- burn down lint or type-check errors
- port files from one language/framework to another
- resolve PR review comments one by one
- work through a queue of items until empty

If the task is one-shot ("add a button to this page", "explain this function"),
a ralph adds nothing. Recommend a single-shot implementation and stop.

## Workflow

### 1. Capture intent (2-3 questions, max)

Ask only what you cannot infer from the conversation or cwd. Skip questions
the user already answered.

1. **What does "done" look like for one full run?** (coverage above 90%, zero
   clippy warnings, all review threads resolved). Drives the stop condition
   and which commands the ralph surfaces each iteration.
2. **Language and tools?** Inspect `pyproject.toml`, `Cargo.toml`, or
   `package.json` first; ask only if ambiguous.
3. **Hard constraints?** Files to leave alone, commit format, style guide.
   Ask only if non-obvious.

Do **not** ask about command blocks, frontmatter fields, placeholders, or YAML.
Translation is your job.

### 2. Pick a name and location

- Derive a kebab-case name (`coverage-climber`, `ts-porter`,
  `clippy-burndown`) unless the user provided one. The validator (step 7)
  enforces the exact character set ralphify accepts.
- Default location: `ralphs/NAME/` inside the current repo. Confirm only if
  cwd is not a sensible home for it.

### 3. Scaffold from the canonical template, then rewrite

Start from ralphify's own template so the file parses:

```bash
ralph init ralphs/NAME
```

If `ralph` is not on `PATH`, fall back to `~/.local/bin/ralph` (where
`uv tool install ralphify` places it). After scaffolding, rewrite the file
for the user's task — do not ship the stock template.

### 4. Design the frontmatter

`references/schema.md` is the authoritative schema reference. Read it when you
need exact rules; do not re-derive from this skill body.

Default agent: `claude -p --dangerously-skip-permissions`, unless the user is
on a different agent (Gemini, Cursor agent).

Set `credit: false` if the repo forbids automated commit trailers.

Default `commands` picks by stack:

- any repo: `git-log` → `git log --oneline -10`
- Python: `tests` → `uv run pytest`, `lint` → `uv run ruff check .`
- Rust: `tests` → `cargo test`, `lint` → `cargo clippy --all-targets -- -D warnings`
- Node/TS: `tests` → `npm test`, `lint` → `npm run lint`
- stop-condition probe: write a script (step 5), reference as `./check-done.sh`

Add `args` only if the ralph is meant to be reusable across targets.
Hardcode otherwise — generalizing later is cheap.

#### Guard scripts (short-circuit pattern)

When the ralph has a clear "all done" condition checkable before spinning up
an agent, wrap the agent call in a guard script and point `agent:` at the
script. See `references/guards.md` for the full pattern, including the
iteration-cap and COMPLETE-sentinel guards every generated ralph must include.

### 5. Shell features belong in scripts

`commands[].run` is parsed with `shlex.split`. No pipes, `&&`, redirects, or
`$(...)`. For anything non-trivial, write a script in the ralph directory
and reference it with `./name.sh`:

```yaml
commands:
  - name: coverage
    run: ./check-coverage.sh
```

- Make scripts executable (`chmod +x`).
- Scripts invoked via `./` prefix run with the ralph directory as cwd;
  commands without the prefix run from the project root.
- Keep scripts short and single-purpose — the agent only sees their output.

### 6. Write the prompt body

Each iteration starts with a fresh context. The prompt must re-establish
enough situation every time to be useful. Follow the canonical shape:

1. **Role + loop awareness.** "You are an autonomous {role} agent running
   in a loop." Include `{{ ralph.iteration }}` under an `## Iteration` header
   so the agent knows where it is — useful for "on final iteration, do
   cleanup" logic.
2. **Context-reset acknowledgment.** "Each iteration starts with a fresh
   context. Your progress lives in the code and git." Stops the agent from
   trying to remember state.
3. **Command output sections.** Put `{{ commands.<name> }}` under
   `## <Title>` headers. The agent only sees what the prompt shows it.
4. **Task section.** State exactly what one iteration of work is. Narrow
   beats broad: "add tests for one untested function" beats "improve
   coverage". A fresh-context agent should pick a target and finish it
   within a single iteration.
5. **Stop condition.** A `<promise>COMPLETE</promise>` sentinel the agent
   prints when the loop's "done" condition is met. The runner wrapper in
   `scripts/run.sh` watches for this and halts.
6. **Rules.** Bulleted list — what to avoid, what to always do.
7. **Commit conventions.** One commit per iteration; format (Conventional
   Commits or repo style); push or not.

HTML comments (`<!-- ... -->`) are stripped before piping to the agent —
safe for maintenance notes, never wastes tokens.

### 7. Validate with the bundled script

Run the bundled validator against the draft. It is the gate — do not skip,
do not mentally re-implement what it checks:

```bash
uv run --with pyyaml python scripts/validate.py PATH/TO/RALPH.md
```

(Adjust the script path to wherever this skill lives in your harness.)

It enforces the schema rules in `references/schema.md` — required fields,
name regex, shlex safety, placeholder coverage, agent on PATH, timeout type.
Exit 0 = clean (warnings advisory), 1 = errors that must be fixed, 2 =
environment problem.

Pay attention to warnings — declared-but-unused commands or args are
cleanup signals.

### 8. Wire the runner wrapper

Drop `scripts/run.sh` into the generated ralph directory (or symlink it).
The wrapper enforces the iteration-cap rule the bare `ralph run` does not:

- Refuses to start if `-n / --max-iterations` is missing — every generated
  ralph must declare a cap.
- Surfaces the iteration count loudly each iteration.
- Exits with a non-zero code (and a banner) when the cap is hit before the
  COMPLETE sentinel — silent continuation is forbidden.
- Watches for `<promise>COMPLETE</promise>` and stops the loop early on a hit.

See `references/guards.md` for the full guard contract.

### 9. Report back

Show the user:

1. **File tree** of the created directory.
2. **One sentence** describing what a single iteration does.
3. **Suggested first run:** `./scripts/run.sh ralphs/NAME -n 50 -t 1800 -s
   -l ralphs/NAME/logs` — 50 iterations, 30-minute timeout, stop on error,
   logs captured. Starting with `-n 50` lets them see the loop work before
   going unbounded — and the wrapper refuses to drop the cap entirely.

## Templates

`assets/RALPH.template.md` is the canonical burn-down-todos template — the
shape this very skill's port-loop uses. Copy it into the new ralph dir and
fill the placeholders rather than re-deriving the structure each time.

## What not to do

- Do not invent frontmatter fields ralphify does not support. Schema is
  small on purpose — anything outside `agent`, `commands`, `args`, `credit`
  is noise.
- Do not pipe or chain commands in `run:`. Use a script.
- Do not leave placeholders without a matching declaration — they render
  as literal text and confuse the agent.
- Do not ask the user about ralphify internals. If they wanted to write
  YAML, they would not be here.
- Do not default to `-n` unbounded on first run. Start with `-n 50`. The
  runner wrapper enforces this.
- Do not omit the `<promise>COMPLETE</promise>` sentinel. Loops without an
  explicit terminator burn tokens until the cap and exit ambiguously.

## Bundled scripts

```bash
# Validate a draft RALPH.md against the v0.3.0 schema.
#   exit 0 = clean, 1 = errors, 2 = environment problem
uv run --with pyyaml python scripts/validate.py PATH/TO/RALPH.md

# Wrap `ralph run` so the iteration cap is mandatory and the COMPLETE
# sentinel decides the exit code. Forwards every other flag to ralph run.
scripts/run.sh PATH/TO/RALPH_DIR -n 50 -t 1800 -s -l PATH/TO/RALPH_DIR/logs
```

## References

- `references/schema.md` — read when you need the exact frontmatter
  rule (v0.3.0): required fields, regex constraints, default values.
- `references/guards.md` — read when designing the guard story for a
  new ralph: iteration cap, COMPLETE sentinel, pre-agent short-circuit.
- `assets/RALPH.template.md` — copy into the new ralph dir as the
  starting point for burn-down-todos loops; fill the `PLACEHOLDER_*`
  markers in place.

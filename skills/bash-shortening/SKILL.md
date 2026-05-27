---
name: bash-shortening
description: >
  Write, review, or refactor Bash scripts into concise, idiomatic shell
  code. Use this skill whenever the user is editing a `.sh`, `.bash`, or
  `bash`-fenced block with a Bash shebang (or no shebang in a Bash-only
  context), mentions "shorten this script", "make this more idiomatic",
  "clean up this bash", "this script is too long", "is there a shorter
  way to do this in bash", or asks for a code review of shell scripts.
  Also trigger when generating new Bash scripts from scratch — produce
  idiomatic patterns the first time instead of refactoring later. Covers
  parameter expansion, brace expansion, process substitution, arithmetic
  contexts, function patterns, pipelines vs temp files, heredocs,
  associative arrays, parallel execution, and CSV/IFS parsing — 51
  techniques total. Refuses cryptic one-liners when shortening would
  hurt readability. Do NOT use for fish, zsh, or POSIX-only `/bin/sh`
  scripts where bashisms would break portability.
license: MIT
---

# bash-shortening

Make Bash scripts shorter without making them harder to read. Source: 51
techniques from Karandeep Singh's "Bash Code Shortening" article, codified
here as a cheatsheet plus categorized references.

## Philosophy

Shortening is about **expressing intent**, not saving keystrokes. The win
is fewer moving parts (subprocesses, temp files, intermediate variables) —
not denser code per line. A 7-line script that creates a temp file and
deletes it is worse than a 1-line pipeline; a cryptic `${1:-${X:-${Y/-,/ }}}`
chain is worse than three clear lines.

Two heuristics decide every call:

1. **Does shortening eliminate a class of bugs?** (e.g. forgotten `rm` on
   temp files, missed branches in nested `if`, race on directory checks
   before `mkdir -p`). If yes, shorten.
2. **Does shortening obscure intent for the next reader?** If yes, expand.

Both heuristics can fire at once — when they conflict, prefer clarity.
See `references/anti-patterns.md` for the long-form version.

## How to use this skill

**Architecture: workers gather, main thread writes.** Read-only analyzer
workers run in parallel to collect violations. Then the main thread is the
*single writer* — it runs the script, verifies the result, and hand-edits
the rest. This avoids parallel writes to the same file (the lost-update
race the user is right to worry about).

1. **Identify the target.** File path(s), pasted snippet, or `bash`-fenced
   block. Confirm it's actually bash (see "When NOT to use this skill").
2. **Dispatch the analyzer sweep.** Fire one read-only analyzer worker per
   reference category in a single batched call so they run concurrently.
   Analyzers only *report* violations — they do not write, do not run the
   script, do not touch the target. See "Parallel sweep — worker contract"
   below.
3. **Merge analyzer reports.** Collect each analyzer's `path:line —
   verbose → idiomatic` hits. Dedupe overlap (the same line can be flagged
   by two categories — keep one entry, note both attributions).
4. **Run the script from the main thread.** Run
   `python3 scripts/bash-shorten.py --apply <target>` (one file per
   invocation — loop over the list if there are several). This applies
   the high-confidence `core` rules in one writer. Never delegate
   `--apply` to a worker.
5. **Verify the script's output.** `bash -n <target>` for syntax,
   `shellcheck <target>` if available, and read the diff. If a rule
   produced something surprising, revert and narrow with `--rules` or
   `--skip` before retrying.
6. **Present the remaining punch list.** Show the analyzer hits the script
   didn't cover and let the user pick which categories to apply,
   especially when anti-pattern hits conflict with shortening hits.
7. **Hand-edit the rest.** One technique per change with rationale visible.
   Use the `before / after` examples in the matching reference file.
   Preserve quoting (`"$var"`), `set -euo pipefail`, and any error
   handling. Stop before any rewrite needs a comment to explain *what* it
   does.

Don't bulk-rewrite silently. Workers gather, main thread writes, every
change attributable to one technique the user can learn.

## Automated rewrites

The skill ships with `scripts/bash-shorten.py` — a Python rewriter for
the high-confidence patterns. It has no third-party Python dependencies
but does require [ast-grep](https://ast-grep.github.io/) (`sg`) on PATH;
see the "ast-grep is required" section below. It defaults to dry-run
(prints a unified diff to stdout and per-rule counts to stderr) and only
writes when you pass `--apply`. Always run `shellcheck` on the output
afterwards.

```bash
# preview rewrites
python3 scripts/bash-shorten.py path/to/script.sh

# apply (atomic write, preserves the file in place)
python3 scripts/bash-shorten.py --apply path/to/script.sh

# only run a subset of rules
python3 scripts/bash-shorten.py --rules backticks,test-numeric script.sh

# disable a rule
python3 scripts/bash-shorten.py --skip find-exec-rm-delete script.sh

# enable the modernize rule group (off by default — see "Rule groups")
python3 scripts/bash-shorten.py --include modernize --apply script.sh

# explore the ruleset
python3 scripts/bash-shorten.py --list
python3 scripts/bash-shorten.py --explain test-numeric

# verify the rules still work after editing
python3 scripts/bash-shorten.py --self-test
```

**Two rule groups (`core`, `modernize`)** with embedded positive and
negative fixtures for every rule (run `--list` to see the current
counts). The `core` group is on by default; the `modernize` group is
opt-in. Most `core` rules mirror source-article examples
(`sed-replace-first/all`, `echo-wc-c`, `cut-c-substring`,
`expr-arith-vars/literal/increment`, `combined-tests`, `test-numeric`,
`empty-default`, `param-default`, `mkdir-guard`, `for-range-expansion`);
the rest are bonus core patterns the article doesn't cover but are
obvious wins (`backticks`, `legacy-null-check`, `empty-string-eq`,
`find-exec-rm-delete`, `cat-file-pipe-grep` — three of which shellcheck
flags but doesn't auto-fix).

**What the rewriter deliberately *can't* do**: anything that needs data
flow analysis (single-use variable inlining), multi-statement detection
(temp-file → pipeline), function extraction, parallelization, or
behavioral judgment ("is this `&&`/`||` chain safe?"). Those are
hand-edits guided by the references.

When a user asks "can you shorten this whole script?", the main thread
runs the script *after* the read-only analyzer sweep returns (see next
section). The script handles the boring 60% of the rewrites; the analyzers
cover what regex can't see; the main thread is the only writer.

## Parallel sweep — worker contract

A single in-context reviewer reliably tunnel-visions on the first one or two
categories it looks at, missing whole classes (command substitution,
parameter expansion, arithmetic, brace expansion, process substitution,
functions, advanced, real-world). The fix is to fan out **read-only**
analyzers — one per reference category — in parallel. They gather
violations; they don't write. The main thread runs the script and the
hand-edits afterward.

### Workers are strictly read-only

Every parallel worker has the same contract: **read the target, report
violations, return.** No worker writes to disk, applies rewrites, or runs
`scripts/bash-shorten.py --apply`. If a job involves writing, it isn't a
worker — it's the main thread's job.

Why: parallel writes to the same file race. Even if you scoped each worker
to its own category, they'd produce overlapping diffs against shared lines
that the main thread would still have to merge by hand. Gather first, write
once.

### Worker type

| Worker | Count | Role | Output |
|---|---|---|---|
| Category analyzer | One per reference category (9 by default) | Read `references/<category>.md` and scan the target for the patterns it covers | Markdown list of `path:line — verbose → idiomatic`, no rewriting |

**Brief for each category analyzer** (substitute `<category>` and
`<target>` — paths in this skill are relative to the skill directory
unless noted; pass the repo-relative path of the script being shortened
as `<target>`):

> Read `references/<category>.md` (relative to the bash-shortening skill
> directory). Scan `<target>` for every instance of the patterns it
> covers. Return a markdown list: `path:line — verbose form → idiomatic
> form`. Do not rewrite the file, do not run `scripts/bash-shorten.py`,
> do not write anything. If a hit conflicts with the anti-patterns
> reference, flag it but still include it.

**Dispatch rule:** fire all analyzers in a single batched call so they run
concurrently. Sequential dispatch defeats the latency win that's the whole
reason to fan out.

### Harness-agnostic — pick your primitive

The contract is "fire N read-only tasks in parallel and collect results."
Use whichever primitive your harness exposes — the skill does not depend on
any one of them:

- A parallel sub-agent / sub-task call (one batched message that spawns all
  workers at once).
- Multiple parallel tool calls in a single assistant turn.
- A task / node fan-out in an agent-graph framework.
- Plain shell: `xargs -P 9` or `parallel` over the worker briefs piped to
  your agent CLI, with each worker's stdout captured to a separate file.
- **No parallel primitive available:** walk the category table sequentially
  in one pass. Tick each off explicitly — tunnel-vision on the first hits
  is the failure mode this section exists to prevent.

### Categories

These are the **complete** checklist — do not stop after finding hits in two
or three of them. Tunnel-vision on `basename`/`dirname`/`for-range` while
skipping command substitution, parameter expansion, arithmetic, brace
expansion, process substitution, functions, advanced, and real-world is the
failure mode this section exists to prevent.

| Category | Reference | Article refs | Patterns to find |
|---|---|---|---|
| Command-substitution sweep | `references/command-substitution.md` | 3-7 | `$(cmd)` temp-file elimination, pipelines vs reused output, `xargs` patterns, `find … \| xargs` vs process substitution |
| Parameter-expansion sweep | `references/parameter-expansion.md` | 8-15 | `if [ -z "$X" ]; then X=…` defaults, `cut -c`/`echo \| sed` substitutions, `${#S}` length, `basename`/`dirname` shells |
| Function-pattern sweep | `references/functions.md` | 16-20 | repeated logging blocks, default-param boilerplate, echo-returns, named-param patterns |
| Brace-expansion sweep | `references/brace-expansion.md` | 21-26 | sequential `mkdir`/`touch a b c`, `for i in 1 2 3 4 5` ranges, zero-padded sequences |
| Process-substitution sweep | `references/process-substitution.md` | 27-31 | temp files feeding `diff`/loops, `echo "x" \| cmd` → `<<<` here-strings |
| Arithmetic sweep | `references/arithmetic.md` | 32-38 | `expr`, `$(…)+1` increments, `[ -gt/-lt/-eq ]` numeric tests, `[ "$A" ] && [ "$B" ]` |
| Real-world sweep | `references/real-world.md` | 39-45 | config parsing, log analysis, health checks, batch processing, backups, API+`jq` |
| Advanced sweep | `references/advanced.md` | 48-51 | repeated `echo` lines (→ heredoc), 3+ branch `if`/`elif` (→ assoc array or `case`), sequential independent commands (→ `& … & wait`), `cut -d,` in loops (→ custom `IFS`) |
| Anti-pattern sweep | `references/anti-patterns.md` | 1-2, 46-47 | nested expansions, cryptic one-liners, places where shortening would *hurt* — flag for the user, do not auto-rewrite |

### Merge, apply, verify — single writer

After all analyzers return, the **main thread** owns every write:

1. **Collect and dedupe** the per-category hit lists. The same line can be
   flagged by two analyzers — keep one entry, note both attributions.
2. **Run the rewriter script** from the main thread. Invoke
   `python3 scripts/bash-shorten.py --apply <target>` once per file
   (loop if there are several — the script takes one positional file).
   Defaults to `core`; add `--include modernize` to opt into the sd/rg/fd
   rewrites. This is the *only* place `--apply` runs.
3. **Verify the script's output.** `bash -n <target>` for syntax,
   `shellcheck <target>` if available, and a manual diff read. If a rule
   produced something surprising, revert and narrow with `--rules` or
   `--skip` before retrying.
4. **Present the remaining analyzer punch list** — the hits the script
   didn't cover. Let the user pick which categories to apply, especially
   when anti-pattern hits conflict with shortening hits.
5. **Apply hand-edits** one technique per change with rationale (per the
   "How to use this skill" workflow above). Each edit is a single write
   from the main thread; no fan-out, no parallelism.

**Why one writer:** the analyzers are orthogonal in *what they look for*,
but their proposed rewrites overlap on the same lines (a single `for i in
1 2 3 4 5` hit shows up in brace-expansion *and* arithmetic *and* maybe
real-world). If workers wrote in parallel, the last writer would silently
overwrite the others. Gathering all violations first and applying from one
place keeps the diff auditable and prevents lost-update bugs.

**Why fan out for analysis:** the categories are orthogonal, each needs a
different reference file in working memory, and parallel workers force
coverage that a single pass over many files reliably skips. Read-only
tasks don't race, so parallelism is free here — exactly where the
single-writer discipline doesn't apply.

### Rule groups

| Group | Default | Contents |
|---|---|---|
| `core` | on | 18 idiomatic-bash rewrites that don't change tooling |
| `modernize` | off | `sed-replace-to-sd`, `grep-fixed-to-rg`, `find-name-to-fd` — rewrite to non-coreutils binaries (sd, rg, fd) the user must have installed |

The modernize rules are conservative on purpose: only literal sed
patterns map to `sd`, only `grep -F` (fixed-string) maps to `rg -F`
(plain `grep PAT` uses BRE which differs enough from rg's regex flavor
to risk silent behavior changes), and only `find . -type f -name "GLOB"`
without additional flags maps to `fd` (and even then the `find → fd`
rewrite changes behavior: fd respects `.gitignore` by default).

Opt in via `--include modernize`. The installer (`scripts/install.sh`)
brings down `sd`, `ripgrep`, `fd`, and `ast-grep` so the rewritten code
runs and the rewriter has its required dependencies.

### ast-grep is required

`bash-shorten.py` requires [ast-grep](https://ast-grep.github.io/) (`sg`)
on PATH. Structural patterns (`backticks`, `test-numeric`) route
through ast-grep first using the rule pack at `scripts/sg-rules/`, then
the remaining regex rules run on the output. Tree-sitter parses the bash
once, so context-sensitive rules (skip `#` comments, skip heredoc bodies)
work correctly without ad-hoc lookbehinds in the regex layer.

If `sg` is missing, the script exits with a friendly diagnostic. Two paths
forward:

```sh
brew install ast-grep        # macOS / Linuxbrew
cargo install ast-grep --bin sg
```

Or skip the script entirely and run only the category analyzers from the
parallel sweep — the reference-driven methodology in this file is the
fallback for environments without ast-grep.

**Why some rules are still regex-only.**

- `mkdir-guard`, `empty-default`, `param-default`, `expr-increment`
  need cross-metavariable equality (same name in two positions). YAML
  ast-grep rules can't express that constraint cleanly; the Python
  regex uses a backreference instead.
- `combined-tests` — `[ ... ]` flattens into a list of word tokens, so
  structural matching on the operator side is brittle.
- `for-range-expansion` — needs runtime arithmetic to verify the
  captured integers form a step-1 ascending sequence; pure-YAML rules
  can't compute that.
- `sed-replace-*` — the literal-pattern guard needs character-class
  restrictions in the matcher.

`test-numeric` was previously in this list but moved to `sg-rules/`
as six per-operator rules — tree-sitter-bash distinguishes
`test_command` (`[ ]`) from `conditional_expression` (`[[ ]]`) so the
sg form correctly skips `[[ ]]` whereas the regex bled into it
(issue #18).

These regex-only rules still run after the ast-grep pass. See
`AGENTS.md` for guidance on writing new rules — default to ast-grep,
fall back to regex only when one of the constraints above blocks it.

## Quick wins

The most common bloat patterns and where to read the full treatment.
Numbers in parens are the example numbers from the source article.

| Verbose form | Idiomatic form | Reference |
|---|---|---|
| `if [ -z "$X" ]; then X=default; fi` | `X=${X:-default}` | parameter-expansion (8) |
| `$(echo "$S" \| cut -c1-5)` | `${S:0:5}` | parameter-expansion (10) |
| `$(basename "$P")` / `$(dirname "$P")` | `${P##*/}` / `${P%/*}` *(hand-edit only — silently breaks on trailing-slash paths; `dirname` also diverges from `${P%/*}` when the path has no `/` (real `dirname` returns `.`, the expansion returns the original string); rewriter does not auto-apply)* | parameter-expansion (11-12) |
| `$(echo "$S" \| sed 's/a/b/g')` | `${S//a/b}` | parameter-expansion (13-14) |
| `$(echo -n "$S" \| wc -c)` | `${#S}` | parameter-expansion (15) |
| `$(expr $A + $B)` / `C=$(expr $C + 1)` | `$((A + B))` / `((C+=1))` (or `C=$((C + 1))`) | arithmetic (32-34) |
| `[ $X -gt 100 ]` / `[ $A ] && [ $B ]` | `((X > 100))` / `[[ $A && $B ]]` | arithmetic (35, 37) |
| `mkdir a; mkdir b; mkdir c` | `mkdir -p {a,b,c}` | brace-expansion (21-22) |
| `for i in 1 2 3 4 5` | `for i in {1..5}` (or `{01..10}`, `{2..10..2}`) | brace-expansion (23-26) |
| `cmd > /tmp/x; cmd2 < /tmp/x; rm /tmp/x` | `cmd \| cmd2`  *or*  `cmd2 < <(cmd)` | command-substitution (5), process-substitution (29) |
| `sort a > /tmp/a; sort b > /tmp/b; diff ...` | `diff <(sort a) <(sort b)` | process-substitution (27) |
| `if [ "$E" = dev ]; elif ... ; fi` (3+ branches) | `case` *or* assoc array `${URLS[$E]:-default}` | functions (intro), advanced (49) |
| Repeating `echo "[$(date)] [LEVEL] msg"` | `log()` function with `${1^^}` | functions (16) |
| `find ... > /tmp/x; while read; ...; done < /tmp/x; rm` | `find ... \| xargs cmd` *or* `done < <(find ...)` | command-substitution (6), process-substitution (29) |
| Multi-line `echo "..."` x N | `cat <<EOF ... EOF` heredoc | advanced (48) |
| `cmd1; cmd2; cmd3` (sequential, independent) | `cmd1 & cmd2 & cmd3 & wait` | advanced (50) |
| `cut -d, -f1,2,3` inside loop | `while IFS=, read -r a b c` | advanced (51) |

If the user's pattern doesn't appear here, search the reference index
below — every example from the article is preserved.

## Reference index

Read the file matching the technique class. Each holds the full
before/after from the source article plus gotchas worth knowing.

| File | Covers | Examples |
|---|---|---|
| `references/command-substitution.md` | Pipelines, `xargs`, eliminating temp files, single-shot vs reused command output | 3-7 |
| `references/parameter-expansion.md` | Defaults, alternatives, substring, path extraction, replacement, length | 8-15 |
| `references/functions.md` | Logging, default params, inline conditionals, echo-returns, named params | 16-20 |
| `references/brace-expansion.md` | Directory/file expansion, numeric and char sequences, steps, zero-padding | 21-26 |
| `references/process-substitution.md` | `<(cmd)`, `>(cmd)`, here-strings (`<<<`), feeding loops from commands | 27-31 |
| `references/arithmetic.md` | `$(( ))`, `(( ))`, `[[ ]]`, comparison operators, ternary gotcha | 32-38 |
| `references/real-world.md` | Config parsing, log analysis, health checks, batch processing, backups, user mgmt, API+jq | 39-45 |
| `references/anti-patterns.md` | When *not* to shorten — cryptic one-liners, nested expansions, the philosophy | 1-2, 46-47 |
| `references/advanced.md` | Heredocs, associative arrays, parallel execution + `wait`, custom IFS for CSV | 48-51 |

## When NOT to use this skill

- **Non-bash shells.** Most parameter expansions (`${var//x/y}`,
  `${var:offset:length}`), `[[ ]]`, arrays, and process substitution are
  bashisms. If the script's shebang is `#!/bin/sh`, `#!/usr/bin/env dash`,
  or it targets `posh`/Alpine `ash`/busybox, switch register or refuse and
  explain. `fish` and `zsh` have their own grammars — none of this applies.
- **One-liner golf.** If the user explicitly wants the shortest possible
  line for a code-golf challenge, shortening past readability is the goal,
  not a bug — but call out the readability cost so they own the choice.
- **Critical infrastructure scripts.** Boot scripts, init scripts, and
  scripts that run before logging is set up benefit from being *boring*.
  Don't trade clarity for elegance in code that runs at 3 AM during an
  incident.
- **POSIX-portability requirement.** When a script is shipped as
  `#!/bin/sh` for cross-distro install scripts, stay POSIX. The
  anti-patterns reference has a portability checklist.

## What you don't do

- Don't rewrite the whole file in one pass — one technique per change,
  with the rationale visible.
- Don't introduce new dependencies (`yq`, `jq`, `parallel`) just to enable
  a shortening. Suggest them, but only apply if the user agrees.
- Don't strip comments or `set -euo pipefail` while shortening — those
  are load-bearing.
- Don't claim a rewrite is faster without measuring. Subprocess
  elimination *usually* is, but say "should be faster" not "is 5x faster"
  unless you ran `time` against both.

## Common mistakes to catch on review

These come up often when LLMs (or humans rushing) try to shorten bash:

- **Unquoted `$var`** inside the rewrite. Shortening should never drop
  quoting; word-splitting bugs are worse than verbosity.
- **Arithmetic ternary returning a string.** `$((C > 10 ? "high" : "low"))`
  does not work — bash arithmetic is integer-only. Use
  `[[ $C -gt 10 ]] && S=high || S=low` or a `case`. (Source article
  example 36 has this bug; the arithmetic reference flags it.)
- **`&& ... || ...` as if-then-else.** Only safe when the first branch
  cannot fail. If the first command has any chance of returning non-zero
  on success, the `||` branch fires anyway. Use `if`/`else` for non-trivial
  branches.
- **`mkdir` without `-p`.** Shortening removes the existence check, so the
  `-p` flag is what makes the rewrite safe. Don't drop both.
- **`xargs` without `-r` or `-0`.** Empty input or filenames with spaces
  blow up `xargs`. Use `-r` (don't run on empty) and `-0` with `find -print0`
  for path safety. The article doesn't mention this; flag it on review.

## Source

51 techniques from
<https://karandeepsingh.ca/posts/bash-code-shortening-techniques/> by
Karandeep Singh (2023). Every numbered example in the source article is
preserved in the references — counts and numbering match the original.

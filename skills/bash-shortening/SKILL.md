---
name: bash-shortening
description: >
  Write, review, or refactor Bash and POSIX shell scripts into concise,
  idiomatic shell code. Use this skill whenever the user is editing a
  `.sh`, `.bash`, or `bash`-fenced block, mentions "shorten this script",
  "make this more idiomatic", "clean up this bash", "this script is too
  long", "is there a shorter way to do this in bash", or asks for a code
  review of shell scripts. Also trigger when generating new shell scripts
  from scratch — produce idiomatic patterns the first time instead of
  refactoring later. Covers parameter expansion, brace expansion, process
  substitution, arithmetic contexts, function patterns, pipelines vs temp
  files, heredocs, associative arrays, parallel execution, and CSV/IFS
  parsing — 51 techniques total. Knows when shortening hurts readability
  and refuses to produce cryptic one-liners. Do NOT use for fish, zsh, or
  POSIX-only sh scripts where bashisms (parameter expansion `${var//x/y}`,
  `[[ ]]`, arrays, process substitution `<( )`) would break portability —
  call that out and switch register.
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

When the user asks for shorter shell, do the **whole** workflow, not just
the cheatsheet lookup:

1. **Identify the bloat class.** Match the verbose code against the
   "Quick wins" table below — that tells you which reference file holds
   the idiomatic form.
2. **Try the automated rewriter first** (see "Automated rewrites"
   below). For the 16 highest-confidence patterns, `bash-shorten.py`
   produces a diff in seconds with a tested ruleset. Use it for the
   easy half, then hand-edit the rest.
3. **Apply the rewrite.** Use the `before / after` examples in the matching
   reference file. Preserve quoting (`"$var"`), `set -euo pipefail` if the
   surrounding script has it, and any error handling already in place.
4. **Stop before it gets cryptic.** If the rewrite needs a comment to
   explain *what* it does (not *why*), back off to the verbose form. The
   anti-patterns reference has the calibration.
5. **Show the user the diff** with a one-line rationale per change — what
   was eliminated (subprocess, temp file, intermediate var, redundant
   branch).

Don't bulk-rewrite a whole script silently. Each change should be
attributable to one technique the user can learn.

## Automated rewrites

The skill ships with `scripts/bash-shorten.py` — a zero-dependency Python
rewriter for the highest-confidence patterns. It defaults to dry-run
(prints a unified diff + per-rule counts to stderr) and only writes when
you pass `--apply`. Always run `shellcheck` on the output afterwards.

```bash
# preview rewrites
python3 scripts/bash-shorten.py path/to/script.sh

# apply (atomic write, preserves the file in place)
python3 scripts/bash-shorten.py --apply path/to/script.sh

# only run a subset of rules
python3 scripts/bash-shorten.py --rules basename,dirname,backticks script.sh

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

**19 rules across 2 groups, 31 embedded test cases** (positive + negative).
The `core` group has 16 rules, all on by default; the `modernize` group
has 3 rules, off by default. 11 of the core rules mirror source-article
examples (`basename`, `dirname`, `sed-replace-first/all`, `echo-wc-c`,
`expr-arith-vars/literal`, `combined-tests`, `test-numeric`,
`empty-default`, `mkdir-guard`); 5 are bonus core patterns the article
doesn't cover but are obvious wins (`backticks`, `legacy-null-check`,
`empty-string-eq`, `find-exec-rm-delete`, `cat-file-pipe-grep` — three
of which shellcheck flags but doesn't auto-fix).

**What the rewriter deliberately *can't* do**: anything that needs data
flow analysis (single-use variable inlining), multi-statement detection
(temp-file → pipeline), function extraction, parallelization, or
behavioral judgment ("is this `&&`/`||` chain safe?"). Those are
hand-edits guided by the references.

When a user asks "can you shorten this whole script?", run the
rewriter first to clear the obvious patterns, *then* walk through the
remaining bloat by hand using the cheatsheet. Don't skip the rewriter —
it eliminates the boring 60% of the work.

### Rule groups

| Group | Default | Contents |
|---|---|---|
| `core` | on | 16 idiomatic-bash rewrites that don't change tooling |
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
on PATH. Structural patterns (`basename`, `dirname`, `backticks`) route
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

Or skip the script and invoke `/bash-shortening` directly in Claude Code
— the methodology in this file is the fallback for environments without
ast-grep.

**Why some rules are still regex-only.** tree-sitter-bash flattens a lot
of structure:

- `mkdir-guard`, `empty-default` need ARG1 == ARG2 cross-metavariable
  equality to skip mismatched-var cases. ast-grep can't express that
  constraint cleanly; the Python regex uses a backreference instead.
- `combined-tests` — `[ ... ]` (`test_command`) flattens into a list of
  word tokens, so structural matching on the operator side is brittle.
- `test-numeric` — same flattening, plus the operator map (`-gt → >`)
  needs lambda-style replacement that YAML rules can't express.
- `sed-replace-*` — the literal-pattern guard needs character-class
  restrictions in the matcher.

These regex-only rules still run after the ast-grep pass.

## Quick wins

The most common bloat patterns and where to read the full treatment.
Numbers in parens are the example numbers from the source article.

| Verbose form | Idiomatic form | Reference |
|---|---|---|
| `if [ -z "$X" ]; then X=default; fi` | `X=${X:-default}` | parameter-expansion (8) |
| `$(echo "$S" \| cut -c1-5)` | `${S:0:5}` | parameter-expansion (10) |
| `$(basename "$P")` / `$(dirname "$P")` | `${P##*/}` / `${P%/*}` | parameter-expansion (11-12) |
| `$(echo "$S" \| sed 's/a/b/g')` | `${S//a/b}` | parameter-expansion (13-14) |
| `$(echo -n "$S" \| wc -c)` | `${#S}` | parameter-expansion (15) |
| `$(expr $A + $B)` / `$(expr $C + 1)` | `$((A + B))` / `((C++))` | arithmetic (32-34) |
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

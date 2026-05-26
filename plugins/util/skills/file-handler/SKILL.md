---
name: file-handler
description: >
  Persist, fetch, and search local artifacts produced by skills under a
  predictable `.skillz/<type>/<slug>` tree. Use whenever a skill needs
  to save scratch files, plans, notes, or other working artifacts to
  disk and look them up again in a later turn — for example when a
  user says "save this as <slug>", "where did we save the <type>",
  "find the <slug> we saved", or any time another skill in this repo
  references "the file-handler". Wraps a single dependency-free Bash
  script (`scripts/skillz.sh`) that exposes `save_file`, `get_file`,
  and `search_files` (titles + body grep). All other skills should
  delegate to this skill instead of inventing their own on-disk
  layout. Do NOT use for source code edits, version-controlled files,
  or anything that belongs in the project tree — this is for skill
  artifacts, not application files.
license: MIT
---

# file-handler

One on-disk convention for every skill in this repo:

```text
.skillz/
└── <type>/
    └── <slug>
```

`<type>` groups artifacts by purpose (`note`, `plan`, `draft`, `log`,
…). `<slug>` is the filename. Both are validated — no path separators,
no `..`, no leading dots — so a skill can hand a user-supplied slug
straight through without sanitizing.

## How to use this skill

When another skill needs to save or load a working file, shell out to
`scripts/skillz.sh`. Three subcommands cover the surface area:

```bash
# write content to .skillz/<type>/<slug>
scripts/skillz.sh save_file note todo "things to do"

# write stdin (preferred for multi-line content)
some_command | scripts/skillz.sh save_file plan release-cut

# read it back
scripts/skillz.sh get_file note todo

# search by title and body, optionally narrowed to a type
scripts/skillz.sh search_files todo
scripts/skillz.sh search_files release --type plan
```

`save_file` echoes the resolved path on success so callers can quote
it back to the user. `get_file` and `search_files` exit 1 when the
target / matches don't exist, so a skill can branch on `||`.

## Storage root

By default the tree lives at `$PWD/.skillz`. Override with `SKILLZ_DIR`
when you want a session-scoped or repo-scoped location:

```bash
SKILLZ_DIR="$HOME/.local/share/skillz" scripts/skillz.sh save_file note hello "hi"
```

Add `.skillz/` to the repo's `.gitignore` if these artifacts shouldn't
be committed.

## Conventions for callers

- **Pick a type once and reuse it.** Don't invent a new `<type>` per
  artifact — types are buckets (`note`, `plan`, `draft`, `log`,
  `transcript`). Slugs are the unique part.
- **Slugs are ASCII filename-safe.** Allowed: `[A-Za-z0-9._-]`
  (letters, digits, dot, underscore, hyphen — kebab-case is the
  preferred style, but dots for extensions and uppercase are
  accepted). Anything else gets rejected with exit 2.
- **No extension is required**, but adding one (`.md`, `.txt`, `.json`)
  helps when a user opens the file directly.
- **Prefer stdin over the inline content arg** for anything multi-line
  — quoting gets fragile fast.
- **Don't write through this skill for tracked source files.** This is
  for skill scratch space, not the project tree.

## Subcommand reference

### `save_file <type> <slug> [content]`

Writes to `.skillz/<type>/<slug>`. If `content` is omitted, reads from
stdin. Parent directories are created on demand. Prints the resolved
path. Exit 2 on bad args.

### `get_file <type> <slug>`

Prints `.skillz/<type>/<slug>` to stdout. Exit 1 if the file is
missing, exit 2 on bad args.

### `search_files <query> [--type <type>]`

Two-pass search:

1. **Titles** — case-insensitive `find -iname "*<query>*"` across the
   storage root (or one type if `--type` is supplied).
2. **Bodies** — `grep -rIn` for the same query against the same scope.
   Binary files are skipped.

Output is sectioned (`## titles` then `## bodies`); body hits are in
the standard `<path>:<line>:<text>` form so editors can jump to them.
Exit 1 when neither pass finds anything.

## Why a single skill for this

Without a shared convention, each skill that needs to persist working
state ends up inventing its own (`~/.cache/<skill>/`, `/tmp/<skill>-*`,
a hidden file in `$PWD`, …). That fragments where users go to find
their stuff and makes cross-skill workflows harder ("the plan we drafted
last turn"). Centralizing on `.skillz/<type>/<slug>` plus three verbs
keeps the rest of the toolkit tiny.

## When NOT to use this skill

- **Application code, configs, or anything tracked in git.** Edit those
  in place — this skill is for skill scratch space.
- **Secrets.** `.skillz/` is plaintext on local disk. Use a secret
  manager (1Password, Bitwarden, age) instead.
- **Cross-machine sync.** Files live on the local filesystem only. If
  you need them elsewhere, copy them out by hand or commit deliberately.
- **Large binaries.** The body-grep pass skips binary files, but the
  storage layout isn't tuned for big artifacts.

## Gotchas

- **`SKILLZ_DIR` is captured at invocation, not export-once.** Each
  shell-out re-resolves it, so changing the env between calls changes
  the storage root.
- **Slug validation rejects spaces.** Pass `release-cut`, not
  `release cut`. The regex is documented above.
- **Body grep uses fixed-string matching (`grep -F`).** Special
  characters (`.`, `*`, `[`) are matched literally. That's deliberate
  — users searching for `v1.2.3` get exactly what they typed, not a
  regex.
- **No locking.** Two skills writing the same `<type>/<slug>`
  concurrently will race; last writer wins. Use distinct slugs for
  parallel work.

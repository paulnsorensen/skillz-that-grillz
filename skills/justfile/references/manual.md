# The just manual — best parts, special use cases, and link tree

Primary source: **[Just Programmer's Manual](https://just.systems/man/en/introduction.html)**.

The manual is a multi-page mdbook — **each section is its own `.html` page**
under `https://just.systems/man/en/` (e.g. `recipe-parameters.html`,
`dependencies.html`). There is no `recipes.html`; "recipes" content is split
across the chapters linked below. The full chapter index is in the
[Link tree](#link-tree) at the bottom.

Use this file to ground *why* a justfile is worth adding and *which* features
to reach for first. Per-feature syntax lives in `SKILL.md`.

> **`just` ships its own agent skill** —
> [Skill for Agents](https://just.systems/man/en/skill-for-agents.html). Worth
> citing when an agent asks "how should I use just" rather than "how do I
> write a justfile."

## Why just is worth it — the official feature list

Straight from the manual's [Introduction](https://just.systems/man/en/introduction.html)
and [Features](https://just.systems/man/en/features.html) — the "ton of useful
features and many improvements over make":

1. **Command runner, not a build system** — sidesteps make's complexity. No
   `.PHONY`, no file-target machinery.
2. **Cross-platform, zero deps** — Linux, macOS, Windows and other unixes work
   out of the box (needs an `sh`, or pick another shell via `set shell`).
3. **Specific, informative errors** — syntax errors reported with source context.
4. **Recipes accept command-line arguments** — first-class parameters.
5. **Static error resolution** — unknown recipes and circular dependencies are
   reported *before anything runs*.
6. **Loads `.env` files** — `set dotenv-load` populates env vars for free.
7. **Recipes are listable** — `just --list` is self-documenting discovery.
8. **Shell completion** — scripts for most popular shells.
9. **Recipes in arbitrary languages** — Python, Node, Ruby, etc. inline.
10. **Invoke from any subdirectory** — not just the dir holding the `justfile`.

## Most-used features (day-to-day) — deep links

| Feature | Why it's used constantly | Chapter |
|---|---|---|
| Recipes + dependencies | `test: build` runs `build` first; deps always run first; fail-fast on first error | [Dependencies](https://just.systems/man/en/dependencies.html) |
| `just --list` | Doc-comment-driven discovery — why every public recipe gets a `#` comment | [Listing Available Recipes](https://just.systems/man/en/listing-available-recipes.html) |
| Parameters + defaults | `test filter="":`, variadic `run *args:` | [Recipe Parameters](https://just.systems/man/en/recipe-parameters.html) |
| `set dotenv-load` | Canonical replacement for `-include .env` | [Settings § dotenv](https://just.systems/man/en/settings.html#dotenv-settings) |
| Doc comments | The text `--list` displays | [Documentation Comments](https://just.systems/man/en/documentation-comments.html) |
| Aliases | One-letter aliases for hot-path recipes | [Aliases](https://just.systems/man/en/aliases.html) |
| `@` quiet prefix | Suppress the per-line command echo | [Quiet Recipes](https://just.systems/man/en/quiet-recipes.html) |
| Variables / `os()` / backticks | `VERSION := "…"`, ``GIT_HASH := `…` `` | [Variables and Assignments](https://just.systems/man/en/variables-and-assignments.html) |
| The default recipe | First recipe runs on bare `just` | [The Default Recipe](https://just.systems/man/en/the-default-recipe.html) |

## Best special use cases — what makes justfiles truly worth it

| Use case | Payoff | Chapter |
|---|---|---|
| Polyglot recipes | Write a recipe body in Python/Node/Ruby — one runner holds shell *and* scripting tasks | [Shebang Recipes](https://just.systems/man/en/shebang-recipes.html) · [Script Recipes](https://just.systems/man/en/script-recipes.html) |
| Python via uv | `uv`-managed deps inside a recipe | [Python Recipes with uv](https://just.systems/man/en/python-recipes-with-uv.html) |
| Safer bash | `set -euxo pipefail` shebang pattern | [Safer Bash Shebang Recipes](https://just.systems/man/en/safer-bash-shebang-recipes.html) |
| Run from any subdir | `just test` works anywhere in the repo | [Invoking justfiles in Other Directories](https://just.systems/man/en/invoking-justfiles-in-other-directories.html) |
| Monorepo split | `mod api`, `mod web`; recipes depend on `api::build` | [Modules](https://just.systems/man/en/modules.html) · [Imports](https://just.systems/man/en/imports.html) |
| Cross-platform branching | `[macos]`/`[linux]`/`[windows]` + `os()` — one justfile per machine | [Attributes](https://just.systems/man/en/attributes.html) · [Conditional Expressions](https://just.systems/man/en/conditional-expressions.html) |
| Safety & visibility attrs | `[confirm("…")]` gates destructive recipes; `[private]` hides helpers | [Private Recipes](https://just.systems/man/en/private-recipes.html) · [Groups](https://just.systems/man/en/groups.html) |
| Watch / re-run on change | Re-run a recipe when files change | [Re-running recipes when files change](https://just.systems/man/en/re-running-recipes-when-files-change.html) |
| Parallel recipes | Run independent recipes concurrently | [Parallelism](https://just.systems/man/en/parallelism.html) |
| Fallback to parent | Walk up to a parent justfile for shared recipes | [Fallback to parent justfiles](https://just.systems/man/en/fallback-to-parent-justfiles.html) |
| `just`-as-interpreter | `#!/usr/bin/env just --justfile` scripts | [Just Scripts](https://just.systems/man/en/just-scripts.html) |

## When NOT to cite just as the answer

The manual is explicit: just is a *command runner*, not a build system. Don't
present it as a replacement for cargo/webpack/go build or for make's real
file-target dependency tracking — it wraps those, it doesn't replace them. See
[idiosyncrasies of Make that just avoids](https://just.systems/man/en/what-are-the-idiosyncrasies-of-make-that-just-avoids.html)
and [Just vs Cargo build scripts](https://just.systems/man/en/whats-the-relationship-between-just-and-cargo-build-scripts.html).

## Link tree

Top-level chapters (the manual's own sidebar carries the full nested index on
every [just.systems/man](https://just.systems/man/en/introduction.html) page):

- [Introduction](https://just.systems/man/en/introduction.html)
  - [Installation](https://just.systems/man/en/installation.html) — prerequisites, packages, pre-built binaries, GitHub Actions, Docker, Node.js, Nix
  - [Backwards Compatibility](https://just.systems/man/en/backwards-compatibility.html)
  - [Editor Support](https://just.systems/man/en/editor-support.html) — Vim/Neovim, Emacs, VS Code, JetBrains, Helix, Zed, LSP, MCP
  - [Quick Start](https://just.systems/man/en/quick-start.html)
  - [Examples](https://just.systems/man/en/examples.html)
  - **[Features](https://just.systems/man/en/features.html)** — the meat: default recipe, listing, working dir, aliases, settings, doc comments, variables, expressions, strings, functions, constants, attributes, groups, conditionals, parameters, dependencies, shebang/script recipes, modules, imports, private/quiet recipes, chooser, formatting, fallback, shell config, signals
  - [Changelog](https://just.systems/man/en/changelog.html)
  - [Miscellanea](https://just.systems/man/en/miscellanea.html) — watch, parallelism, shell alias, completion, man page, grammar, global/user justfiles, remote justfiles, **Skill for Agents**
  - [Contributing](https://just.systems/man/en/contributing.html)
  - [Frequently Asked Questions](https://just.systems/man/en/frequently-asked-questions.html)
  - [Further Ramblings](https://just.systems/man/en/further-ramblings.html)

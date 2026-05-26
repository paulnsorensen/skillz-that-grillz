---
name: justfile
model: haiku
description: >
  Create or migrate to a justfile (casey/just command runner) for any project.
  Use this skill when the user asks to add a justfile, replace a Makefile,
  set up project commands, create task runners, or mentions "just" in the
  context of build/dev workflows. Also trigger when you see a project with
  a Makefile that would benefit from just's simpler syntax, or when setting
  up a new project that needs common dev commands (build, test, lint, fmt).
  Covers Rust, Python, TypeScript/JavaScript, Go, and Ruby ecosystems.
  Do NOT use for CI pipeline configuration, Dockerfiles, or actual build system setup (cargo, webpack, etc.).
license: MIT
---

# justfile

Generate idiomatic justfiles for any project. Replace Makefiles and ad-hoc
shell scripts with a clean, discoverable command runner.

## Why just over Make

- No `.PHONY` hacks — all recipes are commands, not file targets
- No tab-indentation gotchas — any whitespace works
- First-class parameters, dotenv loading, OS detection, and modules
- `just --list` gives instant discoverability with doc comments
- Shebang recipes let you write Python/Ruby/Node inline

## Protocol

### 1. Detect the project

Scan the project root for ecosystem markers:

| File | Ecosystem | Reference |
|------|-----------|-----------|
| `Cargo.toml` | Rust | `references/rust.md` |
| `pyproject.toml`, `setup.py`, `uv.lock` | Python | `references/python.md` |
| `package.json` | TypeScript/JS | `references/typescript.md` |
| `go.mod` | Go | `references/go.md` |
| `Gemfile` | Ruby | `references/ruby.md` |

Read the relevant reference file for language-specific recipes.

If multiple markers exist (e.g., a Rust backend + TypeScript frontend), combine
patterns. Use modules (`mod frontend`, `mod backend`) for true monorepos.

**Multi-ecosystem naming:** When a project has multiple languages (e.g., Tauri
with Rust + TypeScript), use ecosystem suffixes to disambiguate overlapping
concerns:

- `test-rust`, `test-ts` (not generic `test` that hides what runs)
- `fmt-rust`, `fmt-ts` (each ecosystem's formatter)
- `lint-rust` (clippy), `lint-ts` (eslint/biome)
- Aggregate recipes combine them: `test: test-rust test-ts`, `fmt: fmt-rust fmt-ts`
- Shared recipes that span both ecosystems keep plain names: `dev`, `build`, `clean`

### 2. Check for existing build files

Look for `Makefile`, `Taskfile.yml`, `Rakefile`, `package.json` scripts, or
shell scripts in `scripts/` or `bin/`. If found:

- **Migrate**: Translate existing targets to just recipes (see migration table below)
- **Preserve**: Keep any complex logic that just can't replace (e.g., Make's
  file-target dependency tracking for actual build artifacts)
- **Remove**: Delete the old file only after confirming with the user

### 3. Write the justfile

Place `justfile` in the project root. Follow these conventions:

**Structure order:**

1. Settings (`set dotenv-load`, `set shell`, etc.)
2. Variables (version, binary name, etc.)
3. Default recipe (first recipe — either `default: check` or `@just --list`)
4. Core recipes grouped by concern: build, test, lint/fmt, run, deploy
5. Utility recipes (clean, docs, etc.)
6. Private helpers (`_prefixed` or `[private]`)

**Recipe naming:**

- Use kebab-case: `test-coverage`, `build-release`
- Use verbs: `build`, `test`, `lint`, `deploy` (not `builder`, `tests`)
- Group with prefixes for large files: `db-migrate`, `db-seed`, `db-reset`
- Default recipe should be the most common action or `--list`

**Doc comments:**
Every public recipe gets a comment on the line above it — this is what
`just --list` displays:

```just
# Run the full test suite
test *args:
    cargo test {{args}}
```

**Parameters:**

- Use defaults for optional args: `test filter=""`
- Use variadic for passthrough: `run *args`
- Use `+args` (1+ required) sparingly
- For ergonomic CLI flags (`just build --target x86_64`), use the
  `[arg()]` attribute (v1.46+ — see "Recipe argument flags" below)

**Aliases:** Add a one-letter alias for any recipe a developer will
type more than a few times a day. Define it on the line below the
recipe so `just --list` shows the canonical name first:

```just
# Run tests
test *args:
    cargo test {{args}}
alias t := test
```

Don't manufacture aliases for every recipe — only the hot path.

**Settings to always include:**

```just
set dotenv-load
set shell := ["bash", "-euo", "pipefail", "-c"]
```

`-euo pipefail` makes shell recipes fail loudly on any error, unset
variable, or broken pipe — the same posture as a well-written bash
script. If a recipe needs softer behavior, use `-` line prefix to
ignore errors on a single command, not weaker shell flags. Skip the
`set shell` line for Windows targets or projects without a bash
dependency — the default `/bin/sh` is fine for most recipes.

Add `set unstable` if you use modules (`mod`), the `[script]`
attribute, or `[arg()]` flags (see below).

### Tool dependencies — `require()`

When a recipe depends on a tool that isn't a standard system command,
declare it at the top of the justfile so `just --evaluate` fails fast
when it's missing:

```just
# https://github.com/jqlang/jq
jq := require("jq")

# https://github.com/casey/just (yes, just itself)
just := require("just")
```

Use `{{ jq }}` to invoke through the resolved path, or call `jq`
normally — `require()` has already confirmed it exists at evaluate
time, so either form works. The URL comment doubles as the install
hint. Skip `require()` for ubiquitous tools like `git`, `curl`, or
the language toolchain — declare only what a fresh machine might be
missing.

### 4. Token-optimized output (for LLM-driven builds)

When recipes run inside an LLM agent's context, output verbosity = token cost.
Three universal levers, then escalate to `references/rtk.md` if needed:

1. **`@` prefix + tool flags** (~60 → ~15 lines). Silences recipe echo; pass
   `--silent --no-audit --no-fund` to npm to drop banner/audit/fund noise.

2. **Skip coverage in the default recipe.** Coverage tables are ~15 lines.
   Use `npm test` (or `cargo test`) in `build`; keep `test:coverage` in
   `build-ci` where the CI logs aren't paying per-token.

3. **rtk wrappers** (deterministic per-tool filters + hard-gate on failure).
   See `references/rtk.md` for the shell-wrap pattern, `rtk test`/`rtk err`
   gates, and the npm script-naming gotcha.

### 5. Update project docs

After creating the justfile:

**Agent context file** (e.g. `AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, or whatever convention the harness uses) — Add a "Key Commands" or "Common Tasks" section:

```markdown
## Key Commands

This project uses [just](https://github.com/casey/just) as its command runner.
Run `just` to see all available recipes.

- `just` — List all available commands
- `just test` — Run tests
- `just lint` — Run linters
- `just fmt` — Format code
```

Only list the 4-6 most important recipes. Point to `just --list` for the rest.

**README.md** — Add a "Development" or "Getting Started" section:

```markdown
## Development

### Prerequisites
- [just](https://github.com/casey/just) — `brew install just` / `cargo install just`

### Quick Start
```bash
just install   # Install dependencies
just test      # Run tests
just           # See all available commands
```

```

Don't duplicate the full recipe list — `just --list` is self-documenting.

## Makefile Migration Table

| Makefile | justfile |
|----------|----------|
| `.PHONY: target` | (not needed) |
| `$(VAR)` | `{{var}}` |
| `$(shell cmd)` | `` `cmd` `` |
| `-include .env` | `set dotenv-load` |
| `ifeq ($(OS),Darwin)` | `if os() == "macos" { ... }` |
| `ifndef VAR` / `$(or ...)` | `env_var_or_default("VAR", "default")` |
| `make -C subdir` | `mod subdir` |
| `$(MAKE) target` | `just target` |
| `.DEFAULT_GOAL := help` | First recipe is default |
| `@cmd` (suppress echo) | `@cmd` (same) |
| Tab indentation | Any whitespace |
| `%:` pattern rules | Not applicable — just has no file targets |

## Key Syntax Reference

**Variables:**
```just
VERSION := "1.0.0"
GIT_HASH := `git rev-parse --short HEAD`
DB_URL := env_var_or_default("DATABASE_URL", "postgres://localhost/dev")
OPEN := if os() == "macos" { "open" } else { "xdg-open" }
```

**Recipe attributes:**

```just
[confirm("Deploy to production?")]
deploy: build test

[macos]
open-docs:
    open target/doc/index.html

[linux]
open-docs:
    xdg-open target/doc/index.html

[private]
_setup:
    mkdir -p tmp/

# Group recipes in `just --list` output
[group("checks")]
lint:
    cargo clippy

[group("checks")]
fmt:
    cargo fmt

# Multiple attributes — same line (comma-separated) or stacked
[group("dev"), no-cd]
status:
    git status
```

**Recipe argument flags (v1.46+):** Use `[arg()]` to expose a
parameter as a CLI option instead of a positional arg. Best for
recipes a human runs at the prompt:

```just
[arg("target", long, help="Build target architecture")]
[arg("release", long, value="true", help="Build in release mode")]
build target release="false":
    cargo build --target {{target}} {{ if release == "true" { "--release" } else { "" } }}
```

Usage: `just build --target x86_64 --release`. Run `just --usage build`
to see the generated help.

**Script blocks (`[script]`):** Cleaner than shebang recipes and
preferred when the body has shell control flow you don't want
re-evaluated line-by-line. Requires `set unstable`:

```just
[script("bash")]
deploy env:
    set -e
    case {{env}} in
        prod) URL="https://prod.example.com" ;;
        stage) URL="https://stage.example.com" ;;
        *) echo "unknown env: {{env}}"; exit 1 ;;
    esac
    curl -X POST "$URL/deploy"

[script("python3")]
analyze:
    import json
    print(json.load(open("results.json"))["total"])
```

`[script()]` is preferred over `#!/usr/bin/env` shebangs for
cross-platform portability and so the whole body runs as one block.

**Built-in color constants:** Available globally without definition —
`RED`, `GREEN`, `YELLOW`, `BLUE`, `CYAN`, `MAGENTA`, plus `BOLD`,
`UNDERLINE`, and `NORMAL` to reset. Useful for status output in
multi-step recipes:

```just
@check:
    cargo check
    echo -e '{{ GREEN }}✓ check passed{{ NORMAL }}'

@fail-loudly:
    echo -e '{{ BOLD + RED }}deploy aborted{{ NORMAL }}'
```

Don't sprinkle color through every recipe — reserve it for status
lines that summarize an aggregate step.

**Modules (monorepo):**

```just
mod api        # looks for api/justfile or api.just
mod web
mod? local     # optional — no error if missing

# Usage: just api::test, just web::build
```

**Shebang recipes (multi-line scripts, no `set unstable` needed):**

```just
analyze:
    #!/usr/bin/env python3
    import json
    data = json.load(open("results.json"))
    print(f"Total: {len(data)}")
```

Prefer `[script("python3")]` over shebangs on just ≥ 1.34 with
`set unstable` — it runs the body as one block and avoids
shebang-portability quirks.

## Anti-patterns

- Don't recreate Make's file-target system — just is a command runner, not a build system
- Don't use `set positional-arguments` unless you have a strong reason — `{{arg}}` is clearer
- Don't put secrets in justfiles — use dotenv or env vars
- Don't write 200-line justfiles — use modules (`mod`) to split by concern
- Don't duplicate CI pipeline steps 1:1 — group them into meaningful recipes like `check` or `ci`

## What You Don't Do

- Design CI pipelines or GitHub Actions workflows
- Create Dockerfiles or container configs
- Replace actual build systems (cargo, webpack, go build) — just wraps them
- Remove existing Makefiles without user confirmation

## Gotchas

- `just` binary may not be on PATH — check with `which just` before generating recipes
- Shebang recipes need explicit `#!/usr/bin/env` for portability across systems
- `dotenv-load` exposes all env vars to all recipes — avoid for secrets-heavy projects
- Module paths are relative to the justfile location, not the working directory
- `set positional-arguments` changes how `$1` works inside recipes — document when used
- `require("tool")` validates at evaluate time — use `{{ jq }}` to
  invoke via resolved path, or call `jq` directly; both work once
  `require` has confirmed the tool exists
- `[script()]` and modules require `set unstable` and just ≥1.34 —
  pin a minimum in your README if you rely on them
- `[arg()]` requires `set unstable` and just ≥1.46 — pin v1.46+ if
  you expose argument flags

# Copilot setup — generate `.github/` instructions

Bootstrap a repository with GitHub Copilot agent and review instructions
aligned to a project's engineering principles. This is a **one-time**
operation per repo (or whenever the principles change). It is loaded from
the parent `SKILL.md` only when the user explicitly asks for setup.

The output is a set of files under `.github/`:

- `.github/copilot-instructions.md` — global instructions read by both the
  coding agent and the review agent.
- `.github/instructions/code-review.instructions.md` — review-only rules.
- `.github/instructions/coding-agent.instructions.md` — coding-agent-only rules.
- `.github/instructions/<language>.instructions.md` — language-specific rules
  for each detected project type.

## 1. Detect project type

Check for indicator files in the project root. A project may match more
than one type. If none match, generate only the global instructions and the
review/coding-agent files.

| Indicator | Type | Language extensions |
| --- | --- | --- |
| `package.json` | node | `*.ts`, `*.tsx`, `*.js`, `*.jsx` |
| `pyproject.toml` or `setup.py` or `uv.lock` | python | `*.py` |
| `Cargo.toml` | rust | `*.rs` |
| `go.mod` | go | `*.go` |
| `Gemfile` | ruby | `*.rb` |
| `.brew` or `zshrc` or `zsh/` dir | dotfiles | `*.sh`, `*.zsh` |

## 2. Detect existing tooling

Note which CI tools are already configured — they are listed in the
review instructions as "do not comment on; already handled by CI".

| File / config | Tool |
| --- | --- |
| `.eslintrc*` or `eslint.config.*` | ESLint |
| `.prettierrc*` or `prettier.config.*` | Prettier |
| `ruff.toml` or `[tool.ruff]` in `pyproject.toml` | Ruff |
| `mypy.ini` or `[tool.mypy]` in `pyproject.toml` | mypy |
| `rustfmt.toml` or `.rustfmt.toml` | rustfmt |
| `clippy.toml` or `.clippy.toml` | Clippy |
| `.golangci.yml` or `.golangci.yaml` | golangci-lint |
| `.rubocop.yml` | RuboCop |
| `shellcheck` in any CI config | ShellCheck |

## 3. Check for existing files

Before writing, check whether `.github/copilot-instructions.md` or any
`.github/instructions/*.instructions.md` already exist. If they do, **ask
the user** whether to overwrite or skip.

## 4. Create directory structure

```bash
mkdir -p .github/instructions
```

## 5. Generate global instructions

Write `.github/copilot-instructions.md` with the content below. Adapt the
**Tech Stack** and **Build, Test, and Lint Commands** sections from what
the detector found in step 1 (read `package.json` scripts, `pyproject.toml`
scripts, `Makefile` targets, `Cargo.toml`, etc.).

```markdown
# Copilot Instructions

## Engineering Principles

1. **Input Validation** - Trust nothing from external sources. Validate at system boundaries (user input, external APIs, file I/O). Internal code trusts internal code.
2. **Fail Fast and Loud** - Handle errors where they occur. No silent failures, no swallowed exceptions, no empty catch blocks. If something fails, the caller should know immediately.
3. **Loose Coupling** - Separate business logic from infrastructure. Core models and domain logic must not import HTTP frameworks, ORMs, or I/O libraries. Use dependency injection or protocols/interfaces at boundaries.
4. **YAGNI** - Build only what is needed now. No abstract base classes with one implementation, no plugin systems with one plugin, no configuration options that are never varied. If it is needed later, it can be written later.
5. **Real-World Models** - Name things after business concepts, not technical abstractions. `Order`, not `DataProcessor`. `PricingRule`, not `StrategyHandler`.
6. **Immutable Patterns** - Minimize state mutation. Prefer pure functions, return new values instead of mutating arguments, use immutable data structures where the language supports them.

## Complexity Budget

- **Functions**: Maximum 40 lines
- **Files**: Maximum 300 lines
- **Parameters**: Maximum 4 per function
- **Nesting**: Maximum 3 levels deep

If a function or file exceeds these limits, decompose it.

## Code Style

- **Classes**: PascalCase
- **Functions**: snake_case (Python, Ruby, Rust) / camelCase (JS/TS, Go exported)
- **Constants**: SCREAMING_SNAKE_CASE
- **Files**: kebab-case
- **Commits**: Conventional Commits format (`feat:`, `fix:`, `chore:`, etc.)

## Architecture

Follow vertical slice architecture:

- Each domain concept gets its own module/directory.
- Public API is exposed through an index/barrel file only.
- Do not reach into another slice's internals — import from its public API.
- Core models stay pure: no ORM decorators, no framework imports, no I/O.
- `common/` or `shared/` is a leaf — it imports nothing from sibling domains.
- One-directional dependencies only; use events for reverse communication.

**Growth pattern:**

1. Start with one file per concept.
2. Extract a sibling file when the original gets crowded.
3. When a file needs helpers, it becomes a facade with a subdirectory.

## What NOT to Do

- Do not add docstrings to private methods or small helpers with clear names.
- Do not create abstract base classes, factories, or registries unless there are multiple concrete implementations today.
- Do not add error handling for conditions that cannot occur in the current system.
- Do not add backwards-compatibility shims — change the code directly.
- Do not wrap functions that add no logic — call the original directly.
- Do not add type annotations to every local variable — annotate function signatures and let inference handle the rest.

## Tech Stack

{REPLACE_WITH_DETECTED_STACK}

## Build, Test, and Lint Commands

{REPLACE_WITH_DETECTED_COMMANDS}
```

If the detector cannot determine a stack or command set, **remove the
relevant section** rather than guessing or leaving placeholders behind.

## 6. Generate code review instructions

Write `.github/instructions/code-review.instructions.md`:

```markdown
---
applyTo: "**"
excludeAgent: "coding-agent"
---

## Code Review Focus

Focus reviews on these categories, in priority order:

1. **Security** - Flag hardcoded secrets, SQL injection, XSS, command injection, path traversal, and insecure deserialization.
2. **Silent failures** - Flag empty catch blocks, swallowed errors, missing error propagation, or functions that return null/undefined on failure without signaling.
3. **Coupling violations** - Flag domain/model code that imports infrastructure (HTTP, DB, file I/O, framework decorators).
4. **Complexity violations** - Flag functions over 40 lines, files over 300 lines, functions with more than 4 parameters, nesting deeper than 3 levels.
5. **Architectural violations** - Flag cross-slice internal imports, mutable shared state, God classes/modules.
6. **Weak tests** - Flag tests that prove nothing: no assertions, `is_ok()` / `is_some()` without checking inner values, tautological assertions, mirror-implementation tests that re-derive expected values from the same logic, mock echo tests, `.len()`-only collection checks, assertions gated behind conditional branches that silently pass, and happy-path-only coverage with no error/boundary tests.

## What NOT to Comment On

{REPLACE_WITH_CI_TOOLS_LIST}
- Import ordering — handled by tooling.
- Formatting and whitespace — handled by tooling.
- Missing docstrings on internal or private functions.
- Style preferences that are consistent with the rest of the codebase.
- Nitpicks with no functional impact.

## Review Style

- Only comment when confidence is high.
- If a pattern is used consistently elsewhere in the codebase, do not flag it as wrong.
- Suggest specific fixes, not vague improvements.
- One comment per issue — do not repeat the same feedback on multiple occurrences.
```

Replace `{REPLACE_WITH_CI_TOOLS_LIST}` with one line per detected tool, e.g.
`- Linting — handled by ESLint in CI`. If no tools were detected, remove
the placeholder line and keep the generic ones.

## 7. Generate language-specific instructions

Write only the files for the project types detected in step 1. Skip
anything not present.

### Python — `.github/instructions/python.instructions.md`

```markdown
---
applyTo: "**/*.py"
---
- Use type hints on all function signatures and return types.
- Use `uv` for dependency management, not pip directly.
- Use pytest for tests, not unittest.
- Use Ruff for linting and formatting.
- Prefer dataclasses or Pydantic models over raw dicts for structured data.
- Use `from __future__ import annotations` for forward references.
- Raise specific exceptions, never bare `raise` or `raise Exception`.
```

### Node / TypeScript — `.github/instructions/typescript.instructions.md`

```markdown
---
applyTo: "**/*.{ts,tsx,js,jsx}"
---
- Use TypeScript strict mode.
- Prefer `interface` over `type` for object shapes that may be extended.
- Use `const` by default, `let` only when reassignment is necessary, never `var`.
- Prefer named exports over default exports.
- Use async/await over raw Promises or callbacks.
- Handle errors explicitly — no unhandled promise rejections.
- Prefer immutable array methods (`map`, `filter`, `reduce`) over mutating loops.
```

### Rust — `.github/instructions/rust.instructions.md`

```markdown
---
applyTo: "**/*.rs"
---
- Use `thiserror` for library errors, `anyhow` for application errors.
- Prefer `impl Trait` in argument position over generics when there is one caller.
- Use `clippy::pedantic` level lints.
- Prefer owned types in public APIs unless borrowing is clearly beneficial.
- Use `#[must_use]` on functions that return values meant to be consumed.
```

### Go — `.github/instructions/go.instructions.md`

```markdown
---
applyTo: "**/*.go"
---
- Always handle errors — never use `_` to discard an error.
- Use table-driven tests.
- Prefer returning errors over panicking.
- Use `context.Context` as the first parameter for functions that do I/O.
- Keep interfaces small — one or two methods.
- Define interfaces where they are used, not where they are implemented.
```

### Shell / Dotfiles — `.github/instructions/shell.instructions.md`

```markdown
---
applyTo: "**/*.{sh,zsh,bash}"
---
- Use `set -euo pipefail` at the top of scripts.
- Quote all variable expansions: `"$var"`, not `$var`.
- Use `[[ ]]` over `[ ]` for conditionals.
- Use functions for any logic that repeats or exceeds 10 lines.
- Use `local` for function variables.
- Prefer `printf` over `echo` for portable output.
```

## 8. Generate coding agent instructions

Write `.github/instructions/coding-agent.instructions.md`:

```markdown
---
applyTo: "**"
excludeAgent: "code-review"
---

## Coding Agent Guidelines

When implementing changes:

- Read existing code before modifying it — understand the patterns in use.
- Follow the existing code style of the file you are editing.
- Keep changes minimal and focused on the issue at hand.
- Do not refactor surrounding code unless the issue requires it.
- Do not add docstrings to helper functions or private methods with clear names.
- Do not introduce new dependencies without explicit approval.
- Write tests for new functionality — match the existing test patterns in the project.
- Prefer editing existing files over creating new ones.

## Architecture Rules

- New domain concepts go in their own module under the appropriate domain directory.
- Public API is exposed through index/barrel files only.
- Do not create abstract classes, interfaces, or factories unless there are 2+ concrete implementations.
- Core models must not import infrastructure code.
```

## 9. Update `.gitattributes` (optional)

If `.gitattributes` does not already exist, ask the user whether to create
one with `linguist-generated` markers for generated files. Do not create it
unprompted.

## 10. Print summary

After writing all files, print a one-shot summary so the user can sanity
check what landed:

```text
Copilot configuration generated:
  Project types detected: python, node
  CI tools detected: ESLint, Prettier, Ruff
  Files created:
    .github/copilot-instructions.md          (global)
    .github/instructions/code-review.instructions.md
    .github/instructions/coding-agent.instructions.md
    .github/instructions/python.instructions.md
    .github/instructions/typescript.instructions.md
```

## Repo conventions

The Copilot CLI and coding agent read the following files automatically:

- `.github/copilot-instructions.md` — global, both agents.
- `.github/instructions/<name>.instructions.md` — scoped via the `applyTo`
  glob and the optional `excludeAgent` field in frontmatter.

Keep all generated content under `.github/`. Do not scatter Copilot config
into a `.copilot/` directory or repo-root files — the supported convention
is `.github/`.

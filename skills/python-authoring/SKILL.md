---
name: python-authoring
description: >
  Write, edit, refactor, or review Python as concise, stdlib-first, fully
  typed code that validates input at the boundary and passes basedpyright.
  Use when the user changes Python source, scripts, CLIs, validators, or
  tests, or says "make this more Pythonic", "clean up this Python",
  "de-slop this Python", "use a dataclass here", "fix the basedpyright
  errors", or invokes /python-authoring. Do NOT use for Bash scripts
  (/bash-shortening), task-runner recipes (/justfile), dependency-version
  conflicts (/version-doctor), or bug and security review (/age).
license: MIT
---

# Authoring Python

Produce the smallest readable Python change that satisfies the request and matches the project.

## Work in this order

1. Read the root configuration (`pyproject.toml`, lockfile, task runner), the target exports, immediate callers, and nearby conventions.
2. Keep every changed line traceable to the request. Do not invent orchestration, compatibility layers, abstractions, dependencies, or future flexibility.
3. Put code in the owning package and preserve existing module and package boundaries.
4. Validate untrusted input once at the boundary, then work with typed trusted data.
5. Choose the clearest succinct Python construct; do not compress code until it becomes harder to read.
6. Remove only slop introduced by the change and code that the change orphaned.
7. Type-check changed files with basedpyright, run targeted tests, regenerate generated artifacts, then run the project's full gate.

Done when every item in the completion check holds.

## Keep runtime code stdlib-first

- Target the Python version the project declares in `requires-python`. Use its language and typing features directly; do not add compatibility code for older versions.
- Treat the standard library as the default dependency budget. Add a third-party runtime dependency only when the project already uses it or the user approves it, and declare it where the project declares dependencies.
- Reuse existing parsing and I/O boundaries instead of importing a second library for the same job.
- Keep test-only and tooling-only dependencies out of runtime modules.
- Prefer `argparse`, `json`, `pathlib`, `tempfile`, `shutil`, `zipfile`, `collections`, `itertools`, and `contextlib` over hand-written equivalents.

## Model and validate data explicitly

- Parse untrusted text with the appropriate stdlib parser, require the expected container shape, check required keys and value types, and raise a specific error at the boundary.
- Convert validated mappings into frozen dataclasses or domain value types when named fields and invariants make the contract clearer.
- Use `TypedDict` only when a mapping must remain a mapping. It documents a static shape; it does not validate runtime input.
- Use `Enum` or `Literal` for closed value sets and `Protocol` for structural interfaces that have multiple real consumers.
- Fully annotate function and public-method boundaries. Omit obvious local annotations when inference is clear.
- Do not pass raw structured dictionaries beyond the parsing boundary when a named record makes the contract clearer.

## Preserve module boundaries

- Do not import another package's private internals. Consume its public interface.
- Move code to a shared module only when multiple existing consumers need the same behavior.
- Keep cross-module orchestration in the owning workflow seam, not a leaf helper. Avoid reverse dependencies and import cycles.
- Never hand-edit generated artifacts. Change their sources and run the project's generator.
- Keep CLI modules thin: accept `argv`, return an integer status, print diagnostics to stderr, and propagate failure through a nonzero exit.
- Keep validators and linters read-only. They inspect and report; they do not mutate the workspace.

## Prefer succinct, readable Python

- Use `match` for genuine shape-based dispatch; keep a simple `if` for a binary decision.
- Use a handler mapping for stable command-to-function dispatch; do not create a registry for one or two branches.
- Use an assignment expression only when it removes a repeated computation or clarifies a loop condition.
- Use `any`, `all`, comprehensions, and generator expressions for pure collection queries or transformations. Keep a loop when it carries state, side effects, or clearer early exits.
- Prefer generators when the result is consumed once.
- Use `enumerate`, direct iteration, f-strings, context managers, and `pathlib` instead of manual indexing, string assembly, cleanup, or path manipulation.
- Ignore only named, intentional failures. Never use a bare `except`, swallow `Exception`, return an empty default on failure, or use `contextlib.suppress(Exception)`.
- Keep new top-level functions at 40 lines or fewer unless one contiguous algorithm is clearer than an artificial split.
- Prefer one clear expression to verbose scaffolding, but split dense expressions when intermediate names explain intent.

## De-slop before finishing

- Delete narration comments and docstrings that restate the code. Keep non-obvious rationale and public API documentation.
- Remove abstractions with one concrete consumer unless the current task requires the seam.
- Name values after domain concepts, not containers or implementation types.
- Consolidate repetitive tests by behavior; do not add shallow input-variation tests.
- Fix the underlying lint issue instead of adding a suppression.

## Type-check with basedpyright

- Run `basedpyright <changed .py files>` before finishing. Fall back to `uvx basedpyright` if the binary is absent. Changed files must report zero errors and warnings.
- Read configuration from `[tool.basedpyright]` in `pyproject.toml` or from `pyrightconfig.json`. Respect its include paths, `pythonVersion`, and execution environments.
- An unset `typeCheckingMode` means `recommended`: every rule is on, and `failOnWarnings` fails the run on warnings too.
- Fix any diagnostic that your change surfaces. When the project keeps a baseline, do not add new entries to it.
- basedpyright is stricter than stock pyright. Assign deliberately ignored call results to `_` (`reportUnusedCallResult`), collapse implicit string concatenations, and `cast` untrusted boundary reads to their validated type.
- Fix the type at its source. When a suppression is genuinely unavoidable, use a rule-scoped `# pyright: ignore[ruleName]`, never a bare `# type: ignore` or a file-wide switch. `reportIgnoreCommentWithoutRule` flags unscoped ignores.
- Use `--outputjson` when a tool needs machine-readable diagnostics. In GitHub Actions the CLI detects CI and emits inline PR annotations with no extra flags.
- Read exit codes precisely: 0 clean, 1 diagnostics reported, 2 fatal internal error, 3 unreadable config, 4 bad CLI arguments. Treat 2–4 as tooling breakage to fix or report, never as type findings.
- Pin basedpyright to an exact version for reproducible results.

## Test and finish

- Test observable behavior and the reason it matters; do not add assertions that can pass when the implementation is broken.
- Keep filesystem tests inside `tmp_path` or an equivalent temporary directory. Do not depend on user paths, repository-external state, network access, or auto-loaded pytest plugins.
- Run the most focused affected tests first, and the project's full gate (for example `just check` or `just build`) last.

## Completion check

Confirm:

- Runtime imports obey the stdlib-first dependency policy.
- Boundary input is validated once and converted into an appropriate trusted representation.
- Code lives in the owning module or a justified shared module.
- Generated artifacts match their sources when applicable.
- CLI and validator failures remain loud, read-only validators remain read-only, and tests are hermetic.
- No silent failures, speculative abstractions, narration comments, unnecessary local annotations, or unrelated cleanup remain.
- Changed Python files pass basedpyright with zero errors and warnings.
- A fresh run of the project's full gate passed.

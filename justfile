# The one command to run after every change.
default: build

# Canonical gate (default) — autofix markdown/YAML, then verify markdown/YAML, type check, dead-code check, and tests.
build: (_gate "fix")

# CI gate — identical checks, NO autofix. A clean run here == a clean CI.
ci: (_gate "check")

[private]
[no-exit-message]
_gate mode:
    python3 .github/scripts/run_gate.py {{mode}}

# List all available commands
list:
    @just --list

# Run tests (skill validators + self-tests — mirrors CI)
test:
    python3 .github/scripts/run_gate.py test

# Type-check source and hidden .github/scripts; Pyright excludes hidden paths by default.
typecheck:
    uv run --locked --all-groups basedpyright
    uv run --locked --all-groups basedpyright --project .github/scripts

# Find unused Python code across source, tests, examples, and validators.
lint-py-dead-code:
    uv run --locked --all-groups python .github/scripts/check_dead_code.py

# Run fromargs' pytest suite, optionally pinned to one Python version.
test-fromargs python="":
    python3 .github/scripts/run_gate.py fromargs {{python}}

# Mutation-test the fromargs acceptance suite (not part of build/ci).
mutate-fromargs:
    uv run --locked --project packages/fromargs python packages/fromargs/tests/e2e/mutate.py

# Print the CHANGELOG section for one fromargs version; fail when it is missing or empty.
[script("bash")]
fromargs-release-notes $version:
    set -euo pipefail
    notes=$(awk -v v="$version" '
        index($0, "## [" v "]") == 1 { on = 1; next }
        on && /^(## |\[[^]]+\]: )/ { exit }
        on { print }
    ' packages/fromargs/CHANGELOG.md | sed '/./,$!d')
    if [ -z "${notes//[[:space:]]/}" ]; then
        echo "error: packages/fromargs/CHANGELOG.md has no section for $version" >&2
        exit 1
    fi
    printf '%s\n' "$notes"

# Print the CHANGELOG section for one session-analytics version; fail when it is missing or empty.
[script("bash")]
session-analytics-release-notes $version:
    set -euo pipefail
    notes=$(awk -v v="$version" '
        index($0, "## [" v "]") == 1 { on = 1; next }
        on && /^(## |\[[^]]+\]: )/ { exit }
        on { print }
    ' packages/session-analytics/CHANGELOG.md | sed '/./,$!d')
    if [ -z "${notes//[[:space:]]/}" ]; then
        echo "error: packages/session-analytics/CHANGELOG.md has no section for $version" >&2
        exit 1
    fi
    printf '%s\n' "$notes"

# Fix markdown formatting issues
lint-md-fix:
    markdownlint-cli2 --fix "*.md"

# Verify markdown (no autofix)
lint-md:
    markdownlint-cli2 "*.md"

# Fix YAML formatting issues
lint-yaml-fmt-fix:
    yamlfmt .

# Verify YAML formatting (no autofix)
lint-yaml-fmt:
    yamlfmt -lint .

# Verify YAML lint rules
lint-yaml:
    yamllint -c .yamllint.yml .

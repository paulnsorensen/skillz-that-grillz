# The one command to run after every change.
default: build

# Canonical gate (default) — autofix lint, then verify markdown/yaml/shell + tests.
build: (_gate "fix")

# CI gate — identical checks, NO autofix. A clean run here == a clean CI.
ci: (_gate "check")

[private]
[no-exit-message]
[script("bash")]
_gate mode:
    set -uo pipefail
    step() { local n=$1; shift; local o
        if o=$("$@" 2>&1); then echo "✓ $n"
        else echo "✗ $n"; printf '%s\n' "$o"; exit 1; fi; }
    if [ "{{mode}}" = "fix" ]; then
        step markdown    just lint-md-fix
        step yaml-fmt    just lint-yaml-fmt-fix
    else
        step markdown    just lint-md
        step yaml-fmt    just lint-yaml-fmt
    fi
    step yaml  just lint-yaml
    step shell just lint-sh
    step test  just test

# List all available commands
list:
    @just --list

# Run tests (skill validators + self-tests + bash unit tests — mirrors CI)
test:
    python3 .github/scripts/test_validate_skills.py
    python3 .github/scripts/test_validate_evals.py
    python3 .github/scripts/validate_skills.py
    bats tests/bash/test_install.bats
    bats tests/bash/test_skillz.bats
    bats tests/bash/test_respond_post_reply.bats
    uv run --locked --project lib/fromargs basedpyright --project lib/fromargs
    uv run --locked --project lib basedpyright lib/src/wedge lib/tests/wedge
    just test-fromargs
    uv run --locked --project lib pytest lib/tests/wedge -q
    uv run --locked --project lib wedge check --root skills --root lib/examples/skills

# Run fromargs' pytest suite, optionally pinned to one Python version.
test-fromargs python="":
    uv run --locked {{ if python != "" { "--python " + python } else { "" } }} --project lib/fromargs pytest lib/fromargs/tests -q

# Lint shell scripts
lint-sh:
    shellcheck scripts/install.sh skills/file-handler/scripts/skillz.sh

# Mutation-test the fromargs acceptance suite (not part of build/ci).
mutate-fromargs:
    uv run --locked --project lib/fromargs python lib/fromargs/tests/e2e/mutate.py

# Fix markdown formatting issues
lint-md-fix:
    markdownlint-cli2 --fix "skills/**/*.md" "*.md"

# Verify markdown (no autofix)
lint-md:
    markdownlint-cli2 "skills/**/*.md" "*.md"

# Fix YAML formatting issues
lint-yaml-fmt-fix:
    yamlfmt .

# Verify YAML formatting (no autofix)
lint-yaml-fmt:
    yamlfmt -lint .

# Verify YAML lint rules
lint-yaml:
    yamllint -c .yamllint.yml .

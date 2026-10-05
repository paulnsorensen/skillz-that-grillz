# The one command to run after every change.
default: build

# Canonical gate (default) — autofix lint, then verify markdown/yaml + tests.
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
    step test  just test

# List all available commands
list:
    @just --list

# Run tests (skill validators + self-tests — mirrors CI)
test:
    python3 .github/scripts/test_validate_skills.py
    python3 .github/scripts/test_validate_evals.py
    python3 .github/scripts/validate_skills.py
    python3 .github/scripts/test_check_skillz_references.py
    python3 .github/scripts/check_skillz_references.py
    python3 -B -m unittest discover -s skills/skillz/engine/tests -p 'test_*.py'
    uv run --locked --project lib/fromargs basedpyright --project lib/fromargs
    uv run --locked --extra experiments --project lib basedpyright lib/src/wedge lib/tests/wedge lib/src/skillz_experiments lib/tests/skillz_experiments skills/skillz/scripts/inspect_skill.py
    just test-fromargs
    uv run --locked --extra experiments --project lib pytest lib/tests/wedge lib/tests/skillz_experiments -q
    uv run --locked --project lib wedge check --root lib/examples/skills --root lib/examples/consumer/skills
    uv run --locked --project lib wedge bundle skills/skillz skills/wedge --check

# Run fromargs' pytest suite, optionally pinned to one Python version.
test-fromargs python="":
    uv run --locked {{ if python != "" { "--python " + python } else { "" } }} --project lib/fromargs pytest lib/fromargs/tests -q

# Mutation-test the fromargs acceptance suite (not part of build/ci).
mutate-fromargs:
    uv run --locked --project lib/fromargs python lib/fromargs/tests/e2e/mutate.py

# Print the CHANGELOG section for one fromargs version; fail when it is missing or empty.
[script("bash")]
fromargs-release-notes $version:
    set -euo pipefail
    notes=$(awk -v v="$version" '
        index($0, "## [" v "]") == 1 { on = 1; next }
        on && /^(## |\[[^]]+\]: )/ { exit }
        on { print }
    ' lib/fromargs/CHANGELOG.md | sed '/./,$!d')
    if [ -z "${notes//[[:space:]]/}" ]; then
        echo "error: lib/fromargs/CHANGELOG.md has no section for $version" >&2
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

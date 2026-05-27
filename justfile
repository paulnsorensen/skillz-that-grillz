set dotenv-load := true
set unstable  # for the [script] gate recipe

# The one command to run after every change.
default: build

# Canonical gate (default) — autofix lint, then verify markdown/yaml/shell + tests.
build: (_gate "fix")

# CI gate — identical checks, NO autofix. A clean run here == a clean CI.
ci: (_gate "check")

[private]
[script("bash")]
_gate mode:
    set -uo pipefail
    step() { local n=$1; shift; local o
        if o=$("$@" 2>&1); then echo "✓ $n"
        else echo "✗ $n"; printf '%s\n' "$o"; exit 1; fi; }
    tests() {
        set -e  # fail-fast: without this, a non-last failing test is masked
        python3 .github/scripts/test_validate_skills.py
        python3 .github/scripts/validate_skills.py
        python3 plugins/util/skills/bash-shortening/scripts/bash-shorten.py --self-test
        python3 -m pytest plugins/util/skills/ralphify-spec/scripts/test_validate.py -q
        bats tests/bash/test_install.bats
        bats tests/bash/test_skillz.bats
        bats tests/bash-shortening/test_bash_shorten.bats
    }
    if [ "{{mode}}" = "fix" ]; then
        step markdown markdownlint-cli2 --fix "plugins/**/*.md" "*.md"
        step yaml-fmt yamlfmt .
    else
        step markdown markdownlint-cli2 "plugins/**/*.md" "*.md"
        step yaml-fmt yamlfmt -lint .
    fi
    step yaml  yamllint -c .yamllint.yml .
    step shell shellcheck scripts/install.sh plugins/util/skills/file-handler/scripts/skillz.sh
    step test  tests

# List all available commands
list:
    @just --list

# Run tests (skill validators + self-tests + bash unit tests — mirrors CI)
test:
    python3 .github/scripts/test_validate_skills.py -v
    python3 .github/scripts/validate_skills.py
    python3 plugins/util/skills/bash-shortening/scripts/bash-shorten.py --self-test
    python3 -m pytest plugins/util/skills/ralphify-spec/scripts/test_validate.py -q
    bats tests/bash/test_install.bats
    bats tests/bash/test_skillz.bats
    bats tests/bash-shortening/test_bash_shorten.bats

# Lint shell scripts
lint-sh:
    shellcheck scripts/install.sh plugins/util/skills/file-handler/scripts/skillz.sh

# Fix markdown formatting issues
lint-md-fix:
    markdownlint-cli2 --fix "plugins/**/*.md" "*.md"

# Verify markdown (no autofix)
lint-md:
    markdownlint-cli2 "plugins/**/*.md" "*.md"

# Fix YAML formatting issues
lint-yaml-fix:
    yamlfmt .

# Verify YAML formatting
lint-yaml:
    yamllint -c .yamllint.yml .

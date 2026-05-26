set dotenv-load := true

# List all available commands
@default:
    just --list

# Run tests (skill validators + bash unit tests)
test:
    python3 .github/scripts/test_validate_skills.py -v
    python3 .github/scripts/validate_skills.py
    bats tests/bash/test_install.bats
    bats tests/bash/test_skillz.bats

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

# Full local check with autofixes
check: lint-md-fix lint-yaml-fix lint-yaml lint-sh test

# CI-mode verification (no autofixes)
ci: lint-md lint-yaml lint-sh test

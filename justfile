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

# Run agent-session-analytics' pytest suite, optionally pinned to one Python version.
test-agent-session-analytics python="":
    python3 .github/scripts/run_gate.py agent-session-analytics {{python}}

# Mutation-test the fromargs acceptance suite (not part of build/ci).
mutate-fromargs:
    uv run --locked --project packages/fromargs python packages/fromargs/tests/e2e/mutate.py

# Opt-in live smoke runs of the experiment stack and session analytics (not part of build/ci). They need SKILLZ_LIVE=1.
# HARNESS is claude or codex. SCENARIO is basic (default), all, or a comma list; see packages/skillz/tests/skillz_live.
# SKILLZ_LIVE_DRY=1 stops each run at its budget question and makes no model call. The basic run uses at most 88 calls.
# Each attempt appends one JSON line to .live-smoke/runs.jsonl (gitignored).
[script("bash")]
live-smoke $harness $model $scenario="basic":
    set -euo pipefail
    if [ "${SKILLZ_LIVE:-}" != 1 ]; then
        echo "error: live-smoke makes real model calls; set SKILLZ_LIVE=1 to run it" >&2
        exit 2
    fi
    SKILLZ_LIVE_HARNESS="$harness" SKILLZ_LIVE_MODEL="$model" SKILLZ_LIVE_SCENARIO="$scenario" \
        uv run --locked --extra experiments --project packages/skillz \
        pytest packages/skillz/tests/skillz_live -m live -v -rs \
        --basetemp "${XDG_CACHE_HOME:-$HOME/.cache}/skillz-live-smoke" -p no:cacheprovider

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

# Print the CHANGELOG section for one agent-session-analytics version; fail when it is missing or empty.
[script("bash")]
agent-session-analytics-release-notes $version:
    set -euo pipefail
    notes=$(awk -v v="$version" '
        index($0, "## [" v "]") == 1 { on = 1; next }
        on && /^(## |\[[^]]+\]: )/ { exit }
        on { print }
    ' packages/agent-session-analytics/CHANGELOG.md | sed '/./,$!d')
    if [ -z "${notes//[[:space:]]/}" ]; then
        echo "error: packages/agent-session-analytics/CHANGELOG.md has no section for $version" >&2
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

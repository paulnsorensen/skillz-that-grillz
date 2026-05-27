# Python Justfile Recipes

Default to `uv` as the package manager. If the project already uses pip, poetry, or pdm, match the existing toolchain instead.

## Template

```just
set dotenv-load
set unstable  # for the [script] gate recipe

# The one command to run after every change.
default: build

# Canonical gate — autofix, then lint, typecheck, test, coverage. Compact output.
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
    if [ "{{mode}}" = "fix" ]; then
        step format uv run ruff format .
        step lint   uv run ruff check --fix .
    else
        step format uv run ruff format --check .
        step lint   uv run ruff check .
    fi
    step typecheck uv run mypy src/
    step test      uv run pytest --cov=src --cov-fail-under=85 -q

# Install dependencies
install:
    uv sync

# Install with dev + all extras
install-dev:
    uv sync --all-extras --dev

# Run the app
run *args:
    uv run python -m myapp {{args}}

# Run tests
test *args:
    uv run pytest {{args}}

# Run tests with coverage (threshold enforced via pyproject.toml)
test-coverage:
    uv run pytest --cov=src --cov-report=html --cov-report=json --cov-report=term-missing

# Per-file coverage gate (workaround — no native pytest-cov support as of 2026)
cov-per-file MIN="70":
    uv run pytest --cov=src --cov-report=json -q
    jq -r --argjson min {{MIN}} \
        '.files | to_entries[] | select(.value.summary.percent_covered < $min) | "\(.key): \(.value.summary.percent_covered | round)%"' \
        coverage.json | (! grep . || { echo "Files below {{MIN}}%"; exit 1; })

# Ratchet: never let overall coverage regress (reads/writes .coverage-baseline)
cov-ratchet:
    #!/usr/bin/env bash
    CUR=$(jq '.totals.percent_covered' coverage.json)
    BASE=$(cat .coverage-baseline 2>/dev/null || echo 0)
    awk -v c=$CUR -v b=$BASE 'BEGIN{exit !(c>=b)}' \
        && echo $CUR > .coverage-baseline \
        || { echo "Coverage regression: $CUR% < $BASE%"; exit 1; }

# Lint (check only)
lint:
    uv run ruff check .
    uv run ruff format --check .

# Format and auto-fix
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Type checking
typecheck:
    uv run mypy src/

# Build distribution artifact (not the gate — that's `build`)
dist:
    uv build

# Publish to PyPI
publish: ci dist
    uv publish

# Clean artifacts
clean:
    find . -type d -name __pycache__ | xargs rm -rf
    find . -name "*.pyc" -delete
    rm -rf .coverage htmlcov/ dist/ build/ *.egg-info
```

## Coverage config (pyproject.toml)

```toml
[tool.coverage.report]
fail_under = 85
show_missing = true
skip_covered = false
exclude_also = ["if TYPE_CHECKING:", "raise NotImplementedError"]

[tool.pytest.ini_options]
addopts = "--cov=src --cov-report=term-missing --cov-report=json --cov-fail-under=85"
```

The `fail_under` in `[tool.coverage.report]` is the single source of truth — it's what `--cov-fail-under` reads. Per-file thresholds are not natively supported (pytest-cov issue #444); use the `cov-per-file` recipe above as a workaround. Commit `.coverage-baseline` to enforce ratcheting in CI.

## Notes

- Always `uv run` instead of bare `python` or `pytest` — ensures correct venv
- Replace `myapp` with the actual module name from `pyproject.toml`
- If project uses `mypy`, add typecheck. If not (ruff-only), skip it
- For Django/FastAPI/Flask, add `dev` recipe with the framework's dev server
- For Alembic, add `migrate` / `rollback` recipes
- Check if `ruff` is configured — if not, `lint`/`fmt` might use `black`/`flake8` instead

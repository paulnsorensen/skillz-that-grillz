# TypeScript/JavaScript Justfile Recipes

Detect the package manager from lockfiles:

- `bun.lockb` / `bun.lock` -> bun
- `pnpm-lock.yaml` -> pnpm
- `yarn.lock` -> yarn
- `package-lock.json` -> npm

## Template (npm — adapt runner for other managers)

```just
set dotenv-load

# The one command to run after every change.
default: build

# Canonical gate — autofix, then lint, typecheck, test, coverage. Compact output.
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
        step format npx prettier --write .
        step lint   npx eslint --fix src/
    else
        step format npx prettier --check .
        step lint   npx eslint src/
    fi
    step typecheck npx tsc --noEmit
    # test:coverage must exist in package.json and enforce its own threshold
    # (e.g. vitest --coverage with thresholds in vitest.config, or jest --coverage).
    step test npm run test:coverage

# Install dependencies
install:
    npm install

# Run dev server
dev:
    npm run dev

# Build for production (the artifact — not the gate, which is `build`)
dist:
    npm run build

# Run tests
test *args:
    npm test -- {{args}}

# Lint (static analysis only — not typechecking)
lint:
    npx eslint src/

# Format
fmt:
    npx prettier --write .

# Type check only
typecheck:
    npx tsc --noEmit

# Clean
clean:
    rm -rf dist/ node_modules/.cache
```

**Keep `lint` and `typecheck` as separate recipes and separate npm scripts.**
Conflating them (e.g. `"lint": "tsc --noEmit"`) breaks rtk's `npm run <script>`
wrapper, which infers tool output format from the script name. More importantly,
the name is a lie — ESLint/biome lints, tsc typechecks. Name the script after
what it runs.

## Bun variant

```just
install:
    bun install

dev:
    bun run dev

test *args:
    bun test {{args}}

dist:
    bun run build
```

In the `_gate`, swap `npx`/`npm run test:coverage` for the bun equivalents
(`bunx prettier`, `bunx eslint`, `bunx tsc --noEmit`, `bun test --coverage`).

## Monorepo (Turborepo/Nx)

```just
# Build all packages
build-all:
    npx turbo build

# Test all packages
test-all:
    npx turbo test

# Run a specific workspace
dev workspace:
    npx turbo dev --filter={{workspace}}
```

## Notes

- Check `package.json` scripts — mirror the important ones as just recipes
- Don't duplicate every npm script — just wrap the most common workflows
- For Next.js/Vite/Remix, the `dev`/`build` recipes map to framework CLI
- If using Biome instead of ESLint+Prettier, use `npx biome check`/`npx biome format`

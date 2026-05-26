# Go Justfile Recipes

## Template

```just
set dotenv-load
set unstable  # for the [script] gate recipe

BINARY := "myapp"
VERSION := `git describe --tags --always 2>/dev/null || echo "dev"`

# The one command to run after every change.
default: build

# Canonical gate — autofix, then vet, lint, test, coverage. Compact output.
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
    # Go has no native --fail-under; enforce the total via awk (functions resolve
    # inside step's command-substitution subshell).
    cov() {
        go test -coverprofile=coverage.out -covermode=atomic ./... || return 1
        go tool cover -func=coverage.out | tail -1 |
            awk -v min=80 '{gsub(/%/,"",$3); if ($3+0 < min) {printf "FAIL: total %s%% < %s%%\n",$3,min; exit 1}}'
    }
    if [ "{{mode}}" = "fix" ]; then
        step format bash -c 'gofmt -s -w . && goimports -w .'
        step lint   golangci-lint run --fix ./...
    else
        step format bash -c 'o=$(gofmt -s -l .); [ -z "$o" ] || { printf "unformatted:\n%s\n" "$o"; exit 1; }'
        step lint   golangci-lint run ./...
    fi
    step vet  go vet ./...
    step test cov

# Build binary artifact (not the gate — that's `build`)
dist:
    go build -ldflags "-X main.version={{VERSION}}" -o bin/{{BINARY}} ./cmd/{{BINARY}}

# Run the app
run *args:
    go run ./cmd/{{BINARY}} {{args}}

# Run tests
test *args:
    go test ./... {{args}}

# Run tests with race detector
test-race:
    go test -race ./...

# Run tests with coverage report
test-coverage:
    go test -coverprofile=coverage.out -covermode=atomic ./...
    go tool cover -html=coverage.out

# Enforce global coverage threshold (Go has no native --fail-under)
cov-check MIN="80":
    go test -coverprofile=coverage.out -covermode=atomic ./...
    go tool cover -func=coverage.out | tail -1 | \
        awk -v min={{MIN}} '{gsub(/%/,"",$3); if ($3+0 < min) {printf "FAIL: %s%% < %s%%\n", $3, min; exit 1}}'

# Per-function coverage gate
# `go tool cover -func` emits one row per function (path/file.go:line:\tname\tNN.N%),
# not per package — there is no native per-package threshold in the Go toolchain.
cov-per-func MIN="75":
    go test -coverprofile=coverage.out ./...
    go tool cover -func=coverage.out | awk -v min={{MIN}} '
        /^total:/ {next}
        {gsub(/%/,"",$NF); if ($NF+0 < min) { printf "FAIL %s: %s%%\n",$1,$NF; bad=1 }}
        END { exit bad+0 }'

# Ratchet: never let overall coverage regress (reads/writes .coverage-baseline)
cov-ratchet:
    #!/usr/bin/env bash
    go test -coverprofile=coverage.out ./... >/dev/null
    CUR=$(go tool cover -func=coverage.out | tail -1 | awk '{gsub(/%/,"",$3); print $3}')
    BASE=$(cat .coverage-baseline 2>/dev/null || echo 0)
    awk -v c=$CUR -v b=$BASE 'BEGIN{exit !(c>=b)}' \
        && echo $CUR > .coverage-baseline \
        || { echo "Coverage regression: $CUR% < $BASE%"; exit 1; }

# Lint (requires golangci-lint)
lint:
    golangci-lint run ./...

# Format
fmt:
    gofmt -s -w .
    goimports -w .

# Tidy modules
tidy:
    go mod tidy

# Generate code
generate:
    go generate ./...

# Clean
clean:
    rm -rf bin/ coverage.out
```

## Cross-compilation

```just
dist-all:
    GOOS=linux GOARCH=amd64 go build -o bin/{{BINARY}}-linux-amd64 ./cmd/{{BINARY}}
    GOOS=darwin GOARCH=arm64 go build -o bin/{{BINARY}}-darwin-arm64 ./cmd/{{BINARY}}
    GOOS=windows GOARCH=amd64 go build -o bin/{{BINARY}}-windows-amd64.exe ./cmd/{{BINARY}}
```

## Coverage notes

- Go's toolchain has no `--fail-under` flag — thresholds always require a shell script or awk one-liner.
- The awk pattern above is one common approach; some projects use env-var `COVERAGE_THRESHOLD` instead of a just parameter, or the `go-test-coverage` tool for declarative thresholds via YAML.
- Commit `.coverage-baseline` to enforce the ratchet in CI.

## Notes

- Replace `myapp` and `./cmd/myapp` with actual binary/module path
- If no `cmd/` directory, use `./` or `.` as the build path
- Check for `golangci-lint` config (`.golangci.yml`) before adding lint recipe
- For web services, add `dev` recipe with air/reflex for hot reload
- For protobuf projects, add `proto` recipe for code generation

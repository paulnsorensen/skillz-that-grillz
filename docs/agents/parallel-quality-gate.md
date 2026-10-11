# Parallel quality gate

The quality gate uses bounded parallel check lanes and pytest workers.
Local verification remains incomplete on this shared Mac.
CI supplies Linux verification when this branch has a pull request.[^1]
Do not treat this implementation as a measured speed improvement.[^2]

## Commands

`just build` runs format fixes before checks.
`just ci` runs the same checks without format fixes.
`just test` runs test lanes.
`just test-fromargs [python]` keeps the optional Python version argument.[^3]

## Execution order

The scheduler completes markdown and YAML format steps first.
It then synchronizes all workspace packages, development groups, and extras once.
The packages share the root `.venv` and `uv.lock`.
Concurrent uv readers use `--no-sync` after that preparation.
The wedge and fromargs pytest lanes start before the smaller test lanes.
Each failed lane prints its captured diagnostics.
The scheduler returns nonzero if any lane fails.[^4]

## Worker limits

`GATE_JOBS` sets the maximum concurrent check lanes.
`TEST_WORKERS` sets the worker count for each pytest suite.
Both settings default to four and require positive integers.
These limits are separate.
Two concurrent pytest lanes can each start four workers.[^5]

## Resource ownership

The scheduler creates a unique short directory under `/tmp`.
Each pytest suite receives a separate base directory and cache.
Short bases avoid the Mac Unix-socket path limit after xdist adds worker directories.
The scheduler stops owned process groups before it deletes the temporary root on interruption.[^6]

## Dependencies and artifact keys

pytest-xdist remains a development dependency in both workspace packages.
Runtime dependency declarations remain unchanged.
The root lock records the changed development dependencies.
The cheese-cave example key changes because its key includes the root lock.
Its bundled content digest remains unchanged.[^7]

## Verification limits

The repeated timing benchmark is cancelled during its warmup.
It supplies no completed sample or green baseline.
After rebase onto `43a9dbe`, the local wedge pytest lane reports 22 failed, 2864 passed, and 47 skipped.
All other gate lanes pass.
The socket-path failure passes after the short-base correction.
The remaining failures have no completed local baseline comparison.
Do not label them pre-existing or weaken runtime guards to hide them.[^2]

## References

[^1]: .github/workflows/validate.yml:3-6,38-82. The workflow runs on pull requests or pushes to main.
[^2]: .context/parallelism/run-plan.md; .context/parallelism/gate-failures.md:43-55. These local evidence files are gitignored.
[^3]: justfile:1-34.
[^4]: .github/scripts/run_gate.py:110-161; pyproject.toml:17-24.
[^5]: .github/scripts/run_gate.py:164-172; CONTRIBUTING.md:60-65.
[^6]: .github/scripts/run_gate.py:89-107,196-217; .github/scripts/test_run_gate.py:82-127.
[^7]: packages/wedge/pyproject.toml:37-38; packages/fromargs/pyproject.toml:28-29; uv.lock; packages/wedge/src/wedge/_key.py:134-152; packages/wedge/examples/skills/cheese-cave/scripts/cheese-cave.wedge.json.

_Source: Approved parallel-build contract, origin/main workspace rebase, and local verification · Updated: 2026-10-10 · Supersedes: preliminary separate-environment description_

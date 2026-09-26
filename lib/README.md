# Wedge

Wedge packages a `fromargs` CLI as a reproducible shiv `.pyz` for a skill.
It is the CLI in the `skillz-that-grillz` distribution. The `fromargs`
library remains a separate distribution at `lib/fromargs/`.

A skill commits `wedge.toml`, a content-keyed lock, and a launcher. Wedge
builds the `.pyz` from the locked dependency closure and publishes it as a
GitHub release asset. The launcher verifies the asset hash before execution.

For local development, run `uv run --project lib wedge --help`. Building
requires `uv`; publishing also requires an authenticated `gh` CLI.

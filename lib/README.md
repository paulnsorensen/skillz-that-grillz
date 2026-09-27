# Wedge

Wedge packages a `fromargs` CLI as a compressed shiv `.pyz` for a skill.
It is the CLI in the `skillz-that-grillz` distribution. The `fromargs`
library remains a separate distribution at `lib/fromargs/`.

A skill commits `wedge.toml`, a content-keyed lock, and a launcher. Wedge
builds the `.pyz` from the locked dependency closure and publishes it as a
GitHub release asset. The launcher verifies a sha256 over the asset's
uncompressed contents before execution.

Every command takes skill directories as arguments or finds `*/wedge.toml`
under `--root` (default `skills`). `build`, `lock`, and `publish` run the
skills in parallel (`--jobs`), and skills that share a project, source, and
groups share one installed site directory, so a repository of many skills
over one package downloads its closure once per run.

```sh
wedge lock --root skills                 # rewrite every lock and launcher
wedge check --root skills                # verify locks, launchers, no committed .pyz
wedge build --root skills --out dist     # one <name>-<digest12>.pyz per skill
wedge publish --root skills --repo owner/name --target <sha>
```

For local development, run `uv run --project lib wedge --help`. Building
requires `uv`; publishing also requires an authenticated `gh` CLI.

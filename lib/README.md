# Wedge

Wedge packages a `fromargs` CLI as a compressed shiv `.pyz` for a skill.
It is the CLI in the `skillz-that-grillz` distribution. The `fromargs`
library remains a separate distribution at `lib/fromargs/`.

A skill may commit a direct executable bundle at `scripts/<name>.pyz`. Wedge
also supports the legacy content-keyed lock, launcher, and release workflow.
The archive digest covers normalized uncompressed contents.

Every command takes skill directories as arguments or finds `*/wedge.toml`
under `--root` (default `skills`). A `wedge.toml` in the root itself holds
defaults for every skill beside it. A parent directory with a `SKILL.md` is a
skill, so a CLI directory nested in a skill reads no defaults. `build`, `lock`, and `publish` run the
skills in parallel (`--jobs`). Skills that share a project, source, includes,
and groups share one installed site directory. A repository of many skills
over one package downloads its closure once per run.

When `source_paths` is present, each entry selects an explicit relative path under `source`; its path remains in the archive namespace, including package initializers. When it is absent, the complete source tree is selected.

```sh
wedge bundle --root skills              # write scripts/<name>.pyz
wedge bundle --root skills --check       # verify committed bundles
wedge lock --root skills                 # legacy lock and launcher workflow
wedge build --root skills --out dist     # content-keyed build artifact
wedge publish --root skills --repo owner/name --target <sha> --branch main
```

`--branch` refuses to publish unless `--target` resolves to the checked-out
HEAD of every skill directory. It then compares that commit against the
branch through the compare API, so a shallow checkout still passes.

For local development, run `uv run --project lib wedge --help`. Building
requires `uv`; publishing also requires an authenticated `gh` CLI.

## Optional skill experiments

The installed skill ships `scripts/skillz-experiment.pyz` with pinned GEPA 0.1.4.
Run `python3 /path/to/skillz/scripts/skillz-experiment.pyz --help` from any directory.
For source development, use `uv run --project lib --extra experiments skillz-experiment --help`.
Source self-tests require an explicit `--target skills/skillz`.
Regenerate the committed archive with `uv run --project lib wedge bundle skills/skillz`.
The virtual root manifest selects runtime dependencies without bundling wedge tooling.
`just build` verifies archive freshness.
The commands are `dataset`, `baseline`, `search`, `evaluate`, `export`, and `self-test`.
They do not change wedge or fromargs behavior.
See [the experiment contract](../skills/skillz/references/experiments.md) before making live calls.

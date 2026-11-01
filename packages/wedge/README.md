# Wedge

Wedge packages a `fromargs` CLI as a compressed, directly runnable ZIP `.pyz` for a skill.
It is the CLI in the `skillz-that-grillz` distribution. The `fromargs`
library remains a separate distribution at `packages/fromargs/`.

A skill may commit a direct executable bundle at `scripts/<name>.pyz`. Wedge
also supports the legacy content-keyed lock, launcher, and release workflow.
The archive digest covers normalized uncompressed contents.

## Runtime and bytecode

Archives run from the ZIP without extraction or runtime cache writes.
The command can still write its requested output.
The runtime requires Python 3.11 or later.
On POSIX, older Python re-executes the newest pinned minor on `PATH`, preserving arguments and isolated mode.
Without a candidate, or on another platform, it exits 3 with a JSON version error.

Default builds contain bytecode for Python 3.11 through 3.15.
Exact compiler patches are 3.11.18, 3.12.16, 3.13.18, 3.14.10, and 3.15.2.
Set `bytecode = ["3.11", "3.15"]` for a subset, or `bytecode = []` for source only.
Unsupported minors fail configuration validation.
Pins and subsets enter the content key.
Cold builds can download pinned compilers through uv; warm builds can use read-only installed compilers.

Each tree holds complete first-party source and bytecode.
Third-party trees hold the static import set, reached literal dynamic imports, and package data.
The full source closure supports unknown cache tags and missing tree submodules.
Stock loaders retain package-resource support, and tracebacks read source lines from the ZIP.
Lazy command imports stay lazy.
The legacy download launcher still caches hash-verified release assets before archive execution.

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

For local development, run `uv run --project packages/wedge wedge --help`. Building
requires `uv`; publishing also requires an authenticated `gh` CLI.

## Merge queues and publication races

Run the check that matches each skill's packaging mode on both `pull_request`
and `merge_group: {types: [checks_requested]}`:

- **Locks and launchers:** run `wedge check --root skills`, or use the action's `command: check`.
- **Committed direct bundles:** run `wedge bundle --root skills --check`. It checks the committed bundles and does not overwrite them.
- **Bundles built only in CI:** build them from the event checkout, then test the generated distribution before merging.

`wedge check` rejects a direct-bundle skill, and `wedge bundle --check` rejects a
lock-mode skill. Keep each mode under its own root, or pass the skill directories
of one mode. The
[action workflow guide](../../actions/wedge/README.md#workflows)
defines the merge-queue setup, stale-artifact recovery, and publication policy.

### Example: easy-cheese

The [easy-cheese repository](https://github.com/paulnsorensen/easy-cheese) uses
CI-built direct bundles and a separate `dist` branch, not the action's legacy
release-asset workflow. These links pin the implementation inspected on
2026-10-22:

- [Validation workflow](https://github.com/paulnsorensen/easy-cheese/blob/d70e619dc0cda9c4d52140b1689d2e744c51a754/.github/workflows/validate.yml):
  handles `pull_request` and `merge_group`; builds archives, stages skills,
  checks publication with `gh skill publish --dry-run`, and runs each archive's `--help`.
- [Distribution workflow](https://github.com/paulnsorensen/easy-cheese/blob/d70e619dc0cda9c4d52140b1689d2e744c51a754/.github/workflows/publish-dist.yml):
  uses a shared `publish-dist` concurrency group with `cancel-in-progress: false`.
  Each run checks out the latest `main` tip, rather than the triggering commit.
  Manual and hourly runs retry publication when an earlier run fails or a newer pending run replaces it.
- [Distribution publisher](https://github.com/paulnsorensen/easy-cheese/blob/d70e619dc0cda9c4d52140b1689d2e744c51a754/scripts/publish_dist.py):
  puts the source snapshot and all built archives in one `dist` commit.
  Its `Source-Commit` trailer identifies the source. It skips older snapshots,
  rejects divergent history, and uses a non-force push so concurrent writes fail
  rather than overwrite another publication.

This example shows merge-group-ready validation and race-safe distribution,
not proof of queue enforcement. Check your own repository settings before
relying on a queue.

Do not copy the distribution workflow's pending-run replacement into a
release-asset publisher. It works here because the next snapshot includes all
intervening source changes; it does not preserve every historical release asset.

## Optional skill experiments

The installed skill ships `scripts/skillz-experiment.pyz` with pinned GEPA 0.1.4.
Run `python3 /path/to/skillz/scripts/skillz-experiment.pyz --help` from any directory.
For source development, use `uv run --project packages/wedge --extra experiments skillz-experiment --help`.
Regenerate the committed archive with `uv run --project packages/wedge wedge bundle skills/skillz`.
The virtual root manifest selects runtime dependencies without bundling wedge tooling.
`just build` verifies archive freshness.
The commands are `run`, `export`, `audit-facts`, and `self-test`.
They do not change wedge or fromargs behavior.
See [the experiment contract](../skills/skillz/references/experiments.md) before making live calls.

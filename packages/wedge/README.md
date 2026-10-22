# Wedge

Wedge packages a `fromargs` CLI as a compressed shiv `.pyz` for a skill.
It is the CLI in the `skillz-that-grillz` distribution. The `fromargs`
library remains a separate distribution at `packages/fromargs/`.

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

For local development, run `uv run --project packages/wedge wedge --help`. Building
requires `uv`; publishing also requires an authenticated `gh` CLI.

## Merge queues and publication races

Use a merge queue for repositories where concurrent pull requests change shared
skill sources, dependencies, or packaging configuration. Separate green pull
requests do not prove that their combined tree has current locks or bundles.
The queue checks that combined tree before merging; it does not repair artifacts
or serialize post-merge publication.

Run the appropriate packaging check on both `pull_request` and
`merge_group: {types: [checks_requested]}`:

- **Locks and launchers:** run `wedge check --root skills`, or use the action's `command: check`.
- **Committed direct bundles:** run `wedge bundle --root skills --check` without first overwriting the committed bundles.
- **Bundles built only in CI:** build them from the event checkout, then test the generated distribution before merging.

Require these checks in the target branch's rules and enable its merge queue
where available. Keep the event checkout so checks test the merge-group SHA,
not an individual pull request or `main` alone. The
[action workflow guide](../../actions/wedge/README.md#check-pull-requests-and-merge-groups)
shows the triggers, required-check setup, and stale-artifact recovery.

Publication needs a separate policy:

- **Release assets:** preserve every post-merge publication run. Immutable,
  content-addressed assets support concurrent uploads; a later run does not
  necessarily publish the asset an older lock needs.
- **A distribution branch:** publish one complete source snapshot and its bundles
  together. Serialize writers and reject an older source snapshot after a newer
  snapshot has shipped. A merge queue alone does not provide these guarantees.

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
  Manual and hourly runs retry publication when an earlier run fails or is replaced.
- [Distribution publisher](https://github.com/paulnsorensen/easy-cheese/blob/d70e619dc0cda9c4d52140b1689d2e744c51a754/scripts/publish_dist.py):
  puts the source snapshot and all built archives in one `dist` commit.
  Its `Source-Commit` trailer identifies the source. It skips older snapshots,
  rejects divergent history, and uses a non-force push so concurrent writes fail
  rather than overwrite another publication.

This example shows merge-group-ready validation and race-safe distribution,
not proof of queue enforcement. The active `main-protection` ruleset inspected
on 2026-10-22 has no `merge_queue` rule. Check your own repository settings before
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

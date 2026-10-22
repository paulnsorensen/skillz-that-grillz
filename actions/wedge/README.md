# wedge GitHub Action

`wedge` packages a pure-Python skill CLI as one `.pyz` file. Your repository
commits only a small launcher and a lock with the `.pyz` content sha256: a
digest over the archive's member names and uncompressed bytes. This action
builds the compressed `.pyz` and uploads it to a rolling `wedge` prerelease.
The launcher downloads the asset on first use and runs it only when its content
sha256 matches the lock. The digest ignores compression, so a lock written on
any host matches the asset that the runner builds.

The action has two commands:

- `check` fails when a lock is missing or stale, when a launcher differs
  from the template, or when a `.pyz` is committed beside a launcher. It
  builds nothing and needs no write access.
- `publish` builds each locked skill, checks that the build matches the lock,
  and uploads assets that are missing. It skips an asset that is already
  present only when the fresh build and the asset both match the lock.

The action runs `wedge` from its own checkout (the `packages/wedge/` project in
this repository). When you pin the action to a commit SHA, you also pin `wedge`. A
repository can pin `wedge` in a uv project of its own instead (step 3
below). It then runs the same commands with `uv run --locked --project
tools/wedge wedge …` in its workflows. One pin serves both local runs and CI.

## Set up a skill

1. Give the skill CLI a uv project: a `pyproject.toml` and a committed
   `uv.lock`. The non-dev dependencies of that project become the `.pyz`
   closure. Every dependency must be a `py3-none-any` wheel without a
   platform marker.
2. Add `wedge.toml` to the skill directory. All paths are relative to the
   skill directory:

   ```toml
   name = "hello"                    # launcher is scripts/hello
   entry = "hello:main"              # module:function that shiv runs
   source = "hello.py"               # module file or package directory
   project = "../.."                 # directory with pyproject.toml + uv.lock
   include = []                      # optional local packages to vendor
   groups = []                       # optional uv dependency groups to bundle
   repo = "your-org/your-repo"       # repository that hosts the release
   ```

   `source` and each `include` entry must be inside `project`. Use `include`
   for a local package that is not on an index, such as `fromargs`. Use
   `groups` when the CLI's dependencies live in a uv dependency group rather
   than in the project's own dependencies, as they do when the project also
   publishes a library. A `wedge.toml` in the root directory
   (`skills/wedge.toml`) holds defaults for every skill beside it: any key
   except `name` and `entry`, with the skill's own file winning. A repository
   of many skills over one package states `source`, `include`, `project`,
   `groups`, and `repo` once there.
3. Pin `wedge` in a small uv project of its own, for example
   `tools/wedge/pyproject.toml`, and commit that directory's `uv.lock`:

   ```toml
   [project]
   name = "wedge-tool"
   version = "0"
   requires-python = ">=3.11"
   dependencies = ["skillz-that-grillz @ git+https://github.com/paulnsorensen/skillz-that-grillz@<sha>#subdirectory=packages/wedge"]
   ```

   Keep it out of the skill project's own lock: `wedge` resolves `fromargs`
   from its checkout, and uv carries that source into any lock that depends
   on `wedge`, which would replace the `fromargs` wheel in the `.pyz` closure.

4. Write the locks and the launchers. Run this from the repository root and
   commit each `scripts/<name>` and `scripts/<name>.wedge.json`:

   ```sh
   uv run --locked --project tools/wedge wedge lock --root skills
   ```

   Every command finds `*/wedge.toml` under `--root`; `lock`, `build`, and
   `publish` run the skills in parallel and share one installed site
   directory between skills that share a project, source, includes, and
   groups. Use the same `<sha>` that
   your workflows pin. A different `wedge` version can compute a different
   key. A project without uv can run the same command through
   `uvx --from 'skillz-that-grillz @ git+…@<sha>#subdirectory=packages/wedge' wedge`.

The [consumer example](../../packages/wedge/examples/consumer/) shows this skill
directory layout.

## Workflows

### Check pull requests and merge groups

Use a merge queue to check the combined tree before merging changes to shared
skill sources, dependencies, or packaging configuration. Two pull requests can
pass separately while their combined inputs invalidate a lock or bundle.
The queue tests each candidate with the current base and earlier queued changes.
See GitHub's [merge queue guide](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue).

Check locks on both pull requests and merge groups:

```yaml
name: wedge-check
on:
  pull_request:
  merge_group:
    types: [checks_requested]
permissions:
  contents: read
jobs:
  check:
    name: wedge locks
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<sha>
      - uses: paulnsorensen/skillz-that-grillz/actions/wedge@<sha>
        with:
          command: check
          roots: skills
```

Configure the target branch after this workflow is available:

1. Require the `wedge locks` status check and your normal test checks.
2. Enable **Require merge queue** in the branch ruleset or branch protection settings, where GitHub supports it.
3. Ensure every required test workflow handles `merge_group`, not only `pull_request`.
4. Queue pull requests instead of bypassing the queue or pushing directly to the protected branch.

Keep the event's default checkout. Do not set `ref: main` or the pull request's
head SHA in the check job: that skips the merge-group tree. Avoid workflow path
filters or job conditions that omit required packaging checks. If validation
uses concurrency, include `${{ github.ref }}` in its group so different merge
groups do not cancel each other.
GitHub documents the [required `merge_group` trigger](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#merge_group).

The action's `check` command validates legacy locks and launchers, not direct
bundles. For committed `.pyz` files, run `wedge bundle --root skills --check`
through your pinned wedge installation instead. When CI creates bundles only
for distribution, build and test them from the merge-group checkout.
See [direct bundles and the easy-cheese example](../../packages/wedge/README.md#merge-queues-and-publication-races).

If a queue check finds a stale lock or bundle, update the pull request against
the current base. Regenerate the affected artifacts with the pinned wedge
version, commit them, and queue the pull request again. Do not regenerate them
inside a check job and report success without checking the committed artifacts.

GitHub documents queue support for public organization repositories and private
organization repositories on Enterprise Cloud. Check availability for your
repository; workflow YAML alone does not enable a queue. Where queues are
unavailable, require up-to-date branches and revalidate after each merge.
Coordinate merges to avoid reusing checks from an older base.

### Publish release assets after merging

Publish after a merge to the default branch. Give `contents: write` only to
this job. `branch: main` makes `publish` refuse unless `target` resolves to
the checked-out HEAD of every skill directory. `publish` then confirms that
commit is on `main`, so a manual dispatch from another ref cannot publish:

```yaml
name: wedge-publish
on:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@<sha>
      - uses: paulnsorensen/skillz-that-grillz/actions/wedge@<sha>
        with:
          command: publish
          roots: skills
          branch: main
```

Keep `publish` on the trusted post-merge branch, not on `pull_request` or
`merge_group`. The queue validates integration; it does not serialize publication
jobs or make release assets available before upload finishes.

Release-asset publication is immutable and append-only, so concurrent runs are
safe. Preserve each commit's run: an older lock can still need its unique asset.
Do not add a shared concurrency group that drops pending publication runs.
With the default concurrency queue, `cancel-in-progress: false` protects only
the running job; a new pending run replaces the previous pending run.
See GitHub's [concurrency reference](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

This differs from publishing a complete, latest-state `dist` branch. That model
can replace pending work only when a later run includes all missed changes.
See the [easy-cheese publication example](../../packages/wedge/README.md#example-easy-cheese).

Pin every action to a full commit SHA, as GitHub's
[secure use guide](https://docs.github.com/en/actions/reference/security/secure-use)
recommends.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `command` | `publish` | `check` or `publish`. |
| `roots` | `skills` | Space-separated roots. The action finds `*/wedge.toml` under each root. |
| `repo` | `${{ github.repository }}` | Repository that hosts the `wedge` release. `publish` refuses a lock that names another repository. |
| `target` | `${{ github.sha }}` | Commit for the `wedge` release if `publish` creates it. |
| `branch` | none | With `publish`, refuse unless `target` resolves to every skill's checked-out HEAD. Then compare that commit against this branch through the compare API. |
| `token` | `${{ github.token }}` | Token for `gh`. `publish` needs `contents: write`. |

## Outputs

| Output | Description |
| --- | --- |
| `result` | The JSON document that `wedge` printed. |

## Limits

- `publish` runs only on Linux X64 runners, because it installs a pinned
  `linux_amd64` `gh` binary. `check` runs on any runner.
- The `.pyz` closure must be pure Python. Wedge refuses platform wheels,
  platform markers or unexportable requirements. Vendor local packages through `include`; URL and unsupported requirements must be replaced with vendored source.
- The launcher and the build need Python 3.11 or later.
- Wedge never deletes an old asset from the `wedge` release.

The design records are in
[wedge skill packaging decisions](../../docs/agents/decisions/wedge-skill-packaging.md).

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

The action runs `wedge` from its own checkout (the `lib/` project beside this
directory). When you pin the action to a commit SHA, you also pin `wedge`. A
repository that already pins `wedge` in its own `uv.lock` (step 3 below) can
run the same commands with `uv run --locked --only-group wedge wedge …` in
its workflows instead, so one pin serves local runs and CI.

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
   publishes a library.
3. Pin `wedge` in the project's own `uv.lock` through a dependency group
   that stays out of `default-groups`, so it never enters the `.pyz` closure:

   ```toml
   [dependency-groups]
   wedge = ["skillz-that-grillz @ git+https://github.com/paulnsorensen/skillz-that-grillz@<sha>#subdirectory=lib"]
   ```

4. Write the locks and the launchers. Run this from the repository root and
   commit each `scripts/<name>` and `scripts/<name>.wedge.json`:

   ```sh
   uv run --locked --only-group wedge wedge lock --root skills
   ```

   Every command finds `*/wedge.toml` under `--root`; `lock`, `build`, and
   `publish` run the skills in parallel and share one installed site
   directory between skills that share a project. Use the same `<sha>` that
   your workflows pin. A different `wedge` version can compute a different
   key. A project without uv can run the same command through
   `uvx --from 'skillz-that-grillz @ git+…@<sha>#subdirectory=lib' wedge`.

The [consumer example](../../lib/examples/consumer/) shows this layout.

## Workflows

Check locks on every pull request:

```yaml
name: wedge-check
on: pull_request
permissions:
  contents: read
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<sha>
      - uses: paulnsorensen/skillz-that-grillz/actions/wedge@<sha>
        with:
          command: check
          roots: skills
```

Publish after a merge to the default branch. Give `contents: write` only to
this job, and publish only commits that are on the default branch:

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
        with:
          fetch-depth: 0
      - name: Verify HEAD is on main
        run: |
          git fetch origin main
          git merge-base --is-ancestor "$GITHUB_SHA" origin/main
      - uses: paulnsorensen/skillz-that-grillz/actions/wedge@<sha>
        with:
          command: publish
          roots: skills
```

Publication is immutable and append-only, so concurrent runs are safe. Do not add cancellation that can drop a pending asset publication.

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

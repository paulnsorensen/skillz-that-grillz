# wedge GitHub Action

`wedge` packages a pure-Python skill CLI as one `.pyz` file. Your repository
commits only a small launcher and a lock with the `.pyz` sha256. This action
builds the `.pyz` reproducibly and uploads it to a rolling `wedge` prerelease.
The launcher downloads the asset on first use and runs it only when its sha256
matches the lock.

The action has two commands:

- `check` fails when a lock is missing or stale, or when a launcher differs
  from the template. It builds nothing and needs no write access.
- `publish` builds each locked skill and uploads assets that are missing. It
  skips assets that are already present with the same digest.

The action runs `wedge` from its own checkout (the `lib/` project beside this
directory). When you pin the action to a commit SHA, you also pin `wedge`.

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
   repo = "your-org/your-repo"       # repository that hosts the release
   ```

   `source` and each `include` entry must be inside `project`. Use `include`
   for a local package that is not on an index, such as `fromargs`.
3. Write the lock and the launcher. Run this from the repository root and
   commit `scripts/<name>` and `scripts/<name>.wedge.json`:

   ```sh
   uvx --from 'skillz-that-grillz @ git+https://github.com/paulnsorensen/skillz-that-grillz@<sha>#subdirectory=lib' \
     wedge lock skills/hello
   ```

   Use the same `<sha>` that your workflows pin. A different `wedge` version
   can compute a different key.

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
concurrency:
  group: wedge
  cancel-in-progress: false
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
  platform markers, and editable, path, or URL dependencies.
- The launcher and the build need Python 3.11 or later.
- Wedge never deletes an old asset from the `wedge` release.

The design records are in
[wedge skill packaging decisions](../../docs/agents/decisions/wedge-skill-packaging.md).

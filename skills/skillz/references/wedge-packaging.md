# Build and install with wedge

Read this before you wedge a CLI in the packaged skill.
These instructions describe the current builder, not a general Python packager.

## Prerequisites and limits

The packaged skill needs a uv project.
That project has a `pyproject.toml` and a committed `uv.lock`.
The non-dev dependencies in that lock form the archive's closure.
A dependency group named in `groups` joins that closure.
A local package that is not on an index, such as fromargs, enters through `include`.

Run the builder from `scripts/wedge.pyz` under the loaded `/skillz` `SKILL.md` directory; it carries wedge and its shiv dependency.
The builder also invokes `uv`, so `uv` must be on `PATH`.
Every dependency must be a pure-Python `py3-none-any` wheel without a platform marker.
A compressed universal tag such as `py2.py3-none-any` also passes.
The builder rejects platform wheels and path or URL requirements.
The runtime requires Python 3.11 or later.
An installed launcher does not require uv or a project environment.

Do not add unrelated dependencies to a shared project to make packaging pass.
Do not change the `packages/fromargs` closure of the skillz-that-grillz repository.
When the layout or a dependency is not supported, ship the CLI unpackaged and state the reason.
Installing the wedge command alone does not create the required project.

## Manifest and source

Copy the bundled manifest and Python template into the packaged skill.
Set `repo` explicitly to the release repository as `owner/name`.
The template's placeholder fails validation until you replace it.

```text
packaged-skill/
  SKILL.md
  wedge.toml
  records.py
  scripts/
    records
    records.wedge.json
```

The manifest accepts eight keys: `name`, `entry`, `source`, `source_paths`, `project`, `repo`, `include`, and `groups`.
It also accepts `[[target]]` tables, described below.
The template sets `name`, `entry`, `source`, `project`, and `repo`.
`entry = "records:main"` names the callable in `records.py`.
Every path resolves relative to the packaged skill, not the working directory.
`project` names the directory with `pyproject.toml` and `uv.lock`.
`source` and each `include` entry must resolve inside `project`.
Choose a single module file or an importable package directory, not its generic `src` parent.
Use `source_paths` only to select paths below a `source` directory.
Build inputs and source paths cannot contain symlinks.

The template sets `project = "../.."`, which suits a skill at `skills/NAME` in a repository root project.
Change it when the project sits elsewhere.
Wedge only when fromargs resolves. Supply it in one of two ways:

- Add `fromargs` to that project's dependencies and commit the updated `uv.lock`. This is a dependency write; name it in the approval question.
- Vendor a local fromargs source tree with `include`, for example `include = ["../../src/fromargs"]`.

Use `groups` when the CLI dependencies sit in a uv dependency group other than `dev`.
Shared defaults for many skills may sit in a `wedge.toml` in their parent directory.
A parent directory that holds a `SKILL.md` is a skill, so it gives no shared defaults.

### Several CLIs in one skill

Wedge reads one `wedge.toml` per directory, so each extra CLI gets its own directory below the skill.
Place each manifest at `<skill>/<cli>/wedge.toml`, with its source beside it.
Add one `../` to `project` and to each `source` and `include` path.
A nested CLI reads no shared defaults.

A direct-mode manifest can instead hold several CLIs through `[[target]]` tables.
Use either one directory per CLI or `[[target]]` tables; both layouts work.
The root `skills/skillz/wedge.toml` in this repository shows the `[[target]]` layout.
Each table sets `name`, `entry`, `source` or `source_paths`, `include`, and `groups`.
Keys outside the tables, such as `project` and `repo`, act as defaults for every table.
A table value replaces the default.
Do not set top-level `name` or `entry` beside a `[[target]]` table; wedge rejects that mix.
Target names must differ.
`wedge bundle` writes `scripts/<name>.pyz` for each target, and `bundle --check` checks each one.
`wedge build` also builds every target.
`lock`, `publish` (release mode), and `check` reject `[[target]]` tables.
`publish` skips each target that is a direct bundle.

Code can also reuse one third-party install across builds through the site layer in `wedge._build`.
`populate_site_layer` installs the locked closure once and may use the network.
`open_site_layer` reopens that layer later and refuses changed inputs.
`resolve_target` and `stage_sources` stage the first-party files of one target, with an optional overlay of edited files.
`build_from_layer` copies the layer and the staged files into a fresh tree and runs shiv with no network.
The layer key covers the wedge format version, the `uv.lock` digest, the groups, and the target Python.
First-party source never enters the layer, and a build leaves the layer unchanged.
The output matches a fresh `build` byte for byte.

The skillz experiment runner also generates targets. Its `@new-cli` component appends one `[[target]]` table to the `wedge.toml` of a candidate.
The generated table sets `groups = []` and builds from the site layer of the project with no groups.
The experiments reference describes the component.

Keep reusable source inside the packaged skill when possible.
Ship source and manifest with the generated launcher and lock.
In this default mode, the archive belongs in release assets, not version control.
A `.pyz` beside a launcher fails `wedge check`, so never hand-copy one into a launcher skill.
For consumers that cannot fetch at first run, `wedge bundle` and `wedge bundle --check` offer an opt-in direct mode.
That mode commits `scripts/NAME.pyz` and uses no lock or launcher.

## Local verification

Set `WEDGE` to `scripts/wedge.pyz` under the loaded `/skillz` `SKILL.md` directory.
Set `SKILL_DIR` to the absolute packaged skill directory.
Set `OUT_DIR` to a temporary build-output directory.

```bash
python3 "$WEDGE" lock "$SKILL_DIR"
python3 "$WEDGE" build "$SKILL_DIR" --out "$OUT_DIR"
python3 "$WEDGE" check "$SKILL_DIR"
```

For CI, pin wedge in a locked tool project, for example `tools/wedge`, and run `uv run --locked --project tools/wedge wedge …`.
The [wedge action README](https://github.com/paulnsorensen/skillz-that-grillz/blob/main/actions/wedge/README.md) explains that pin.
Run `lock` with the same wedge revision that CI pins.
A different builder can change the archive bytes, and `publish` then rejects the lock digest.

`lock` builds and writes `scripts/NAME` and `scripts/NAME.wedge.json`.
Regenerate both after a manifest, source, fromargs, or dependency-lock change.
Do not edit generated files by hand.

`build` prints JSON keyed by skill name: `{name: {key, content_sha256, path}}`.
It does not update the lock.
Compare `.<name>.content_sha256` with the lock's `content_sha256`.
`check` checks input freshness and generated metadata without building.
It does not prove that the remote archive exists or that the command works.

Copy or install the packaged skill into a separate directory.
Read `.<name>.path` from the `build` output and use it as `BUILT_PYZ`.
Resolve `INSTALLED_SKILL_DIR` from that installation's loaded `SKILL.md`, never the caller's working directory.

From an unrelated working directory, run:

```bash
WEDGE_PYZ="$BUILT_PYZ" python3 "$INSTALLED_SKILL_DIR/scripts/records" counts "$INPUT"
WEDGE_PYZ="$BUILT_PYZ" python3 "$INSTALLED_SKILL_DIR/scripts/records" counts "$INPUT" --full
```

The launcher verifies the local archive against its adjacent committed lock.
An archive with a changed member fails with status 3.
Bytes outside the archive members, such as trailing data, do not change the content digest.
To test rejection, change the content of one member, not the file's tail.
A successful override verifies local packaging, not network publication.

## Runtime contract

This section is the single description of what a wedge archive needs when it runs.
It applies in launcher mode and in direct mode.
State it in the install docs of every skill that ships a wedge archive.

Every `.pyz` that wedge builds is a shiv archive.
On the first run of each build, the archive unpacks its dependencies into a cache root.
The archive picks the root in this order:

1. `SHIV_ROOT`, when it is set and not empty.
2. `XDG_CACHE_HOME/shiv`, when `XDG_CACHE_HOME` is an absolute path.
3. `~/.cache/shiv`.

Wedge archives do not use `~/.shiv`.
Only the first run of a new build writes to the root.
A later run of an unpacked build only reads it.

When the archive cannot write the root for a new build, it prints one JSON line to stderr and exits with status 3:

```json
{"error": "cannot extract this archive into SHIV_ROOT=<root>: <OS error>; set SHIV_ROOT to a writable directory", "exit_code": 3}
```

To fix the error, set `SHIV_ROOT` to an absolute path that the process can write.

After the archive unpacks a new build, it deletes old builds of the same archive name in the same root.
The name ignores a `-<12 hex digits>` content-digest suffix, so the release assets of one skill count as one name.
The archive keeps the newest other build and every build younger than one day, because another session can still run one.
It does not delete symlinks, files, or entries with other names.
You can delete the cache root at any time; the next run unpacks the build again.

### Run in an agent sandbox

An agent harness can run shell commands in a sandbox that allows writes only to some paths.
Put the cache root below one of those paths before the first run of a new build.

In Claude Code, sandboxed Bash can write only to the working directory, the per-user temporary directory, and directories that you add.
Use one of these settings ([Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing)):

- Add the parent of the default cache root to `sandbox.filesystem.allowWrite`. `~/.cache` is a common entry.
- Set `SHIV_ROOT` in the settings `env` block to an absolute path that the sandbox can write. Do not use `~` in that value; the documentation does not say that `env` expands it.

```json
{
  "sandbox": {"filesystem": {"allowWrite": ["~/.cache"]}},
  "env": {"SHIV_ROOT": "/home/<user>/.cache/shiv"}
}
```

Some wedged CLIs start their own bubblewrap sandbox.
On Linux, a command in the Claude Code sandbox cannot create user namespaces by default.
Bubblewrap needs them, so such a CLI cannot run nested in that sandbox.
Use one of these options:

- Run the CLI from a normal shell, outside the agent.
- Add a `sandbox.excludedCommands` entry for the CLI, for example `python3 /path/to/skill/scripts/<name>.pyz *`.
  A matching call runs with no sandbox: it has no filesystem limits and no network proxy.
  Claude Code matches the text of the call, so a script that calls the CLI does not match.
  Every command in a chained call must match an entry, or the whole call stays in the sandbox.

Other harnesses have their own sandbox settings.
In each one, make the cache root writable, or set `SHIV_ROOT` to a writable absolute path.

## Distribution contract for direct mode on a dist branch

This section applies when a repository keeps source on `main` and publishes built direct archives on a `dist` branch.
In that model, CI builds `scripts/<name>.pyz` and appends the source and the archives to `dist`, and `dist` is the default branch.
The skillz-that-grillz repository uses this model ([ADR-012](https://github.com/paulnsorensen/skillz-that-grillz/blob/main/docs/agents/decisions/wedge-skill-packaging.md)).

- An installer must follow the remote default branch. `npx skills add` and a plain `git clone` do.
  A vendoring cache that keeps the branch it checked out first does not follow a change of the default branch.
  Such a cache can stay on `main` and install the skill with no archives ([paulnsorensen/dotfiles#1489](https://github.com/paulnsorensen/dotfiles/issues/1489)).
- A release tag on a `main` commit has no archives. An install from that tag gets source only.

State both facts in the install docs of your skill.
Also link the runtime contract above, so that users can make the cache root writable.

## Distribution and first run

Without `WEDGE_PYZ`, the launcher fetches its content-addressed asset from the repository's rolling `wedge` release.
First use needs network access and a published asset.
Later runs use a verified cache under `WEDGE_CACHE`, or `XDG_CACHE_HOME/wedge`, or `~/.cache/wedge`.
The launcher rejects mismatched archive hashes before execution.

Publication changes remote release state.
Only run it when authorized, using the actual release repository and the merged commit:

```bash
python3 "$WEDGE" publish --root "$SKILLS_ROOT" --repo "$RELEASE_REPO" --target "$COMMIT" --branch "$DEFAULT_BRANCH"
```

`--target` must be the checked-out, merged `HEAD`, which the default branch contains.
`--branch` makes wedge refuse any other commit before it changes release state.
Without `--branch`, that check does not run.

This command discovers skills under the supplied root.
Check its scope before publication.
If the repository has a post-merge publication workflow, use that workflow instead of duplicating it.
The skillz-that-grillz repository already has that workflow.

## Sources

The [wedge implementation](https://github.com/paulnsorensen/skillz-that-grillz/tree/main/packages/wedge/src/wedge) defines the supported builder and launcher.
The [Agent Skills specification](https://agentskills.io/specification) defines portable skill resources and progressive loading.
The [Anthropic engineering guide](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) explains bundled executable code for repeatable work.

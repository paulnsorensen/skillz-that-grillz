# Build and install with wedge

Read this before preparing the target skill.
These instructions describe the current builder, not a general Python packager.

## Prerequisites and limits

The target skill needs a uv project.
That project has a `pyproject.toml` and a committed `uv.lock`.
The non-dev dependencies in that lock form the archive's closure.
A dependency group named in `groups` joins that closure.
A local package that is not on an index, such as fromargs, enters through `include`.

Run the builder from this skill's bundled `scripts/wedge.pyz`; it carries wedge and its shiv dependency.
The builder also invokes `uv`, so `uv` must be on `PATH`.
Every dependency must be a pure-Python `py3-none-any` wheel without a platform marker.
A compressed universal tag such as `py2.py3-none-any` also passes.
The builder rejects platform wheels and path or URL requirements.
The runtime requires Python 3.11 or later.
An installed launcher does not require uv or a project environment.

Do not add unrelated dependencies to a shared project to make packaging pass.
Do not change the `lib/fromargs` closure of the skillz-that-grillz repository.
Stop and explain an unsupported layout or dependency requirement.
Installing the wedge command alone does not create the required project.

## Manifest and source

Copy the bundled manifest and Python template into the target skill.
Set `repo` explicitly to the release repository as `owner/name`.
The template's placeholder fails validation until you replace it.

```text
target-skill/
  SKILL.md
  wedge.toml
  records.py
  scripts/
    records
    records.wedge.json
```

The manifest accepts eight keys: `name`, `entry`, `source`, `source_paths`, `project`, `repo`, `include`, and `groups`.
The template sets `name`, `entry`, `source`, `project`, and `repo`.
`entry = "records:main"` names the callable in `records.py`.
Every path resolves relative to the target skill, not the working directory.
`project` names the directory with `pyproject.toml` and `uv.lock`.
`source` and each `include` entry must resolve inside `project`.
Choose a single module file or an importable package directory, not its generic `src` parent.
Use `source_paths` only to select paths below a `source` directory.
Build inputs and source paths cannot contain symlinks.

The template sets `project = "../.."`, which suits a skill at `skills/NAME` in a repository root project.
Change it when the project sits elsewhere.
Supply fromargs in one of two ways:

- Add `fromargs` to that project's dependencies and commit the updated `uv.lock`.
- Vendor a local fromargs source tree with `include`, for example `include = ["../../src/fromargs"]`.

Use `groups` when the CLI dependencies sit in a uv dependency group other than `dev`.
Shared defaults for many skills may sit in a `wedge.toml` in their parent directory.

Keep reusable source inside the target skill when possible.
Ship source and manifest with the generated launcher and lock.
In this default mode, the archive belongs in release assets, not version control.
A `.pyz` beside a launcher fails `wedge check`, so never hand-copy one into a launcher skill.
For consumers that cannot fetch at first run, `wedge bundle` and `wedge bundle --check` offer an opt-in direct mode.
That mode commits `scripts/NAME.pyz` and uses no lock or launcher.

## Local verification

Set `WEDGE` to `scripts/wedge.pyz` under the loaded `/wedge` `SKILL.md` directory.
Set `SKILL_DIR` to the absolute target skill directory.
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

Copy or install the target skill into a separate directory.
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

The [wedge implementation](https://github.com/paulnsorensen/skillz-that-grillz/tree/main/lib/src/wedge) defines the supported builder and launcher.
The [Agent Skills specification](https://agentskills.io/specification) defines portable skill resources and progressive loading.
The [Anthropic engineering guide](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) explains bundled executable code for repeatable work.

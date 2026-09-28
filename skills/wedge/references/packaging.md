# Build and install with wedge

Read this before preparing the target skill.
These instructions describe the current builder, not a general Python packager.

## Prerequisites and limits

The target skill must sit inside a supported build checkout.
An ancestor must contain:

- `lib/fromargs/src/fromargs/`
- `lib/fromargs/uv.lock`
- `lib/fromargs/pyproject.toml` for the frozen dependency export

Use the locked `lib` project for the wedge builder and its shiv dependency.
The builder also invokes `uv`.
The runtime requires Python 3.11 or later.
An installed launcher does not require uv or a project environment.

Wedge bundles fromargs and its frozen, pure-Python dependency closure.
A skill's separate requirements file or project dependencies are not automatically included.
Do not add unrelated dependencies to the shared fromargs closure to make packaging pass.
Stop and explain an unsupported layout or dependency requirement.
Installing the wedge command alone does not create the required build layout.

## Manifest and source

Copy the bundled manifest and Python template into the target skill.
Set `repo` explicitly to the release repository.
Replace the example `OWNER/REPO` before distribution.

```text
target-skill/
  SKILL.md
  wedge.toml
  records.py
  scripts/
    records
    records.wedge.json
```

The manifest has flat `name`, `entry`, `source`, and `repo` keys.
`entry = "records:main"` names the callable in `records.py`.
`source` resolves relative to the target skill, not the working directory.
Keep it inside the build repository.
Choose a single module file or an importable package directory, not its generic `src` parent.
Build inputs and source paths cannot contain symlinks.

Keep reusable source inside the target skill when possible.
Ship source and manifest with the generated launcher and lock.
The archive belongs in release assets, not version control.

## Local verification

Set `REPO_ROOT` to the supported build checkout.
Set `SKILL_DIR` to the absolute target skill directory.
Set `OUT_DIR` to a temporary build-output directory.

```bash
uv run --locked --project "$REPO_ROOT/lib" wedge lock "$SKILL_DIR"
uv run --locked --project "$REPO_ROOT/lib" wedge build "$SKILL_DIR" --out "$OUT_DIR"
uv run --locked --project "$REPO_ROOT/lib" wedge check "$SKILL_DIR"
```

`lock` builds and writes `scripts/NAME` and `scripts/NAME.wedge.json`.
Regenerate both after a manifest, source, fromargs, or dependency-lock change.
Do not edit generated files by hand.

`build` returns JSON containing the archive's `path`, `key`, and `sha256`.
It does not update the lock.
`check` checks input freshness and generated metadata without building.
It does not prove that the remote archive exists or that the command works.

Copy or install the target skill into a separate directory.
Use the returned archive path as `BUILT_PYZ`.
Resolve `INSTALLED_SKILL_DIR` from that installation's loaded `SKILL.md`, never the caller's working directory.

From an unrelated working directory, run:

```bash
WEDGE_PYZ="$BUILT_PYZ" python3 "$INSTALLED_SKILL_DIR/scripts/records" counts "$INPUT"
WEDGE_PYZ="$BUILT_PYZ" python3 "$INSTALLED_SKILL_DIR/scripts/records" counts "$INPUT" --full
```

The launcher verifies the local archive against its adjacent committed lock.
A modified archive must fail with status 3.
A successful override verifies local packaging, not network publication.

## Distribution and first run

Without `WEDGE_PYZ`, the launcher fetches its content-addressed asset from the repository's rolling `wedge` release.
First use needs network access and a published asset.
Later runs use a verified cache under `WEDGE_CACHE`, or `XDG_CACHE_HOME/wedge`, or `~/.cache/wedge`.
The launcher rejects mismatched archive hashes before execution.

Publication changes remote release state.
Only run it when authorized, using the actual release repository and target commit:

```bash
uv run --locked --project "$REPO_ROOT/lib" wedge publish --root "$SKILLS_ROOT" --repo "$RELEASE_REPO" --target "$COMMIT"
```

This command discovers skills under the supplied root.
Check its scope before publication.
If the repository has a post-merge publication workflow, use that workflow instead of duplicating it.
The skillz-that-grillz repository already has that workflow.

## Sources

The [wedge implementation](https://github.com/paulnsorensen/skillz-that-grillz/tree/main/lib/src/wedge) defines the supported builder and launcher.
The [Agent Skills specification](https://agentskills.io/specification) defines portable skill resources and progressive loading.
The [Anthropic engineering guide](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) explains bundled executable code for repeatable work.

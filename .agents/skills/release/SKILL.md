---
name: release
description: >-
  Release the fromargs Python distribution to PyPI and GitHub from this repository.
  Prepares the CHANGELOG, version, pins, locks, and wedge bundles in a release PR,
  then merges it, tags `fromargs-v<version>`, approves the `pypi` deployment, and
  verifies the published artifacts. Use when a maintainer asks to "release fromargs",
  "cut a fromargs release", "publish fromargs <version>", or invokes /release.
  Do NOT use for the published skills or the wedge action.
argument-hint: "[<version>]"
disable-model-invocation: true
license: MIT
metadata:
  internal: true
---

# Release fromargs

This is a repository-local skill for skillz-that-grillz. It lives under `.agents/skills/release/`, with a `.claude/skills/` symlink for Claude Code. `metadata.internal: true` hides it from `npx skills add`. Do not move it into the published `skills/` tree.

A tag push publishes to PyPI after one approval, and PyPI never accepts the same version twice. Treat the tag push and the approval as irreversible.

## Release contract

- `lib/fromargs/CHANGELOG.md` is the only source of release notes. `just fromargs-release-notes <version>` prints one section and fails when it is missing or empty.
- `.github/workflows/publish-fromargs.yml` runs on a `fromargs-v*` tag. Its build job requires the tag commit on `main` and the tag version equal to `lib/fromargs/pyproject.toml`.
- The `publish` job waits for the `pypi` environment approval, then uploads with trusted publishing.
- The `github-release` job creates the GitHub release with `--latest=false`. It fails when the tag moved after the build.
- The wedge bundles and the `cheese-cave` example lock embed the fromargs source, so a version change needs a rebuild.

## 1. Preflight

1. Switch to `main`, pull with `--ff-only`, and require a clean tree.
2. Read the `## [Unreleased]` section of `lib/fromargs/CHANGELOG.md`. Stop when it is empty.
3. Choose the version. Use the argument when the user gives one. Otherwise, propose the next minor version for a `0.x` release with a **Breaking** entry, and the next patch version for other changes. Confirm the proposed version with the user.
4. Stop when the tag `fromargs-v<version>` exists on `origin` or `https://pypi.org/pypi/fromargs/<version>/json` returns 200.

## 2. Prepare the release PR

1. Create the branch `chore/fromargs-release-<version>`.
2. In `lib/fromargs/CHANGELOG.md`, move the `Unreleased` entries under `## [<version>] - <YYYY-MM-DD>` with today's date. Keep an empty `## [Unreleased]` heading.
3. Update the link references at the end of the CHANGELOG. `[Unreleased]` compares `fromargs-v<version>...HEAD`. Add `[<version>]` to compare the previous tag with `fromargs-v<version>`.
4. Set `version = "<version>"` in `lib/fromargs/pyproject.toml`.
5. When the minor version changes, set the `fromargs` range in `pyproject.toml` and `lib/pyproject.toml` to `>=<major>.<minor>.0,<<major>.<next minor>`.
6. Relock with the uv version that the workflow pins in its `setup-uv` step, so the lockfile `revision` stays compatible with CI:

   ```bash
   uvx uv@<ci-uv-version> lock --project .
   uvx uv@<ci-uv-version> lock --project lib
   uvx uv@<ci-uv-version> lock --project lib/fromargs
   ```

7. Rebuild the artifacts that embed fromargs:

   ```bash
   uv run --locked --project lib wedge lock --root lib/examples/skills
   uv run --locked --project lib wedge bundle skills/skillz skills/skillz/wedge
   ```

8. Run `just fromargs-release-notes <version>` and read the output. It must show the complete new section.
9. Run `uv build --project lib/fromargs --out-dir <temp dir>` and `uvx twine check <temp dir>/*`.
10. Run `just build`. Stop on a new failure. Compare any failure with a clean `main` checkout before you call it a baseline failure.
11. Commit the named files as `chore(fromargs): release <version>`. Push the branch and open a PR against `main`. Put the release notes and the post-merge steps in the PR body.

## 3. Merge

1. Wait for every PR check to pass. Resolve or answer every review thread.
2. Get explicit user approval to merge, unless the user asked for the complete release in this session.
3. Squash-merge with `gh pr merge <n> --squash --match-head-commit <sha>`. Record the merge commit SHA.
4. When the merge date differs from the CHANGELOG date, fix the date in a new PR before you tag.

## 4. Tag and approve

1. Fetch `main`. Confirm that the merge commit is on `origin/main` and that it has the new version and CHANGELOG section.
2. Create and push an annotated tag on the merge commit:

   ```bash
   git tag -a fromargs-v<version> <merge-sha> -m "Release fromargs <version>"
   git push origin fromargs-v<version>
   ```

3. Find the `publish fromargs` run for the tag. Wait until the build job passes and the run waits on the `pypi` environment.
4. Approve the deployment only after the user approves it, unless the user asked for the complete release in this session:

   ```bash
   gh api repos/<owner>/<repo>/actions/runs/<run-id>/pending_deployments \
     -X POST -F "environment_ids[]=<pypi-env-id>" -f state=approved \
     -f comment="Approve fromargs <version>"
   ```

## 5. Verify

1. Watch the run to completion. Every job must succeed.
2. Read `https://pypi.org/pypi/fromargs/<version>/json`. Confirm the version, the dependency ranges, and one wheel and one sdist.
3. Read `gh release view fromargs-v<version>`. Confirm that it is not a draft, that its body starts with the CHANGELOG section, and that both dist files are attached.
4. Confirm that the repository's latest release did not change.

## Failure rules

- Never move, delete, or re-push a published tag. A new fix needs a new patch version.
- When the build job fails, no upload happened. Fix the cause on `main`, delete the unpublished tag only with user approval, and tag again.
- When PyPI has the version but the `github-release` job failed, re-run only the failed job with `gh run rerun <run-id> --failed`.
- Never approve a deployment for a run whose tag does not point at the reviewed merge commit.

## Report

Report the PR, the merge commit, the tag, the workflow run, the PyPI URL, and the GitHub release URL. Name every check that did not run.

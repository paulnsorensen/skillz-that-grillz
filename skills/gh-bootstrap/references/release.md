# Release notes and the release workflow

Reference detail for steps 4 and 5 of the protocol: `.github/release.yml` (release-notes config) and `.github/workflows/release.yml` (the optional tag-driven workflow).

Reference: <https://docs.github.com/en/repositories/releasing-projects-on-github/automatically-generated-release-notes>

## `.github/release.yml`

This file is consumed by GitHub's auto-generated release notes — both the "Generate release notes" button on the New Release page and `gh release create --generate-notes`. It groups merged PRs into category buckets driven by PR labels.

The template lives in `assets/release.yml`. Its shape:

```yaml
changelog:
  exclude:
    labels: [...]      # PRs with these labels are dropped from notes
    authors: [...]     # PRs from these authors are dropped (e.g. dependabot)
  categories:
    - title: <bucket name>
      labels: [...]    # PRs with any of these labels go in this bucket
    # ...
    - title: Other changes
      labels: ["*"]    # catch-all — keep last
```

### Required labels

The template references these labels. Create them in the repo (Settings → Labels) so contributors can apply them:

| Label | Bucket |
|---|---|
| `breaking-change`, `breaking` | Breaking changes |
| `enhancement`, `feature` | Features |
| `bug`, `bugfix`, `fix` | Fixes |
| `documentation`, `docs` | Documentation |
| `dependencies` | Dependencies |
| `chore`, `refactor`, `ci`, `build`, `test` | Internal |
| `ignore-for-release`, `skip-changelog` | (excluded — not a bucket) |

`enhancement` and `bug` are GitHub defaults — they exist on every new repo. The others need to be created. One-shot:

```bash
for label in breaking-change feature fix docs dependencies chore refactor ci build test ignore-for-release skip-changelog; do
  gh label create "$label" --force >/dev/null
done
```

### Customizing categories

Two common adjustments:

1. **Emoji prefixes in category titles** — `title: "✨ Features"` etc. The auto-generated notes preserve the title verbatim, so emojis carry through. Keep the file ASCII unless the team is committed to emoji headers; rendering varies in some pipelines.
2. **Drop the dependencies category** — if Dependabot updates already get auto-merged and you don't want them showing up at release time, move `dependencies` to `exclude.labels` and remove the corresponding category.

### Author exclusions

`exclude.authors` drops PRs from listed accounts entirely. The template excludes `dependabot`, `github-actions`, and `renovate`. If you actually want dependency PRs in release notes, remove `dependabot` here and keep the `dependencies` category.

## `.github/workflows/release.yml`

The template lives in `assets/release-workflow.yml`. It triggers on `push` to tags matching `v[0-9]*` and creates a GitHub Release with auto-generated notes.

### Tag-on-main guard

The workflow refuses to publish if the tag isn't an ancestor of `origin/main`:

```bash
if ! git merge-base --is-ancestor "$GITHUB_SHA" origin/main; then
  echo "::error::Tag … is not on origin/main. Refusing."
  exit 1
fi
```

This pairs with the default-branch ruleset: if direct pushes to `main` are blocked, then everything reachable from `main` has been through PR review, and a tag on `main` therefore covers only reviewed code. A tag pushed straight to a side branch would bypass that — the guard catches it.

### `--verify-tag`

`gh release create --verify-tag` ensures the tag exists in the repo (not just locally) before creating the release. Cheap insurance against publishing a release pointing at a tag that's only on someone's laptop.

### Action SHA pinning

The template pins `actions/checkout` to a full commit SHA, not a floating tag like `@v4`. This is supply-chain hygiene: a maintainer of a popular action can retag `v4` to a malicious commit and any workflow using `@v4` picks it up on the next run. SHA pins don't.

To refresh the pin when a new release comes out:

```bash
gh api repos/actions/checkout/git/refs/tags/v6.0.2 --jq '.object.sha'
```

Then update the `uses:` line:

```yaml
uses: actions/checkout@<new-sha> # v6.0.2
```

The `# v6.0.2` trailing comment is what tells the next reader (or Dependabot) which version that SHA corresponds to. Dependabot understands both the SHA and the comment when configured for `package-ecosystem: github-actions`.

### Permissions

`permissions: { contents: write }` is the minimum — it's needed to create the release. Don't grant more (no `pull-requests`, no `id-token`) unless you're adding artifact attestations or PR comments to the workflow. Keep the principle of least privilege.

### Concurrency

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false
```

`cancel-in-progress: false` matters here: if two release jobs are queued (e.g. someone pushes `v1.0.0` and then `v1.0.1` quickly), you want both to publish, not for the second to cancel the first. Differs from CI workflows where `cancel-in-progress: true` is usually correct.

## Cutting a release

Once both files are in place:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The workflow runs, verifies the tag is on `main`, calls `gh release create v1.0.0 --generate-notes`, and the release shows up in the Releases tab with categorized notes.

To preview the notes locally without actually cutting a release:

```bash
gh api -X POST "repos/$REPO/releases/generate-notes" \
  -f tag_name=v1.0.0 \
  -f previous_tag_name=v0.9.0 \
  --jq '.body'
```

This honors `.github/release.yml` and shows exactly what the release body would look like. Useful for verifying labels are correctly buckets-ing PRs before tagging.

## Manual release without the workflow

If the user doesn't want the workflow but still wants categorized notes:

```bash
gh release create v1.0.0 --generate-notes
```

…run from a checkout of the tagged commit. `.github/release.yml` is read regardless of how the release is created.

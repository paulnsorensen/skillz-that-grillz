---
name: release
description: >
  End-to-end release cutting for a GitHub repo: decide the next semantic
  version from the Conventional Commits since the last tag, draft proper
  release notes (GitHub auto-generated via `.github/release.yml`, or
  hand-curated grouped by change type with highlights and upgrade notes
  for breaking changes), create and push an annotated tag, publish the
  GitHub release, and optionally update `CHANGELOG.md`. Use when the user
  says "cut a release", "tag a release", "publish a release", "ship
  vX.Y.Z", "bump the version", "what version should this be", "write the
  release notes", "draft the changelog", or invokes /release. Distinct
  from /gh-bootstrap (which scaffolds the `.github/release.yml` + release
  workflow plumbing once) and /gh (raw per-command `gh release` ops):
  this skill runs the actual release ceremony — version decision, notes,
  tag, publish. Run after the work is merged to the default branch.
allowed-tools: Read, Write, Edit, Glob, Bash(gh:*), Bash(git:*)
license: MIT
---

# release

Cuts a versioned release: pick the next [semantic version](https://semver.org), write proper release notes, tag the commit, and publish the GitHub release.

This sits next to `/gh-bootstrap` and `/gh` and answers a different question. `/gh-bootstrap` wires the release *plumbing* once (`.github/release.yml`, the tag-driven workflow). `/gh` is the raw `gh release` command reference. This skill is the **ceremony you run each time you ship**: decide the bump, draft the notes, tag, publish.

## Where this skill fits

| Concern | Skill |
|---|---|
| One-time release-notes config + tag-driven workflow | `/gh-bootstrap` |
| Raw per-command `gh release create` / `view` / `upload` | `/gh` |
| Deciding the version, drafting notes, tagging, publishing | **`/release`** (this skill) |
| Local commit before any of this | `/commit` |

If the repo has a tag-driven release workflow (from `/gh-bootstrap`), pushing the tag is enough — the workflow publishes the release. This skill still decides the version and (optionally) drafts curated notes; it just stops after the tag push and reports that the workflow takes over. Otherwise it publishes the release itself with `gh release create`.

## Protocol

### 1. Pre-flight

Refuse to release from a dirty or diverged state — a release tag is permanent and public.

```bash
git fetch --tags origin
BRANCH=$(git branch --show-current)
DEFAULT=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
git diff-index --quiet HEAD || { echo "uncommitted changes — commit or stash first"; exit 1; }
[ "$BRANCH" = "$DEFAULT" ] || echo "WARNING: not on $DEFAULT (on $BRANCH) — releases normally tag the default branch"
read -r AHEAD BEHIND < <(git rev-list --left-right --count "origin/$DEFAULT...HEAD")
[ "$BEHIND" = 0 ] || { echo "HEAD is $BEHIND commits behind origin/$DEFAULT — pull/rebase before tagging"; exit 1; }
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
```

Confirm with the user:

- The commit to tag (default: tip of the default branch).
- The last release tag (`$LAST_TAG`) — the baseline for the version bump and the notes range. If the repo has no tags yet, this is the first release.

Stop and tell the user — don't tag — if the tree is dirty, or `HEAD` is behind `origin/$DEFAULT`.

### 2. Decide the version

Read `references/semver.md` for the full decision rules. The short version: scan the Conventional Commits since the last tag and map the highest-impact change to a bump.

```bash
git log "${LAST_TAG:+$LAST_TAG..}HEAD" --no-merges --pretty='%s'
```

| Highest-impact change since last tag | Bump (≥ 1.0.0) | Bump (0.x pre-1.0) |
|---|---|---|
| `feat!:` / `fix!:` / `BREAKING CHANGE:` footer | major | minor |
| `feat:` | minor | patch |
| `fix:` / `perf:` | patch | patch |
| only `docs` / `chore` / `refactor` / `test` / `ci` | patch (or skip — ask) | patch |

**Match the repo's existing tag scheme** — if `$LAST_TAG` is `v1.2.3`, the next is `v1.3.0`, not `1.3.0`. Detect and reuse the `v` prefix (or its absence). Detect prerelease identifiers (`-rc.1`, `-beta.2`) and keep the user's convention.

Propose the computed version and the one-line reason ("3 feats, no breaking → minor → v1.3.0"). Let the user confirm or override. Never tag a version the user hasn't seen.

### 3. Draft the release notes

Read `references/release-notes.md` for the three strategies and how to choose. Summary:

- **Auto-generated** (`gh release create --generate-notes`) — best when the repo merges via PRs and has `.github/release.yml`. GitHub groups merged PRs into the configured category buckets by label. Cheapest, and complete as long as every change landed as a labelled PR.
- **Hand-curated** — best when there's no `.github/release.yml`, when commits (not PRs) are the unit of change, or when the release needs editorial highlights. Group by Conventional Commit type; lead with a **Highlights** section and, for any breaking change, an **Upgrade notes** section with the concrete migration step. `assets/release-notes-template.md` is the starting structure.
- **Hybrid** — generate, then hand-edit the top to add highlights and upgrade notes. The most common choice for a release that matters.

When curating, derive the entries from the commit log, not from imagination — every bullet must trace to a real commit. Link PRs/commits where the audience would want the detail.

### 4. Update CHANGELOG.md (if the repo keeps one)

If `CHANGELOG.md` exists, keep it in sync. Most repos follow [Keep a Changelog](https://keepachangelog.com): move the `## [Unreleased]` entries under a new `## [VERSION] - YYYY-MM-DD` heading, leave a fresh empty `Unreleased`, and update the comparison links at the bottom. If there's no changelog and the user doesn't want one, skip this — don't introduce a changelog they didn't ask for.

### 5. Tag and push

Annotated tag (carries a message, author, and date — lightweight tags don't, and `git describe` treats them differently). Tag the agreed commit, then push only the tag.

```bash
VERSION=v1.3.0
git tag -a "$VERSION" -m "$VERSION"        # add -s to GPG-sign if the repo signs tags
git push origin "$VERSION"
```

If a tag-driven release workflow exists (`.github/workflows/release.yml`), **stop here** — the workflow publishes the release on tag push. Report the workflow run URL and skip step 6.

### 6. Publish the GitHub release

Only when there's no tag-driven workflow doing it for you.

```bash
# Auto-generated notes:
gh release create "$VERSION" --title "$VERSION" --generate-notes --latest

# Curated notes from a file:
gh release create "$VERSION" --title "$VERSION" --notes-file NOTES.md --latest

# Prerelease (rc/beta/alpha) — do NOT mark --latest:
gh release create "$VERSION" --title "$VERSION" --notes-file NOTES.md --prerelease

# Draft for review before going public:
gh release create "$VERSION" --title "$VERSION" --notes-file NOTES.md --draft
```

Attach build artifacts by listing them after the tag (`gh release create "$VERSION" ... dist/*.tar.gz`) when the project ships binaries.

### 7. Verify and report

Read the published state back — don't claim success from the create command's exit code alone.

```bash
gh release view "$VERSION" --json tagName,isLatest,isDraft,isPrerelease,url --jq .
git ls-remote --tags origin "$VERSION"
```

Report to the user: the version and why, the notes strategy used, the release URL, and whether it's draft/prerelease/latest. Call out anything skipped (e.g. "workflow is publishing — release not yet visible") and why.

## What this skill is NOT for

- Scaffolding `.github/release.yml` or the release workflow — that's `/gh-bootstrap` (run once, first).
- Looking up raw `gh release` flags outside a release ceremony — that's `/gh`.
- Committing or pushing branch code — that's `/commit` + `/gh`.
- Publishing to a package registry (npm / crates.io / PyPI). This skill tags and creates the GitHub release; registry publishing is the project's own `just`/CI step.
- Per-package versioning in a monorepo with independent release lines — out of scope; this skill cuts one tag for one version line. Say so if the repo is a multi-package monorepo.

## Gotchas

- **Never re-point an existing tag.** `git tag -f` + `git push -f` to move a published tag breaks every consumer who already fetched it. If you tagged the wrong commit, cut a new patch version instead.
- **Auto-generated notes only see merged PRs.** Direct pushes to the default branch don't appear. A repo without PR-based merges (no branch protection) should curate notes, not rely on `--generate-notes`.
- **`--generate-notes` needs `.github/release.yml`** to group nicely; without it you get a flat "What's Changed" list. If the grouping looks wrong, the categories/labels in `release.yml` are the lever — fix them via `/gh-bootstrap`, not here.
- **Prereleases must not be `--latest`.** Marking an `-rc` build as latest makes it the default download and the target of `releases/latest`. Use `--prerelease` and omit `--latest`.
- **Pre-1.0 semver is different.** Under `0.x`, breaking changes bump the minor, not the major. See `references/semver.md`.
- **Tag must be an ancestor of the default branch.** Tagging a feature-branch commit ships unreviewed code. Confirm the commit is on (or merged into) the default branch before tagging.
- **Signed tags** — if the repo signs releases (`git config tag.gpgSign true` or a project convention), use `git tag -s`; an unsigned tag on a signed-release repo fails verification downstream.

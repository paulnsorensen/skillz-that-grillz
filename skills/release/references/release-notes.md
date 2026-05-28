# Writing the release notes

Release notes answer one question for the reader: **should I upgrade, and what
will change if I do?** Everything else is supporting detail. Lead with the
answer.

## Three strategies

### 1. Auto-generated (`--generate-notes`)

GitHub builds the body from the merged PRs between the previous tag and the new
one, grouped into the buckets defined in `.github/release.yml` (scaffolded by
`/gh-bootstrap`).

```bash
gh release create "$VERSION" --title "$VERSION" --generate-notes --latest
```

Best when:

- The repo merges work through PRs (so each change is a PR with a title + labels).
- `.github/release.yml` exists and its categories/labels are maintained.

Limitations:

- **Only merged PRs appear** — direct pushes to the default branch are invisible.
- Quality is the PR titles' quality. Sloppy PR titles → sloppy notes.
- No editorial highlights — it's a flat categorized list.

To preview without publishing, create a `--draft` and read it back, or use the
API:

```bash
gh api -X POST "repos/{owner}/{repo}/releases/generate-notes" \
  -f tag_name="$VERSION" -f previous_tag_name="$LAST_TAG" --jq .body
```

### 2. Hand-curated

Write the notes from the commit log. Best when there's no `.github/release.yml`,
when commits (not PRs) are the unit of change, or when the release deserves
real editorial framing.

Derive every entry from a real commit — never invent a bullet:

```bash
git log "${LAST_TAG:+$LAST_TAG..}HEAD" --no-merges --pretty='%s (%h)'
```

Group by Conventional Commit type, map to reader-facing headings:

| Commit type | Heading |
|---|---|
| `feat` | Features / Added |
| `fix` | Fixes / Fixed |
| `perf` | Performance |
| `refactor` + user-visible | Changed |
| `BREAKING CHANGE` / `!` | ⚠️ Breaking changes |
| `docs` | Documentation (often folded into "Other") |

Start from `assets/release-notes-template.md`.

### 3. Hybrid (most common for releases that matter)

Generate the categorized list, then hand-edit the top: add a **Highlights**
section (the 1–3 changes most people care about, in plain language) and, if
anything broke, an **Upgrade notes** section with the concrete migration step.
Let GitHub keep the exhaustive PR list below.

## Writing the highlights

- Plain language, user's point of view: "Config now hot-reloads on change" —
  not "Refactored ConfigWatcher to use notify::RecommendedWatcher".
- 1–3 bullets. If everything is a highlight, nothing is.
- Link the PR or commit for readers who want the detail.

## Writing upgrade notes (for any breaking change)

Every breaking change needs a "what you must do" line, not just a "what
changed" line:

> **Breaking:** `--config` now requires an absolute path.
> **Upgrade:** replace `--config app.toml` with `--config "$PWD/app.toml"`.

A breaking change with no migration instruction is the single biggest release-
notes failure — it turns every consumer's upgrade into a debugging session.

## CHANGELOG.md (Keep a Changelog)

If the repo keeps a `CHANGELOG.md`, it usually follows
[Keep a Changelog](https://keepachangelog.com): an `## [Unreleased]` section at
the top accumulating entries under `Added` / `Changed` / `Deprecated` /
`Removed` / `Fixed` / `Security`. On release:

1. Rename `## [Unreleased]` to `## [VERSION] - YYYY-MM-DD` (today's date).
2. Add a fresh empty `## [Unreleased]` above it.
3. Update the comparison links at the bottom:
   `[Unreleased]: .../compare/VERSION...HEAD` and
   `[VERSION]: .../compare/PREV...VERSION`.

The GitHub release body and the CHANGELOG entry can share the same text — pass
the CHANGELOG section to `gh release create --notes-file`. Don't introduce a
CHANGELOG into a repo that doesn't have one unless the user asks.

## Prereleases

Notes for an `-rc` / `-beta` build should say what's being tested and that it's
not the stable release. Publish with `--prerelease` and **without** `--latest`
so it doesn't become the default download or the `releases/latest` target.

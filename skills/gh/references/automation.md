# `gh` automation patterns

> **When to read:** When the user asks for a multi-step flow — tagging
> and uploading a release, watching CI then merging, bulk PR or issue
> triage, daily activity reports, or triggering a workflow and waiting
> on it. Skip for single-command operations covered by SKILL.md.

End-to-end recipes that compose multiple `gh` commands. All patterns use
only `gh` and standard POSIX shell — no extra binaries, no MCPs.

## Wait for CI, merge on green

```bash
PR=123
gh pr checks "$PR" --watch &&                    # block until CI finishes
  gh pr merge "$PR" --squash --delete-branch
```

Or queue auto-merge so the PR merges itself once required checks pass:

```bash
gh pr merge 123 --auto --squash --delete-branch
```

## Trigger a workflow and watch it

`gh workflow run` returns the run URL immediately (gh ≥ v2.87):

```bash
RUN_URL=$(gh workflow run ci.yml --ref main 2>&1 | grep -oE 'https://[^ ]+')
RUN_ID=$(printf '%s\n' "$RUN_URL" | grep -oE '[0-9]+$')
gh run watch "$RUN_ID" --exit-status            # exits non-zero on failure
```

## Re-run failed jobs in the latest CI run

```bash
RUN_ID=$(gh run list --workflow=ci.yml --branch="$(git branch --show-current)" \
  --limit 1 --json databaseId --jq '.[0].databaseId')
gh run rerun "$RUN_ID" --failed
```

## Release flow

```bash
VERSION=v1.2.0

# Pre-flight: clean tree, on main, tag is fresh
git diff-index --quiet HEAD || { echo "uncommitted changes"; exit 1; }
[ "$(git branch --show-current)" = "main" ] || { echo "not on main"; exit 1; }
git rev-parse "$VERSION" >/dev/null 2>&1 && { echo "tag exists"; exit 1; }

# Tag and push
git tag -a "$VERSION" -m "Release $VERSION"
git push origin "$VERSION"

# Generate notes from PRs since previous tag, attach build artifacts
gh release create "$VERSION" \
  --title "$VERSION" \
  --generate-notes \
  dist/*.tar.gz
```

## Bulk PR operations

```bash
# Approve every dependabot PR
gh pr list --author app/dependabot --json number --jq '.[].number' \
  | xargs -I {} gh pr review {} --approve --body "Auto-approved dep update"

# Add a label to every PR in a search query
gh pr list --search "is:open author:@me" --json number --jq '.[].number' \
  | xargs -I {} gh pr edit {} --add-label needs-review
```

## Issue triage

```bash
# Apply a label to every unlabeled bug-shaped issue
gh issue list --label="" --search "bug OR error OR crash" \
  --json number --jq '.[].number' \
  | xargs -I {} gh issue edit {} --add-label bug

# Close issues older than 90 days with no activity
gh issue list --json number,updatedAt \
  --jq '.[] | select((now - (.updatedAt|fromdateiso8601)) > (90*86400)) | .number' \
  | xargs -I {} gh issue close {} --reason "not planned"
```

## Daily activity report

```bash
DATE=$(date +%Y-%m-%d)

printf '## GitHub activity %s\n\n' "$DATE"
printf '### PRs I opened today\n'
gh pr list --author @me --search "created:$DATE"
printf '\n### PRs I reviewed today\n'
gh search prs "reviewed-by:@me created:$DATE"
printf '\n### Issues I closed today\n'
gh issue list --author @me --state closed --search "closed:$DATE"
```

## Patterns to avoid

- **Piping `gh` into a separate `jq` binary or other JSON transforms**:
  inline with `--jq` or `--template` instead. Piping `gh ... --jq` output
  into `xargs`/`sed`/`awk` for downstream bulk operations is fine — the
  rule is about JSON extraction, not all pipes.
- **`--body "$(cat <<EOF...)"`**: use `--body-file`.
- **`gh api repos/...` for endpoints with named subcommands**: prefer
  `gh pr ...`, `gh issue ...`, `gh run ...`. Reach for `gh api` only when
  there is genuinely no subcommand wrapper.
- **`gh repo delete`, `gh release delete`, `gh secret delete`,
  `gh ssh-key delete`, `gh codespace delete`**: never automate these.
  They are irreversible and stay outside this skill.

# jq recipes for `gh`

> **When to read:** When the workflow needs to filter or transform `gh`
> output — extract specific fields, count results, group by attribute,
> drive a downstream `xargs` invocation. Skip for simple one-off
> commands that already inline `--jq`.

`gh` ships with `--jq` and `--template` flags built in. **Always inline**
filters with `--jq` instead of piping to a separate `jq` binary — pipes
trigger Claude Code's compound-command sandbox and double the round-trips.

```bash
# wrong
gh pr list --json number,title | jq '.[].title'

# right
gh pr list --json number,title --jq '.[].title'
```

## Pull-request filters

```bash
# Just the open PR numbers
gh pr list --json number,state --jq '.[] | select(.state=="OPEN") | .number'

# PR titles by a specific author
gh pr list --json number,title,author \
  --jq '.[] | select(.author.login=="octocat") | "\(.number): \(.title)"'

# Count of open PRs
gh pr list --json state --jq 'length'

# PRs blocking on you for review
gh pr list --search "review-requested:@me" --json number,title --jq '.[]'

# Find PRs with failing checks
gh pr list --json number,title,statusCheckRollup \
  --jq '.[] | select(.statusCheckRollup.state=="FAILURE") | "\(.number): \(.title)"'

# Stale PRs (no update in 30 days)
gh pr list --json number,title,updatedAt \
  --jq '.[] | select((now - (.updatedAt | fromdateiso8601)) > (30*86400)) | "\(.number): \(.title)"'
```

## Workflow run filters

```bash
# Latest run ID for a workflow
gh run list --workflow=ci.yml --limit 1 --json databaseId --jq '.[0].databaseId'

# Failed jobs in a run
gh run view 789 --json jobs \
  --jq '.jobs[] | select(.conclusion=="failure") | {name, url}'

# Active (in-progress) runs
gh run list --status=in_progress --json databaseId,workflowName,headBranch \
  --jq '.[] | "\(.databaseId)  \(.workflowName)  \(.headBranch)"'

# Average duration (seconds) of last 50 successful CI runs
gh run list --workflow=ci.yml --limit=50 --status=success \
  --json createdAt,updatedAt \
  --jq 'map(((.updatedAt|fromdateiso8601) - (.createdAt|fromdateiso8601))) | add/length'
```

## Issue filters

```bash
# Unlabeled open issues
gh issue list --label="" --json number,title --jq '.[]'

# Issues older than 7 days, no assignee
gh issue list --json number,title,createdAt,assignees \
  --jq '.[] | select(.assignees|length==0) | select((now - (.createdAt|fromdateiso8601)) > 604800) | .number'

# Bug count grouped by label
gh issue list --label bug --json labels \
  --jq 'group_by(.labels) | map({label: .[0].labels[0].name, count: length})'
```

## Repo metadata

```bash
# Owner/repo of current working tree
gh repo view --json nameWithOwner --jq '.nameWithOwner'

# Default branch
gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name'

# Top 5 starred repos for a user
gh repo list octocat --json name,stargazerCount \
  --jq 'sort_by(.stargazerCount) | reverse | .[:5]'
```

## Auth & rate limits

```bash
# Current login
gh api user --jq '.login'

# Rate-limit headroom
gh api rate_limit --jq '.rate | "\(.remaining)/\(.limit), resets at \(.reset)"'

# Repo-level permissions for the authenticated user
gh api repos/owner/repo --jq '.permissions'
```

## Tips

- Run `gh <cmd> --help | sed -n '/JSON FIELDS/,/^$/p'` to discover valid
  field names. They differ from the REST API (`stargazerCount`, not
  `stargazersCount`).
- For complex transforms that need pipes inside jq, just use `|` *inside*
  the `--jq` string — that does not invoke a separate process.
- When a number is needed (e.g. for `xargs`), the `-r` flag is implicit
  with `--jq`; output is already raw.

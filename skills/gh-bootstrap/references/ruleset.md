# Default-branch ruleset

The full ruleset payload, the `gh api` invocation, and the create-vs-update logic for step 3 of the protocol.

## Two canonical variants

Two ruleset shapes ship as assets, picked by repo size:

| Asset | Use when | Highlights |
|---|---|---|
| `assets/rulesets/main-pr-ci.json` | Solo or small repo, no merge queue | Squash-only via `allowed_merge_methods`, 0 reviews, single CI check |
| (inline below) | Team repo with a real merge queue | `merge_queue` rule, ≥1 review, multi-check matrix |

The "main: PR + CI" template is the simpler default and matches what the user runs on their own repos. Pick it unless the project has enough PR throughput to justify the queue. Both variants enforce the same fundamentals (no force-push, no deletion, PRs required, CI must pass).

## Why a ruleset (not classic branch protection)

Both surfaces can gate merges into `main`, but rulesets are the modern path:

- Merge queue is a **first-class rule** (`type: merge_queue`) — classic branch protection wedges the queue into nested fields and is being phased out for new repos.
- Rulesets layer with org-level rulesets and bypass actors cleanly. Classic branch protection is a single rule per branch.
- They're queryable: `gh api repos/$REPO/rulesets` returns a list with ids, which makes idempotent updates straightforward.

Use classic branch protection only if the user is on an older GHE Server release that doesn't have rulesets, or if the org is already standardized on classic protection.

## Payload shape

The full request body for the default ruleset created by this skill. Substitute the user's choices for `required_approving_review_count` and the entries under `required_status_checks.parameters.required_status_checks`.

```json
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["~DEFAULT_BRANCH"],
      "exclude": []
    }
  },
  "bypass_actors": [],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [
          { "context": "ci" }
        ]
      }
    },
    {
      "type": "merge_queue",
      "parameters": {
        "check_response_timeout_minutes": 60,
        "grouping_strategy": "ALLGREEN",
        "max_entries_to_build": 5,
        "max_entries_to_merge": 5,
        "min_entries_to_merge": 1,
        "min_entries_to_merge_wait_minutes": 5,
        "merge_method": "SQUASH"
      }
    }
  ]
}
```

What each rule buys:

| Rule | Effect |
|---|---|
| `deletion` | Blocks deletion of the default branch. |
| `non_fast_forward` | Blocks force-push (history rewrites). |
| `pull_request` | Forces PRs (no direct pushes), requires approving reviews, dismisses stale reviews on push, requires resolved threads before merge. |
| `required_status_checks` | Named CI checks must pass before merge enqueues. `strict: false` means a PR doesn't need to be rebased on top of latest `main` — the queue handles that speculatively. |
| `merge_queue` | Enables the queue. `merge_method: SQUASH` matches the repo-level squash-only setting. `ALLGREEN` waits for the whole batch to be green. |

`required_approving_review_count: 0` is valid for solo repos and is the right pick when there's nobody else to review.

## Variant: `main: PR + CI` (solo / no merge queue)

`assets/rulesets/main-pr-ci.json` is the simpler shape — the same fundamentals (no force-push, no deletion, PRs required, named CI check must pass) without the merge queue and with squash hard-clamped at the rule level too.

Key differences from the queue variant above:

| Field | This variant | Queue variant |
|---|---|---|
| `name` | `main: PR + CI` | `main-protection` |
| `pull_request.allowed_merge_methods` | `["squash"]` (belt-and-suspenders) | omitted (repo-level squash-only is enough) |
| `pull_request.required_approving_review_count` | `0` | typically `1` |
| `pull_request.dismiss_stale_reviews_on_push` | `false` | `true` |
| `pull_request.required_review_thread_resolution` | `false` | `true` |
| `merge_queue` rule | not present | present |
| `required_status_checks` | one `{ "context": "check" }` | matrix of named checks |

Why these defaults make sense for a solo repo:

- `required_approving_review_count: 0` — there's no second pair of eyes to wait on. The CI check is the gate.
- `dismiss_stale_reviews_on_push: false` — irrelevant when there are no reviewers to dismiss.
- `required_review_thread_resolution: false` — same.
- `allowed_merge_methods: ["squash"]` — the repo-level `allow_squash_merge=true` + `allow_merge_commit=false` + `allow_rebase_merge=false` already enforces this. The rule-level entry is redundant but explicit, which means a future contributor (or a future you) can read the ruleset and immediately see the merge contract without having to cross-check repo settings.
- No `merge_queue` rule — the queue costs latency and only pays off when concurrent PRs are common.

To apply:

```bash
REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')
TEMPLATE="$(skills/gh-bootstrap/assets/rulesets/main-pr-ci.json)"

# Substitute the user's CI check name into the template before POSTing.
jq --arg ctx "$YOUR_CHECK_NAME" \
   '(.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[0].context) = $ctx' \
   "$TEMPLATE" > "$TMPDIR/ruleset.json"

gh api -X POST "repos/$REPO/rulesets" --input "$TMPDIR/ruleset.json"
```

Or, if updating an existing ruleset by name:

```bash
EXISTING_ID=$(gh api "repos/$REPO/rulesets" \
  --jq '.[] | select(.name == "main: PR + CI") | .id')
gh api -X PUT "repos/$REPO/rulesets/$EXISTING_ID" --input "$TMPDIR/ruleset.json"
```

Required-status-checks can be expanded — duplicate the `{ "context": "..." }` object once per CI job that should gate merges. The template ships with a single placeholder named `check`; replace it with the real check names from `gh api repos/$REPO/commits/<sha>/check-runs --jq '.check_runs[].name'`.

## Apply it

```bash
# Read the assembled payload from a temp file to avoid shell-quoting hell.
gh api -X POST "repos/$REPO/rulesets" \
  --input "$TMPDIR/ruleset.json"
```

To make the file: render the JSON above with the user's chosen values into `$TMPDIR/ruleset.json` (the skill should construct this with `jq` or by writing it via the `Write` tool).

## Idempotent update

Re-running the skill on a repo that already has the ruleset must not create a duplicate.

```bash
# Find an existing ruleset by the agreed name.
EXISTING_ID=$(gh api "repos/$REPO/rulesets" \
  --jq '.[] | select(.name == "main-protection") | .id')

if [ -n "$EXISTING_ID" ]; then
  # Update in place.
  gh api -X PUT "repos/$REPO/rulesets/$EXISTING_ID" \
    --input "$TMPDIR/ruleset.json"
else
  # Create new.
  gh api -X POST "repos/$REPO/rulesets" \
    --input "$TMPDIR/ruleset.json"
fi
```

`PUT` with the same body is a no-op response from the API, so re-applying an unchanged ruleset is free.

## Common adjustments

- **Solo repo, no reviews:** drop `pull_request.required_approving_review_count` to `0`. The queue + status checks still gate.
- **Stricter:** add `{"type": "required_linear_history"}`. Squash merging always produces linear history, so this is redundant in practice but harmless and surfaces accidental future config drift.
- **Code owners:** set `pull_request.require_code_owner_review: true` once a `CODEOWNERS` file exists.
- **Strict status checks:** set `required_status_checks.parameters.strict_required_status_checks_policy: true` if you want PRs to be up-to-date with `main` before the queue accepts them. Usually unneeded — the queue rebuilds speculatively.
- **Bypass actors:** add to `bypass_actors` for break-glass admin merges. Leave empty until there's a concrete reason.

## Classic branch protection fallback

If the user explicitly asks for classic protection (e.g. older GHE), the equivalent endpoint is:

```bash
gh api -X PUT "repos/$REPO/branches/main/protection" \
  --input "$TMPDIR/protection.json"
```

with a body like:

```json
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["ci"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_last_push_approval": false
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": true,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": false
}
```

Classic branch protection's merge-queue toggle lives at:

```bash
gh api -X POST "repos/$REPO/branches/main/protection/required_pull_request_reviews/merge-queue" 2>/dev/null || true
```

…which is fragile and undocumented. Prefer rulesets unless the user has a concrete reason not to.

## Status check name lookup

The hardest part of this whole flow is matching status check names to what CI actually emits. After a PR has had a green CI run:

```bash
SHA=$(gh pr view <number> --json headRefOid --jq '.headRefOid')
gh api "repos/$REPO/commits/$SHA/check-runs" --jq '.check_runs[].name'
```

Whatever that prints is what goes in `required_status_checks.parameters.required_status_checks[].context`. Names from the GitHub Actions UI (e.g. "validate / validate skills") often differ from the workflow `name:` field — always copy from the API output, not the YAML.

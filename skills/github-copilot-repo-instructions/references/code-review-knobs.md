# Copilot code review — full knob inventory

Read when the user asks "what can we configure about Copilot reviews?" or
similar. The repo-wide and path-specific instruction files are the *content*
Copilot reads; this file covers the *controls* around when and how it runs.

## Per-repo controls

### Custom-instructions toggle

Settings → **Code & automation** → **Copilot** → **Code review** → toggle
**"Use custom instructions when reviewing pull requests"**. Default on.
UI-only — not exposed in the REST or GraphQL API.

### Auto-review on PR open (branch ruleset)

Rule type: `copilot_code_review`. Parameters (the *complete* set — the API
strictly validates and rejects unknown keys):

| Parameter | Type | Default | Effect |
|---|---|---|---|
| `review_on_push` | bool | `false` | Re-review on every new commit. Multiplies premium-request cost. Useful for agentic loops. |
| `review_draft_pull_requests` | bool | `false` | Review drafts too. Most teams skip these to keep cost down. |

That's it. No severity threshold, no path filter, no "request changes" mode.

Create via `gh api`:

```bash
gh api repos/<owner>/<repo>/rulesets -X POST --input - <<'JSON'
{
  "name": "copilot-auto-review",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "copilot_code_review",
      "parameters": { "review_on_push": false, "review_draft_pull_requests": false } }
  ]
}
JSON
```

UI path: Settings → **Rules** → **Rulesets** → **New branch ruleset** →
**Automatically request Copilot code review**.

The `copilot_code_review` rule type is **not** listed in the published
[rulesets schema](https://docs.github.com/rest/repos/rules#create-a-repository-ruleset)
as of writing, but the endpoint accepts it. Discoverable by probing.

### Per-PR manual reviewer

```bash
gh pr edit <PR#> --add-reviewer @copilot       # added to gh in Mar 2026
gh pr create --reviewer @copilot ...           # at PR creation
```

The bot account is `copilot-pull-request-reviewer` (Organization-typed user,
id 213165537). The UI exposes it in the Reviewers menu as "Copilot".

## Org-level controls

| Knob | Where | Effect |
|---|---|---|
| Enable Copilot code review | Org policies → Copilot | Feature on/off for all repos in the org. |
| Allow Copilot review for users without a Copilot license | Org policies → Copilot | Lets non-licensed members get Copilot reviews. Two policies must both be on. |
| Org-level rulesets | Org Settings → Rulesets | The same `copilot_code_review` rule, applied across repos via repo-name patterns. |

## What Copilot review *cannot* do

Document these explicitly — users frequently ask for them and they don't exist.

- **No "request changes" / merge block.** Copilot reviews always post at
  COMMENT level, never REQUEST_CHANGES. There is no toggle. To require
  Copilot before merge you would need a separate CI check or branch
  protection — and there isn't an official integration for that today.
- **No severity threshold.** Copilot flags what it flags.
- **No path filter for the review itself.** Excluded file types (binaries,
  some generated files) are a fixed list. `excludeAgent: "code-review"` on
  a path-specific instructions file shapes *guidance content*, not which
  files get reviewed.
- **No API to re-request a review** after Copilot has already reviewed.
  Only the UI "re-request" button triggers a fresh pass. Open feature
  request: github/community discussion **#186152**.
- **No auto-approve.** Copilot never approves a PR even when it has no
  comments.
- **No premium-request quota toggle.** Each review consumes the PR author's
  quota; `review_on_push: true` multiplies cost. Quota is view-only.

## Excluded files (hardcoded)

Per GitHub docs, Copilot code review skips:

- Binary files
- Generated files (detected by GitHub's linguist)
- Files above an undocumented size threshold
- Some specific file types (see the [Copilot code review docs](https://docs.github.com/en/copilot/concepts/agents/code-review) for the current list)

This list is not configurable.

## Verification

After flipping any knob:

1. Open a draft PR or a real PR depending on what you toggled.
2. Check the PR's "Reviewers" sidebar for Copilot.
3. Wait for the review event in the PR timeline.
4. If `review_on_push` is on, push an empty commit and confirm a second
   review fires.

## Source

- [Configuring automatic code review by GitHub Copilot](https://docs.github.com/en/copilot/how-tos/copilot-on-github/set-up-copilot/configure-automatic-review)
- [About GitHub Copilot code review](https://docs.github.com/en/copilot/concepts/agents/code-review)
- [Request Copilot code review from GitHub CLI (changelog, Mar 2026)](https://github.blog/changelog/2026-03-11-request-copilot-code-review-from-github-cli/)
- API rule-type discovery: probed against `repos/{owner}/{repo}/rulesets` POST endpoint, May 2026.

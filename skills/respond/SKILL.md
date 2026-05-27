---
allowed-tools: Read, Write, Edit, Grep, Glob, Bash(gh:*), Bash(git:*), Bash(./scripts/post-reply.sh:*)
license: MIT
name: respond
description: >
  Triage PR review comments by confidence score and act — fix the high-scoring
  ones, push back on the low-scoring ones, and ask about the borderline.
  Handles both inline review threads (anchored to diff lines) and PR-level
  review body comments (summaries submitted with reviews, like Age tables or
  Copilot overviews). First checks the PR's build and merge state and fixes
  those before processing comments. Use when the user says "respond to PR
  comments", "handle review feedback", "address PR reviews", "fix the build",
  "fix CI", "fix merge conflicts", or invokes /respond with a PR number. Also
  trigger when the user mentions a specific PR and wants to deal with reviewer
  suggestions — Copilot, human, or bot. Every reply this skill posts ends
  with an "agent on behalf of;" attribution line so reviewers can tell the
  comment came from an agent on behalf of the human, not the human directly.
  Do NOT use to generate a new review — that is /copilot-review's job.
---

# respond

Read review comments on a PR, score each one 0–100, and act based on the
score: fix the obvious wins immediately, push back on the bad calls, ask
about the borderline ones while the fixes are already underway. Every reply
posted by this skill carries an `agent on behalf of;` attribution line.

## Phase 0: PR health check

Before triaging comments, check the PR's build and merge status with the
`gh` CLI:

```bash
gh pr checks <pr-number>            # check run conclusions
gh pr view <pr-number> --json mergeable,mergeStateStatus
```

**Build failures.** If any check has `state: FAILURE`, fetch the failing job
log (`gh run view <run-id> --log-failed`) and fix the root cause before
processing review comments. Build fixes go first — review comments may be
moot if the build is broken.

**Merge conflicts.** If `mergeable` is `CONFLICTING` or `mergeStateStatus`
is `DIRTY`, rebase onto `origin/<base>` and force-push with lease before
processing comments. Stale conflicts block everything downstream.

Include build/merge status at the top of the triage table:

```text
## PR #N status
- Build: passing | failing (N jobs)
- Merge: clean | conflicts (rebase needed)
```

If both are clean, proceed to phase 1. If fixes were needed, note what was
done.

## Phase 1: Fetch threads and review bodies

Determine `<owner>/<repo>` from the current git remote, then fetch both the
inline review threads and the PR-level review bodies:

```bash
# Inline threads — comments anchored to specific diff lines
gh api repos/<owner>/<repo>/pulls/<pr>/comments

# PR-level review bodies — summaries submitted with a review
gh api repos/<owner>/<repo>/pulls/<pr>/reviews

# PR metadata + diff for context
gh pr view <pr> --json number,title,baseRefName,headRefName
gh pr diff <pr>
```

**Inline threads.** Filter to unresolved threads only. Skip outdated threads
unless the user asks to include them. Group comments by thread — the first
comment is the suggestion; the rest is conversation.

**Review bodies.** Filter reviews to those with a non-empty `body`. Empty
bodies are containers for inline comments and can be ignored here.

**Deduplication.** A review with both a body and inline comments shows up
in both fetches. Score the inline comments as threads (they have file
context). Only score the review body for suggestions not already covered by
its inline comments — link inline comments to their parent review via
`pull_request_review_id`.

## Phase 2: Score each suggestion

For each unresolved thread or review body item, read the suggestion, any
follow-up discussion, and the relevant diff. Then assign a 0–100 confidence
score representing how confident **you** are that the suggestion is correct
and valuable.

Parse review bodies into individual items where possible: bullet lists,
numbered lists, and table rows often contain multiple distinct suggestions
that each deserve their own score and triage row. A cohesive comment
("LGTM", general observation) stays as one item.

`CHANGES_REQUESTED` carries more weight than `COMMENTED` — the reviewer
flagged something as blocking. Factor this into scoring; it raises
confidence that the suggestion matters, not that the suggestion is correct.

The full 4-step calibration rubric (suggestion type, evidence grounding,
context modifiers, borderline re-assessment) lives in
[references/scoring.md](references/scoring.md). Read it once, score every
item against it.

## Phase 3: Triage table + immediate execution

Present the full triage table so the user sees everything at once:

```text
## PR #N review triage

| # | Score | Type          | Reviewer | Location      | Summary                       | Action    |
|---|-------|---------------|----------|---------------|-------------------------------|-----------|
| 1 | 92    | BUG           | copilot  | auth.ts:42    | Missing null check on token   | FIX       |
| 2 | 78    | VALID_CONCERN | alice    | (review body) | Missing error handling in 3   | FIX       |
| 3 | 60    | STYLE         | copilot  | utils.ts:15   | Extract to shared helper      | ASK       |
| 4 | 35    | SCOPE_CREEP   | bob      | index.ts:3    | Add backward compat shim      | PUSH BACK |
| 5 | 20    | STYLE         | copilot  | (review body) | "consider adding tests"       | SKIP      |

### Legend
- FIX (>= 50): agree and implement — proceeding now
- ASK (30–49): needs your call — what do you want to do?
- PUSH BACK (< 30): draft reply below — edit or approve
```

For each row, include a one-line expansion so the user can decide on ASK
items without re-opening the PR. See
[references/triage-examples.md](references/triage-examples.md) for the
expansion shape.

**Then immediately — in the same turn:**

1. Start fixing all FIX items (>= 50) while the user reviews ASK and
   PUSH BACK items.
2. Post PUSH BACK replies for items scored < 30 (the user can override
   before you get to them; don't wait).
3. Ask the user about ASK items (30–49). Include enough context for a quick
   decision.

The triage table is informational, not a gate for high-confidence items.
The user sees what's happening and can interrupt ("stop, don't fix #2"),
but the default is to move fast on the obvious wins.

## Phase 4: Execute

Every reply posted in this phase goes through `scripts/post-reply.sh`, which
wraps the `gh api` calls and appends the attribution suffix (see
[Attribution rule](#attribution-rule) below). Do not call `gh api` directly
from this skill for posting replies — the attribution must be applied in a
single place.

**FIX items (>= 50).** Read the source, implement the fix, then reply:

```bash
# Inline thread reply
./scripts/post-reply.sh --thread \
  --pr <pr-number> \
  --comment-id <comment-id> \
  --body "Fixed — <brief description of what changed>."

# Review body reply (PR conversation comment)
./scripts/post-reply.sh --issue \
  --pr <pr-number> \
  --body "Re: @<reviewer>'s review — Fixed: <brief description>."
```

**PUSH BACK items (< 30).** Post the reply explaining *why*, not just *no*:

```bash
./scripts/post-reply.sh --thread \
  --pr <pr-number> \
  --comment-id <comment-id> \
  --body "<pushback explanation>"
```

Keep the tone professional and specific. Cite project conventions
(`AGENTS.md` / `CLAUDE.md` / your harness's instructions file, complexity budget, early-development stance) when relevant.

**ASK items (30–49).** Wait for the user. Execute as FIX or PUSH BACK once
they decide. If the user doesn't respond to a specific ASK, leave it
unresolved.

**After all actions.** If code changed, hand off to `/commit` for the
commit and `/gh` for the push. Present a summary: files modified, threads
replied to, threads still pending user decision.

## Attribution rule

Every reply this skill posts to GitHub — inline thread replies, review
body replies, PR conversation comments — must end with the literal
attribution suffix:

```text
---
agent on behalf of; <github-handle>
```

Wording is exact: `agent on behalf of;` (lowercase, semicolon, single space
before the handle). Do not paraphrase. Place the line at the end of the
reply, on its own line, prefixed by a horizontal rule (`---`). The intent
is reviewer transparency: a human teammate reading the reply should see at
a glance that an agent posted it on behalf of a teammate.

The attribution is enforced by `scripts/post-reply.sh`. The literal phrase
`agent on behalf of;` lives there as a single Bash constant
(`ATTRIBUTION_PREFIX`) — never as a magic string scattered through reply
templates. The handle is resolved at post time by the script:

1. `RESPOND_GH_HANDLE` environment variable, if set.
2. `gh api user --jq .login` (the authenticated `gh` user).
3. `git config user.name` as the final fallback.

Do not strip the attribution to fit a body-length budget. The GitHub REST
limit on issue comments and review thread replies is 65,536 characters;
the attribution suffix is ~40 characters and never approaches the cap. If
a reply ever needs to be that long, split it before stripping attribution.

This rule exists so future audits don't accidentally remove the suffix.
Do not edit `post-reply.sh` to omit or shorten the prefix without
updating this section first.

## Rules

- Show the triage table before executing — but don't wait for approval on
  >= 50 items.
- One reply per thread — don't fragment responses across multiple comments.
- Match the reviewer's tone — professional for humans, concise for bots.
- Cite specifics in pushback — reference the project's agent-instructions file (`AGENTS.md` / `CLAUDE.md` / `GEMINI.md`), the
  complexity budget, or the early-development stance when relevant.
- Don't argue style — if the suggestion is purely stylistic and the score
  is < 30, skip it (note as SKIP in the table) rather than posting
  pushback. Stylistic items in 30–49 stay as ASK so the user can decide.
- Never defer to a follow-up — don't reply "will address in a follow-up
  PR" or "good idea, will do separately." If it scores >= 50, fix it now;
  if it scores < 30, push back. The only valid deferral is an ASK item the
  user explicitly skips.
- Batch commits — group fixes into one commit, not one per thread.
- The user can override anything — "don't fix #2" stops it, "actually fix
  #4" runs it. The score is a default, not a mandate.

## What this skill never does

- Generate a new review — use `/copilot-review` for that.
- Refactor code beyond the specific fix a reviewer requested.
- Add tests unless a reviewer explicitly asked for them.
- Open new issues or PRs beyond the one being triaged.
- Change files not referenced in review comments.
- Resolve threads it didn't reply to — let GitHub auto-resolve.
- Post replies without the attribution suffix — see
  [Attribution rule](#attribution-rule).

## Gotchas

- `gh api` rate-limits hit on PRs with 50+ comments — batch reads where
  possible and prefer a single paginated call over many requests.
- Review body parsing can split cohesive comments into fragments — check
  for related threads before scoring fragments independently.
- `is_resolved` is not always reliable across GitHub Apps — verify thread
  state by inspecting the latest comment if in doubt.
- Bot reviewers with `CHANGES_REQUESTED` inflate perceived urgency —
  `references/scoring.md` applies a -10 modifier for this case.
- Deduplication between inline comments and review-body summaries is
  tricky — check `pull_request_review_id` overlap before acting.

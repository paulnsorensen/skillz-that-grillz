# 4-step calibration for review-comment triage

Apply these four steps in order to land a **severity tier** and a
**calibration tag** for each suggestion. Severity reflects how much the
suggestion matters; the calibration tag reflects **your** confidence that
the suggestion is correct and grounded — not the reviewer's confidence.

Vocabulary: `blocker > high > medium > low`, each tagged `<certain>`
(grounded, verifiable against the code) or `<speculative>` (inference, no
concrete code reference).

## Step 1 — Classify suggestion type → default severity

| Type          | Description                                  | Default severity |
| ------------- | -------------------------------------------- | ---------------- |
| SECURITY      | Vulnerability, data exposure, auth bypass    | `high`           |
| BUG           | Correctness issue, logic error, crash        | `high`           |
| VALID_CONCERN | Architectural, performance, maintainability  | `medium`         |
| CONVENTION    | Style, naming, project-standard deviation    | `medium`         |
| STYLE         | Formatting, subjective preference            | `low`            |
| SCOPE_CREEP   | Unrelated improvement, "while you're here"   | `low`            |

**Cap:** STYLE and SCOPE_CREEP are capped at `low` — the context modifiers
in step 3 never lift them above `low`. A subjective or out-of-scope
suggestion never crosses into FIX territory just because the reviewer is a
maintainer or several reviewers happen to agree.

## Step 2 — Evidence grounding sets the calibration tag

| Evidence                                              | Tag             |
| ----------------------------------------------------- | --------------- |
| Reviewer cites specific code with accurate analysis   | `<certain>`     |
| Names a real code construct (verifiable via search)   | `<certain>`     |
| References a project convention or CLAUDE.md rule     | `<certain>`     |
| Generic observation without specific code reference   | `<speculative>` |
| Reviewer misreads the code or cites the wrong line    | drop the item   |

If the reviewer misreads the code, the suggestion isn't actionable as
written — drop it and reply with the misread (don't silently skip).

## Step 3 — Context modifiers

| Signal                                          | Effect                     |
| ----------------------------------------------- | -------------------------- |
| `CHANGES_REQUESTED` review state                | bump to `high` if `medium` |
| Multiple reviewers flagged the same issue       | bump one tier              |
| Bot reviewer making a generic observation       | downgrade to `low`         |
| Suggestion duplicates another thread            | merge — triage once        |
| Pre-existing issue not introduced by this PR    | downgrade one tier         |

`CHANGES_REQUESTED` raises how much the suggestion *matters*, not whether
it's *correct* — it bumps severity, never the calibration tag. Reviewer
authority (maintainer, codeowner) does not change severity on its own:
triage on the code, not the badge.

## Step 4 — Re-assess borderline items

For any item in the ASK zone (medium+ `<speculative>`, or low `<certain>`),
re-read the reviewer's comment and the relevant code independently. Assess
a second time without looking at your first read. Then:

- If the two assessments conflict, the suggestion is ambiguous — keep as ASK.
- If both land at `medium` or above **and** `<certain>`, upgrade to FIX.
- If both land at `low` `<speculative>`, downgrade to PUSH BACK or SKIP.

The point of step 4 is to catch cases where the first read was anchored to
the reviewer's framing rather than the code itself. A second independent
pass forces a fresh read.

## Action thresholds

Action depends on **both** severity and calibration — evidence quality
gates auto-fixing, not severity alone:

| Severity          | Calibration     | Action                                                        |
| ----------------- | --------------- | ------------------------------------------------------------ |
| `medium` or above | `<certain>`     | FIX — implement the change; reply when done.                  |
| `medium` or above | `<speculative>` | ASK — surface to the user; act on their decision.             |
| `low`             | `<certain>`     | ASK — surface to the user; act on their decision.             |
| `low`             | `<speculative>` | PUSH BACK — reply explaining why; or SKIP a purely stylistic nit (note in the table, no reply). |

A `<speculative>` claim is never auto-fixed: an ungrounded bug or security
claim — even one defaulting to `high` — goes to ASK, not FIX, until its
evidence is confirmed by reading the source. Use SKIP sparingly — reserved
for purely stylistic suggestions that don't merit a pushback reply. When in
doubt, post a brief acknowledgment rather than skipping silently.

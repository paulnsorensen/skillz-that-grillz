# 4-step calibration for review-comment confidence

Apply these four steps in order to land a 0–100 confidence score for each
suggestion. The score reflects **your** confidence the suggestion is
correct and valuable, not the reviewer's confidence.

## Step 1 — Classify suggestion type

| Type           | Description                                           | Base | Cap |
| -------------- | ----------------------------------------------------- | ---- | --- |
| BUG            | Correctness issue, logic error, crash                 | 50   | 100 |
| SECURITY       | Vulnerability, data exposure, auth bypass             | 55   | 100 |
| CONVENTION     | Style, naming, project-standard deviation             | 25   | 65  |
| STYLE          | Formatting, subjective preference                     | 15   | 45  |
| SCOPE_CREEP    | Unrelated improvement, "while you're here"            | 20   | 55  |
| VALID_CONCERN  | Architectural, performance, maintainability           | 40   | 90  |

Start at the type's base and apply modifiers from steps 2 and 3, never
exceeding the cap. The cap matters: a STYLE suggestion never crosses into
FIX territory just because the reviewer is a maintainer.

## Step 2 — Evidence grounding

| Evidence                                                  | Modifier  |
| --------------------------------------------------------- | --------- |
| Reviewer cites specific code with accurate analysis       | +20       |
| Suggestion references project convention or agent-instructions file (`AGENTS.md` / `CLAUDE.md`) | +15 |
| Generic observation without specific code reference       | -10       |
| Reviewer misreads the code or cites the wrong line        | hard cap 0 |

If the reviewer misreads the code, the suggestion isn't actionable as
written — score 0 and reply with the misread (don't silently skip).

## Step 3 — Context modifiers

| Signal                                          | Modifier |
| ----------------------------------------------- | -------- |
| `CHANGES_REQUESTED` review state                | +10      |
| Reviewer is a maintainer or codeowner           | +10      |
| Bot reviewer (Copilot, CodeRabbit, etc.)        | -10      |
| Suggestion duplicates another thread            | -15      |
| Pre-existing issue not introduced by this PR    | -15      |

`CHANGES_REQUESTED` raises confidence the suggestion *matters*, not that
it's *correct*. Don't double-count: if you already gave +20 for accurate
analysis, the +10 from `CHANGES_REQUESTED` is additive but capped by the
type cap from step 1.

## Step 4 — Re-assess borderline (35–49)

For items near the FIX threshold, re-read the reviewer's comment and the
relevant code independently. Score a second time without looking at your
first score. Then:

- If the two scores diverge by more than 15 points, the suggestion is
  ambiguous — keep as ASK.
- If both scores land at 50 or higher, upgrade to FIX.
- If both scores land below 30, downgrade to PUSH BACK or SKIP.
- Otherwise, keep at the higher of the two scores.

The point of step 4 is to catch cases where the first read was anchored
to the reviewer's framing rather than the code itself. A second
independent pass forces a fresh read.

## Action thresholds

| Score   | Action     | What it means                                |
| ------- | ---------- | -------------------------------------------- |
| >= 50   | FIX        | Implement the change; reply when done.       |
| 30–49   | ASK        | Surface to the user; act on their decision.  |
| < 30    | PUSH BACK  | Reply explaining why it's not being applied. |
| < 15    | SKIP       | Note in the table; don't post a reply.       |

Use SKIP sparingly — it's reserved for purely stylistic suggestions that
don't merit a pushback reply. When in doubt, post a brief acknowledgment
rather than skipping silently.

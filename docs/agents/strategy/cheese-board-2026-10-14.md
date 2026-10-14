# cheese-board strategy 2026-10-14

The strategy advisor approved this backlog strategy in round 4 of run `wf_771edc85-430` (30 open issues, 4 ideas, HEAD 5acce3d). The done advisor returned `revise` for report bookkeeping only, so the workflow did not record this page. The user recorded it by hand. The next cheese-board run reads this page as kind strategy.

Report: `.cheese/notes/cheese-board-2026-10-14.md` (local, git-ignored). Workflow defects found in this run: paulnsorensen/dotfiles#1305 and #1306.

## User decision

`--val-policy` (#245) and `--merge` (#246) join the D-6 run compatibility fingerprint. Cross-run attempt history (#243) never mixes runs with a different validation policy or merge setting. IN-12 and IN-14 override D-6 and stay in the spec lane. IN-21 answers only how `compare()` reads a stored run that has no `val_policy` or `merge` field. This decision is final.

## Themes

- **TH-1 A search that learns**: Finish the W4 memory chain on one stable history seam, then add the W5 mechanics on top, so the search stops repeating rejected edits.
- **TH-2 Scoring integrity and judge evidence**: Rest every score and rejection on one narrow check contract and on judge evidence that a reader can audit.
- **TH-3 Trustworthy live harnesses**: Make the Codex and Claude task sandboxes predictable and self-diagnosing through one redacted transcript seam.
- **TH-4 Operator-safe run setup**: Make seeds, splits and the pre-run plan safe and previewable, so bad splits stop before any paid model call.
- **TH-5 Traceability and program hygiene**: Keep acceptance-criteria links and tracker state true to HEAD, so every wave gate can be shown met.
- **TH-6 Targets beyond skillz**: Open W7 targets last, after a gate that shows W2 to W6 merged.

## Initiatives

"Planned" is the lane in the approved strategy. "Run result" is what the Spec phase produced.

| Initiative | Items | Planned | Depends on | Overrides | Run result |
| --- | --- | --- | --- | --- | --- |
| IN-1 Record per-proposal attempts and broke/fixed history (W4.4) | #241 | spec | - | - | spec written: `attempt-history`, 6 curds, factory wave 1 |
| IN-2 Narrow the Checker/CheckMemo contract; retryable sandbox timeouts | #312, #287 | mold | - (cook merges after IN-1) | D-4 | mold |
| IN-3 Move the Codex task TMPDIR outside the workspace | #305 | spec | - | - | failed taste twice |
| IN-4 Claude preflight transcript through one shared redaction seam; write control on 2.1.296 | #291, #290 | mold | - | D-11 | mold |
| IN-5 Backfill AC traceability for pragmatic-autoimprove and the journey workspace | #272, #311 | spec | - | - | drafter raised open questions |
| IN-6 Rejected approaches and proposer notes in the reflection prompt (W4.5) | #242 | spec | IN-1 | - | drafter raised open questions; blocked on IN-1 |
| IN-7 Train minimum in splits; bound the seed output | #300, #296 | mold | - | - | mold |
| IN-8 Private cross-run attempt history for compatible runs (W4.6) | #243 | spec | IN-1, IN-6 | - | failed taste twice |
| IN-9 Judge rationale record and task command trace | #301, #303 | mold | IN-2, IN-4 | - | mold |
| IN-10 Steer a stalled search to the least-tested component (W4.7); close W4 | #244, #228 | spec | IN-8 | - | failed taste twice |
| IN-11 Free plan command: lint contract, preview seeds, splits, estimate | #297 | mold | IN-7 | - | mold |
| IN-12 Selectable GEPA validation policy (W5.1) | #245 | spec | IN-10, IN-21 | D-6 | sent to mold by the override rule |
| IN-13 Case can request a git repository and project tools in Codex | #304 | mold | IN-3, IN-9 | - | mold |
| IN-14 Opt-in GEPA merge proposer (W5.2); close W5 | #246, #229 | spec | IN-12, IN-21 | D-6 | sent to mold by the override rule |
| IN-15 Runner-term leakage check and export review flag for prose+cli | #302 | mold | IN-9 | - | mold |
| IN-16 Replay one case with its full transcript | #306 | mold | IN-9, IN-4 | - | mold |
| IN-17 Live smoke evidence and recipe | #268 | defer | IN-3, IN-4 | - | defer |
| IN-18 Target contract; non-skillz and wedged targets (W7) | #231, #251, #252, #253 | defer | IN-14, IN-2, IN-13, IN-19, IN-20 | - | defer |
| IN-19 Refresh the #223 rollup; file the W6 C15 cost_source child | #223 | mold | - | - | mold |
| IN-20 Deliver the W6 C15 cost_source child | - | defer | IN-19 | - | defer |
| IN-21 How compare() reads stored runs without val_policy or merge | - | mold | - | D-6 | mold |

Parked: #211 (nono research backlog; waits on nono#2087, nono#1573 and a terms-of-service answer).

## Ordering rules

- IN-1 merges before the IN-2 cook. Both edit `run/_session.py`. IN-1 owns attempt recording; IN-2 owns the baseline-timeout Stop.
- IN-4 owns the one shared redaction and transcript helper. IN-9 and IN-16 reuse it. Today the only redaction is the `SECRETISH` regex in `wrapper/_gemini.py`.
- IN-2 and IN-9 each change `scoring_digest`, so each starts a new cohort (D-5). IN-9 cooks right after IN-2.
- W7 (IN-18) starts only after W2 to W6 merge (D-3). W6 closes when IN-20 delivers the C15 child.

## Spec review findings to fix on redraft

- **IN-3 (#305)**: State how the TMPDIR is derived, because `configuration()` and `_environment()` receive only the workspace path. `check_candidate` uses the tempdir itself as the workspace, so `workspace.parent` is the shared `/tmp`. Fix the acceptance grep so it also rejects `workspace / 'tmp'` with single quotes (`codex_bridge.py:29`).
- **IN-8 (#243)**: Key the store by the D-6 fingerprint, as the outcome says, or justify a different key. Name each behavior test as `file::function`. Map the spec AC numbers to the gh-243 AC numbers. State the IN-6 order on `search/_history.py`.
- **IN-10 (#244)**: Define the #243 attempts interface that `least_tested()` reads. Define "unknown observation" and the empty cases. Rename the spec acceptance IDs so they do not collide with gh-244 IDs. Add a step that closes #228.

## Decisions this strategy would change

- D-4 (memo stores a timeout as final): IN-2.
- D-6 (fixed fingerprint fields): IN-12, IN-14, IN-21.
- D-11 (Bash allow rule deferred): IN-4.

# PR 691 browser evidence

T3 browser checks exercise the real source CLI on Linux.
All data is synthetic. PNGs omit the private URL fragment.
All 18 PNGs show the final source after the second bounded cure.
The fixture covers the six page sections and their empty, populated, expanded, fault, and budget states.

## Reproduce

1. Start `uv run --locked --project packages/skillz python -m skillz_experiments watch <temporary-out>`.
2. Open the complete private URL from stdout in the T3 browser.
3. Copy `packages/skillz/tests/skillz_experiments/fixtures/watch/run.json` to `<temporary-out>/run.json`.
4. Expand the Cases and Outcomes disclosures.
5. Set desktop 1280×900 and mobile 390×844 viewports.
6. Check light and dark appearance.
7. Rewrite the temporary record for the transitions below.
8. Stop the watcher after the check.

## Screenshot series

| PNG | Observable result |
| --- | --- |
| [01-private-link-required](01-private-link-required.png) | No token: the landing page requests the printed private link. |
| [02-waiting](02-waiting.png) | Missing file: the page waits for run.json. |
| [03-empty-prepared](03-empty-prepared.png) | Prepared empty record: six sections show missing-value and empty-state text. |
| [04-complete-status-roles-leaderboard](04-complete-status-roles-leaderboard.png) | Complete record: status, comparisons, gate verdict, six roles, and candidate leaderboard. |
| [05-expanded-cases](05-expanded-cases.png) | Expanded cases: mixed scores, genuine zero, unmet reasons, and unscored evaluation failure. |
| [06-reflections-outcomes](06-reflections-outcomes.png) | Accepted and rejected reflections, over-budget flag, newest outcomes, and criterion details. |
| [07-dark-desktop](07-dark-desktop.png) | Dark desktop appearance. |
| [08-mobile-status-roles](08-mobile-status-roles.png) | Mobile status and roles stay within the page. |
| [09-mobile-expanded-cases](09-mobile-expanded-cases.png) | Mobile expanded cases retain long literal text. |
| [10-mobile-dark-reflections](10-mobile-dark-reflections.png) | Dark mobile reflections and outcomes retain long text. |
| [11-mobile-role-columns-scrolled](11-mobile-role-columns-scrolled.png) | The role table scrolls locally to its token and latency columns. |
| [12-live-refresh-searched](12-live-refresh-searched.png) | A live rewrite changes phase, calls, and score without restarting. |
| [13-invalid-json](13-invalid-json.png) | Invalid JSON produces a visible read error. |
| [14-invalid-number](14-invalid-number.png) | A nonfinite number produces a structured invalid-number error. |
| [15-malformed-field-recovery](15-malformed-field-recovery.png) | A list-valued case ID becomes an unknown ID without terminating the request. |
| [16-valid-record-recovery](16-valid-record-recovery.png) | A valid rewrite restores the complete view without restarting. |
| [17-offline](17-offline.png) | A stopped server produces a visible connection error. |
| [18-newest-200-limit](18-newest-200-limit.png) | The page lists the newest 200 of 205 outcomes, newest first. |

## Measured checks

- Missing and wrong capabilities each return HTTP 403 with no run data.
- The valid capability returns HTTP 200 with `Cache-Control: no-store`.
- The API excludes `PRIVATE_TRANSCRIPT_NOT_IN_UI`.
- Exact-JSON fallback gives task input tokens 262, not 220.
- An evaluation failure keeps one attempt, zero judged zeros, and a null mean.
- A genuine zero remains zero. Only the all-zero case receives the zero-case style.
- The view contains six roles, including preflight calls and failed-role seconds without usage.
- Unmeasured token fields are null and display `-`, not a measured zero.
- Fixture-derived tests cover explicit-null usage, rejected-candidate usage, and genuine measured zero tokens.
- Accumulated call time is 77 seconds. The separate wall-clock budget is 600 seconds.
- The page contains one progress bar, for calls only.
- Script-like reasons and hypotheses remain text. No script element appears in the case content.
- A live rewrite changes calls from 9 to 10 and the mixed-case mean from 0.500 to 0.375.
- All nine opened disclosures survive that rewrite.
- An unchanged poll preserves the Roles table node.
- At 390 CSS pixels, page scroll width is 390. Table containers are 358 pixels wide and scroll across 600 pixels.
- A 403-character hypothesis includes 360 unbroken characters. Before the fix, page scroll width was 3343 pixels.
- The cutoff variant has 205 judged outcomes across three cases.
- Its visible range is outcome 205 through outcome 6. Its leaderboard still counts all 205 outcomes.

## Transition inputs

Start with no file, then write `{"run_id":"watch-empty","phase":"prepared","outcomes":[]}`.
Next, write the fixture. Change its phase to `searched`, calls to 10, and second outcome score to 0.75.
Remove the first run ID value to make invalid JSON. Restore it and set calls to `1e999`.
Restore calls to 10. Change the failed outcome case ID to `["malformed"]`.
Restore its case ID and the `complete` phase.

For the cutoff variant, create 205 baseline outcomes with candidate hash `f` repeated 64 times.
Use case ID `cutoff-<i % 3>`, repeat `i + 1`, score `i % 2`, and empty criteria for indices 0 through 204.
The final capture rewrites the same real watcher record. It does not substitute an API.

The error screenshots retain the last successful tables under an explicit error header.
The initial Loading label is transient; the browser checks the resulting waiting or data state.
This series does not test macOS or browsers outside the T3 browser.

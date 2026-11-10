# PR 691 browser evidence

T3 browser checks exercise the real source CLI on Linux.
All data is synthetic. PNGs omit the private URL fragment.
Captures 01–18 preserve the earlier functional baseline before the styling review.
Captures 19–26 show the corrected branding and readable tables before the hidden stale notice is added.
Captures 27–29 show the persistent warning in the final stale-data implementation.
Captures 30–31 verify compact assets through the rebuilt archive on desktop and mobile.
The parent visually reviews all 31 PNGs.
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

## Baseline measured checks

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
Capture 18 rewrites the same real watcher record. It does not substitute an API.

The baseline error screenshots retain the last successful tables under an error header that can scroll away.
The initial Loading label is transient; the browser checks the resulting waiting or data state.
This series does not test macOS or browsers outside the T3 browser.

## Screenshot review and corrected styling

The PR 691 screenshot review finds broken table headers, split short identifiers, weak dark-mode contrast, and hidden error warnings.
The earlier page-width check misses these defects.
The corrected layout gives tables readable widths and local horizontal scrolling.
Headers wrap at spaces rather than inside words.
The official favicon and header art load locally with no external resource requests.

| PNG | Observable result |
| --- | --- |
| [19-branded-desktop-light](19-branded-desktop-light.png) | Official icon and header art, warm cards, readable status and table columns. |
| [20-branded-desktop-dark](20-branded-desktop-dark.png) | Dark theme with stronger zero-case contrast. |
| [21-readable-expanded-cases](21-readable-expanded-cases.png) | Expanded case headers stay whole and judge reasons stay readable. |
| [22-branded-mobile-light](22-branded-mobile-light.png) | Branding fits a 390-pixel viewport. |
| [23-mobile-role-columns](23-mobile-role-columns.png) | Local scrolling retains whole roles and candidate identifiers. |
| [24-mobile-readable-criteria](24-mobile-readable-criteria.png) | Criterion headers do not split inside words. |
| [25-mobile-judge-reasons](25-mobile-judge-reasons.png) | Local scrolling exposes complete judge reasons. |
| [26-mobile-dark-reflections](26-mobile-dark-reflections.png) | Dark reflections retain long literal text without page overflow. |
| [27-mobile-stale-data](27-mobile-stale-data.png) | Invalid JSON leaves a persistent stale-data warning above retained tables. |
| [28-desktop-stale-data](28-desktop-stale-data.png) | The desktop warning stays visible with the detailed header offscreen. |
| [29-offline-stale-data](29-offline-stale-data.png) | The warning stays visible after the real watcher stops. |
| [30-compact-assets-desktop](30-compact-assets-desktop.png) | Rebuilt archive: compact official PNGs retain desktop branding and six role rows. |
| [31-compact-assets-mobile-dark](31-compact-assets-mobile-dark.png) | Rebuilt archive: compact branding fits the dark mobile viewport. |

## Final layout and stale-data checks

The final PR 691 browser checks use the real source CLI and temporary run files, not a replacement API.
At 1280×900, page width is 1280 pixels and table widths are 1158 pixels.
At 390×844, page width is 390 pixels and table containers are 336 pixels wide.
Mobile table scroll widths are 760 or 1120 pixels, depending on column count.
Header words, short role names, and 12-character candidate identifiers have no inside-word splits.

The zero-case text contrast rises from 3.44:1 to 8.36:1 in dark mode.
Light-mode zero-case contrast is 6.34:1.
Hover contrast remains 6.83:1 dark and 5.72:1 light.
Both official images load. External resource requests remain zero.

The persistent stale-data warning covers invalid JSON, a missing record, a removed capability, and a stopped server after valid data.
The warning stays at y=8 with a height of 40.5 CSS pixels while the detailed header is offscreen.
Six role rows and nine open disclosures remain.
An initial missing record shows no stale warning because no old tables exist.
Restoring the record and its original timestamp clears the warning without replacing the Roles table.
Restoring the capability also clears the warning.

To reproduce recovery, preserve the temporary record timestamp before writing invalid JSON.
Restore both the valid record and that timestamp.
To reproduce offline data, stop the watcher only after the page loads a valid record and scrolls to Cases.
These checks remain limited to Linux and the T3 browser.

## Compact asset verification

Captures 19–29 use full-size image copies. Captures 30–31 use the approved compact copies with unchanged page code.
The final asset check runs the rebuilt `skillz-experiment.pyz` against the tracked synthetic fixture.
The favicon loads at 128×128 pixels and 25,548 bytes. The header loads at 560×305 pixels and 61,314 bytes.
Both images load in the T3 browser without external resource requests.
Page width equals the viewport: 1280 pixels on desktop and 390 pixels on mobile.
The source HTTP test rejects the old 1254×1254 favicon and passes with the compact image.
The original artwork files remain unchanged. The rebuilt archive measures 18,729,821 bytes, below the unchanged 24 MiB limit.[^1]

[^1]: packages/skillz/tests/skillz_experiments/test_watch.py:169-176,225-232; packages/skillz/src/skillz_experiments/_candidate.py:21; local rebuilt archive measurement, 2026-11-09.

## Minimalist local styling

Captures 32–43 supersede the warm appearance in captures 19–31.
Captures 44–47 supersede their headers: the final header uses the favicon, not the README hero image.
The parent visually reviews all 12 new PNGs.
Bulma 1.0.4 and Inter variable 5.3.0 load from the watcher, not a CDN.[^2]
The real source CLI and rebuilt archive serve the same synthetic fixture.

| PNG | Observable result |
| --- | --- |
| [32-modern-private-link](32-modern-private-link.png) | The private-link landing page uses the new local font and branding. |
| [33-modern-desktop-overview](33-modern-desktop-overview.png) | Light desktop status and right-aligned numeric role columns. |
| [34-modern-light-cases](34-modern-light-cases.png) | Expanded cases, unmet criteria, and long literal judge reasons. |
| [35-modern-dark-cases](35-modern-dark-cases.png) | Dark expanded cases retain readable failure contrast. |
| [36-modern-dark-reflections-outcomes](36-modern-dark-reflections-outcomes.png) | Dark accepted and rejected reflections, budget flag, and expanded outcomes. |
| [37-modern-dark-mobile-overview](37-modern-dark-mobile-overview.png) | Dark mobile branding and status fit the viewport. |
| [38-modern-light-mobile-tables](38-modern-light-mobile-tables.png) | Light mobile roles and leaderboard scroll within their cards. |
| [39-modern-light-mobile-stale](39-modern-light-mobile-stale.png) | The light stale warning remains above retained, horizontally scrolled role columns. |
| [40-modern-dark-mobile-stale-reasons](40-modern-dark-mobile-stale-reasons.png) | The dark stale warning remains above expanded cases and complete judge reasons. |
| [41-modern-light-mobile-recovered](41-modern-light-mobile-recovered.png) | A valid rewrite clears the stale warning and restores the light mobile overview. |
| [42-modern-archive-mobile](42-modern-archive-mobile.png) | The rebuilt archive loads local Inter and branding at mobile width. |
| [43-modern-archive-desktop-dark](43-modern-archive-desktop-dark.png) | The rebuilt archive renders the dark desktop status and roles. |

### Styling measurements before the favicon-only correction

- The browser loads one Inter variable face with status `loaded` through `document.fonts.load`.
- The old implementation loads zero Inter faces and returns 404 for both new resource paths.
- `document.fonts.check` alone is not sufficient: it reports true when the requested family has no registered face.
- External resource requests remain zero for the source and rebuilt archive.
- Missing and incorrect capabilities return 403. The valid capability returns 200 without the private transcript marker.
- The page contains six role rows and nine disclosures. Literal script-like text creates no inline script.
- Numeric role columns align right and use tabular numerals.
- Page scroll width equals its viewport: 1280 desktop and 390 mobile CSS pixels.
- Mobile table containers are 330 pixels wide; their content scrolls across 760 or 1080 pixels.
- Both official images load with natural widths of 128 and 560 pixels.
- Invalid JSON retains six role rows and nine open disclosures.
- Its stale warning stays at y=8 while the header ends above the viewport at y=-491.75.
- A valid rewrite hides the warning and retains all nine open disclosures.
- Dark zero-case text contrast is 8.77:1 against the section surface.
- The rebuilt archive measures 19,443,221 bytes, below the unchanged 24 MiB ceiling.

### Final favicon-only header

The user excludes the README hero image from the watcher.
The parent visually reviews these four replacement overviews.[^3]

| PNG | Observable result |
| --- | --- |
| [44-favicon-source-desktop-light](44-favicon-source-desktop-light.png) | Source CLI: light desktop header uses the favicon beside the title and status. |
| [45-favicon-source-mobile-dark](45-favicon-source-mobile-dark.png) | Source CLI: dark mobile header fits without the hero image. |
| [46-favicon-archive-mobile-dark](46-favicon-archive-mobile-dark.png) | Rebuilt archive: dark mobile header uses only the favicon. |
| [47-favicon-archive-desktop-light](47-favicon-archive-desktop-light.png) | Rebuilt archive: light desktop layout matches the source page. |

The source and rebuilt archive render one image: `/favicon.png`, natural width 128 and displayed width 32.
Neither page requests `/brand.png`. Both load Inter and make zero external resource requests.
The page fits 1280-pixel desktop and 390-pixel mobile viewports.
Source and archive assertions reject a page that still references the hero.
The final rebuilt archive measures 19,441,918 bytes, below the unchanged 24 MiB ceiling.

These checks cover Linux and the T3 browser only.
The earlier functional transition series remains evidence for unchanged rendering behavior, not the current palette.

[^2]: packages/skillz/src/skillz_experiments/run/_assets/VENDOR.md; packages/skillz/tests/skillz_experiments/test_watch.py; T3 browser checks, 2026-11-10.

[^3]: User instruction, 2026-11-10; packages/skillz/src/skillz_experiments/run/_watch_page.py:14-20; packages/skillz/tests/skillz_experiments/test_watch.py:168-169,238-239; real source/archive T3 browser measurements and captures 44–47.

_Source: PR #691 T3 browser checks and screenshot review · Updated: 2026-11-10_

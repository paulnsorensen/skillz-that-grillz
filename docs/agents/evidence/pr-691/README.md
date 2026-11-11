# PR 691 browser evidence

Only the four latest favicon-only screenshots remain.
They capture the real source CLI and rebuilt archive on Linux in the T3 browser.
All data is synthetic. PNGs omit the private URL fragment.
These captures precede the merge-conflict cleanup; the cleanup does not change watcher rendering.

## Reproduce

1. Start `uv run --locked --project packages/skillz python -m skillz_experiments watch <temporary-out>`.
2. Open the complete private URL from stdout in the T3 browser.
3. Copy `packages/skillz/tests/skillz_experiments/fixtures/watch/run.json` to `<temporary-out>/run.json`.
4. Set desktop 1280×900 and mobile 390×844 viewports.
5. Check light and dark appearance.
6. Repeat with `uv run python skills/skillz/scripts/skillz-experiment.pyz watch <temporary-out>`.
7. Stop each watcher after the check.

## Latest screenshot series

The parent visually reviews these four captures.
The header uses the official favicon, not the README hero.[^1]

| PNG | Observable result |
| --- | --- |
| [44-favicon-source-desktop-light](44-favicon-source-desktop-light.png) | Source CLI: light desktop header uses the favicon beside the title and status. |
| [45-favicon-source-mobile-dark](45-favicon-source-mobile-dark.png) | Source CLI: dark mobile header fits without the hero image. |
| [46-favicon-archive-mobile-dark](46-favicon-archive-mobile-dark.png) | Rebuilt archive: dark mobile header uses only the favicon. |
| [47-favicon-archive-desktop-light](47-favicon-archive-desktop-light.png) | Rebuilt archive: light desktop layout matches the source page. |

The source and archive render one image: `/favicon.png`, natural width 128 and displayed width 32.
Neither page requests `/brand.png`. Both load Inter and make zero external resource requests.
Page width equals the viewport: 1280 pixels on desktop and 390 pixels on mobile.
Source and archive tests reject a page that still references the hero.[^1]

## Retained verification results

These measurements record the earlier functional checks, not additional screenshots or a new browser run.
The synthetic fixture covers all six sections, empty and populated records, expanded details, faults, and budget states.[^2]

- Missing and wrong capabilities return HTTP 403. Valid access returns 200 with no private transcript data.
- Failed evaluations remain unscored. Genuine zeros stay zero. Unmeasured tokens display `-`.
- Numeric role columns align right and use tabular numerals.
- Local Inter loads through `document.fonts.load`; the earlier implementation registers no Inter face.
- Dark zero-case contrast measures 8.77:1 against the section surface.
- A live rewrite preserves nine open disclosures.
- Invalid JSON retains six role rows and nine open disclosures.
- The stale warning stays at y=8 while the detailed header is offscreen.
- A valid rewrite clears the stale warning without closing disclosures.
- Literal script-like reasons and hypotheses do not execute.
- The cutoff fixture shows outcomes 205 through 6, while the leaderboard counts all 205.

These checks cover Linux and the T3 browser only. macOS and other browsers are not run.
The user requests removal of all superseded screenshots.[^3]

[^1]: packages/skillz/src/skillz_experiments/run/_watch_page.py:14-20; packages/skillz/tests/skillz_experiments/test_watch.py; T3 source/archive captures 44–47, 2026-11-10.
[^2]: packages/skillz/tests/skillz_experiments/fixtures/watch/run.json; T3 browser measurements recorded before the merge cleanup, 2026-11-08 through 2026-11-10; packages/skillz/src/skillz_experiments/run/_assets/VENDOR.md.
[^3]: User instruction, 2026-11-10: “fix the merge conflicts, and only use the latest screenshots - we don't need the old ones”.

_Source: PR #691 T3 browser checks and approved screenshot cleanup · Updated: 2026-11-10_

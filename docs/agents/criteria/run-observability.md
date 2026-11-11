# run-observability: query and watch an autoimprove run from run.json

Source: reconstructed from the run-observability task brief (2026-11-07), with evidence from the run.json probe of PR #689

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN an agent runs the `case-scores`, `unmet-criteria`, and `dealbreakers` queries of `references/run-analytics.md` over a `run.json` THE SYSTEM SHALL list each case with its judged zero-score runs (not evaluation-failed placeholders), each criterion that the judge did not accept with its lowest level and the judge reason of that level, and each `must` criterion at level 1 or 2 with its text from `cases.json` |  |
| AC-2 | test | WHEN an agent runs the `score-by-arm` query THE SYSTEM SHALL give one row per split, arm, and candidate with its cases, runs, judged scored runs, mean score, and genuine zero-score runs, best mean first in each split |  |
| AC-3 | test | WHEN an agent runs the `run-status` and `role-usage` queries over a glob of `run.json` files THE SYSTEM SHALL give one row per run with its phase, calls against the call cap, accumulated call seconds separate from the wall-clock budget, search stop, and gate verdict, and one row per recorded role with its calls, seconds, available tokens, and latency. Task usage falls back to `usage` only when `task_usage` is absent |  |
| AC-4 | test | WHEN an agent runs the `reflections` query THE SYSTEM SHALL give each proposer step in run order with its hypothesis, notes, parent, proposed candidate, and changed-file count |  |
| AC-5 | test | WHEN the pack changes THE SYSTEM SHALL keep one `run-analytics.md` section per query, each with a `{RUN_JSON}` placeholder and no statement that writes |  |
| AC-6 | test | WHEN a user runs `skillz-experiment watch <out>` from the source package or the bundled archive THE SYSTEM SHALL listen on 127.0.0.1 only, print a private capability URL as the first stdout line, exit 0 on an interrupt, and stop with a coded error for a port outside 0 to 65535 or a port in use |  |
| AC-7 | test | WHEN the runner writes or rewrites `<out>/run.json` while `watch` runs THE SYSTEM SHALL serve, without a restart, the phase, calls and accumulated call seconds separate from the wall-clock budget, each role, the leaderboard, each case with its criteria and judge reasons, the reflections, and the newest outcomes |  |
| AC-8 | test | WHEN a client calls the `watch` server THE SYSTEM SHALL answer only GET requests for the page, its script, its bundled styles, its Inter font, its two bundled PNG images, and `/api/run`, only for a Host header of its own loopback address, and require the printed private capability for run data, with a same-origin content security policy, without a transcript or candidate file in the data, and without a write under `<out>` |  |
| AC-9 | test | WHEN `run.json` is absent, is not JSON, is a symlink, or holds malformed fields THE SYSTEM SHALL report the waiting or error state, or leave out the malformed parts, and SHALL show the record again once it is valid. Retained tables SHALL have a persistent stale-data warning after a failed refresh |  |

## Accounting and private access

Run observability counts evaluation failures as attempts, not judged scores. A failure placeholder does not lower a candidate mean.
Exact-JSON task usage comes from `usage` only when `task_usage` is absent. Recorded calls and seconds keep a role visible without usage.[^1]

The watcher protects private run data with a random capability in its printed URL. Loopback and Host checks remain separate restrictions.
Keep the printed URL private. The browser sends the capability with data requests, not with public static assets.[^2]

Accumulated call seconds can overlap during concurrent calls. They do not measure elapsed wall-clock budget use.[^3]

## Watcher branding and retained data

The watcher serves its favicon and header from bundled PNG resources without external requests.
ImageMagick creates display-sized copies from `public/favicon.png` and `assets/skillz-that-grillz-logo.png`; the originals remain unchanged.
The favicon is 128×128 pixels. The header is 560×305 pixels.
Source and bundled CLI tests compare packaged response bytes, PNG signatures, dimensions, and a 200 KiB image ceiling.[^4]

Use compact copies rather than change Wedge packaging.
Wedge includes first-party resources in five interpreter trees and the source tree.
Full-size images exceed the unchanged 24 MiB frozen-file limit.[^5]

The binary resources also freeze the experiment target during `prose+cli` planning.
The helper remains editable. The named analytics target now fits after the planner excludes the experiment source tree.
The real-skill integration check must assert both editable targets and keep experiment sources and archives outside editable files.
This follows existing binary-resource and target-order rules; it does not change the planner or its limits.[^6]

The watcher marks retained tables after a failed refresh with a sticky stale-data warning.
The notice remains visible when the detailed header scrolls away.
Read errors, missing records, missing capabilities, and connection failures activate it after a successful record.
A valid response clears it before the unchanged-timestamp shortcut.
Before any successful record, the notice stays hidden because no retained data exists.

The user approves locally bundled Bulma 1.0.4 and Inter variable 5.3.0 for the minimalist appearance.
Pico is not selected because its official maintenance page reports archival and no future releases.
Only `font-src 'self'` extends the content security policy. External resources remain blocked.
The CSS and Latin variable font use exact allowlisted routes. Bundled licenses and SHA-256 values record their provenance.[^7]

The watcher header uses only the official favicon beside the title and status.
The user excludes the README hero image from the page.[^8]

Tables keep readable column widths and scroll inside their sections on mobile.
[PR 691 browser evidence](../evidence/pr-691/README.md) records screenshot review and measured light and dark contrast.

[^1]: packages/skillz/src/skillz_experiments/run/_watch_view.py; skills/skillz/references/run-analytics.md
[^2]: packages/skillz/src/skillz_experiments/run/_watch.py; packages/skillz/src/skillz_experiments/run/_watch_page.py
[^3]: packages/skillz/src/skillz_experiments/_budget.py; packages/skillz/src/skillz_experiments/run/_holdout.py
[^4]: packages/skillz/tests/skillz_experiments/test_watch.py:169-176,225-232; packages/skillz/src/skillz_experiments/run/_assets/
[^5]: decisions/wedge-skill-packaging.md:131-140 (ADR-014); packages/skillz/src/skillz_experiments/_candidate.py:21; PR #691 full gate reports a 26.0 MiB archive before image resizing.

[^6]: packages/skillz/src/skillz_experiments/run/_wedge_targets.py:144-174,450-485; packages/skillz/src/skillz_experiments/run/_wedge_fit.py:107-158; criteria/skillz-wedge-first-class.md:7,11,17.

[^7]: packages/skillz/src/skillz_experiments/run/_assets/VENDOR.md; packages/skillz/src/skillz_experiments/run/_watch.py:25-30,96-99; https://picocss.com/docs/maintenance

[^8]: User instruction, 2026-11-10: “use the favicon, not the hero part in the README”; packages/skillz/src/skillz_experiments/run/_watch_page.py:14-20; packages/skillz/tests/skillz_experiments/test_watch.py:168-169,238-239.

_Source: PR #691 review and approved local styling, branding, and stale-data behavior · Updated: 2026-11-10_

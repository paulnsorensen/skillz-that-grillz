# wedge-site-layer-cache: reuse a verified site layer instead of a fresh install on every build

Source: reconstructed from the task brief that measured the `wedge build` file churn of the skillz e2e tests (2026-10-24)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN a build finds a cache entry for its key whose every file matches the entry's manifest, THE SYSTEM SHALL build from that entry without an install, and the output SHALL equal the output of a fresh build. |  |
| AC-2 | test | WHEN a file of a cached layer is edited, added, removed, or replaced by a symlink, or the manifest is edited, removed, or corrupt, THE SYSTEM SHALL discard the entry, install the layer again, and give the output of a fresh build. |  |
| AC-3 | test | WHEN builders populate one key at the same time, OR an install fails, OR an entry has been unused for 30 days, THE SYSTEM SHALL leave at most one complete entry per key and no staging directory. |  |
| AC-4 | test | `build_from_layer` SHALL read the clean top-level entries of a layer through links, SHALL never write to the layer, and SHALL give the same bytes as a build from a stripped copy of the layer. |  |
| AC-5 | test | WHEN a cached layer changes after the cache checked it and before shiv reads it, THE SYSTEM SHALL fail the build and write no archive. |  |

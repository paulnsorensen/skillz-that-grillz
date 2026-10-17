# skillz-search-free-repeats: free repeats and skipped minibatches do not end the search

Source: skillz-autoimprove ADR-016 amendment of 2026-10-17

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN the parent scores 1.0 on most train cases and the caller serves repeats from its record THE SYSTEM SHALL keep the search going until it scores `metric_calls` distinct candidate and case pairs |  |
| AC-2 | test | WHEN the parent scores 1.0 on every train case THE SYSTEM SHALL end the search after `skip_limit` skipped iterations |  |
| AC-3 | process | WHEN this change merges THE SYSTEM SHALL pass `just build` | `just build` exits 0 |
| AC-4 | test | WHEN the caller rejects a proposal before any live call THE SYSTEM SHALL not count its candidate and case pairs toward `metric_calls` |  |

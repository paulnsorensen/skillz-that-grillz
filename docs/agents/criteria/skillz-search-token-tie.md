# skillz-search-token-tie: a token-rule search can find a cheaper candidate on saturated cases

Source: skillz-autoimprove ADR-016 amendment of 2026-10-22

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN the token rule is on THE SYSTEM SHALL reflect on a train minibatch that the parent scores perfectly, and admit a child with an equal minibatch score sum to the pool only when the child has known, lower mean task tokens on that minibatch |  |
| AC-2 | test | WHEN the token rule is off THE SYSTEM SHALL skip a perfect minibatch and admit only a child with a higher minibatch score sum, as before |  |
| AC-3 | test | WHEN the seed scores 1.0 on every case, a proposal ties it with fewer task tokens, and `min_token_saving` is set THE SYSTEM SHALL pick the proposal as the winner and the gate SHALL give `promote-cheaper` |  |
| AC-5 | test | WHEN a case fixture file name already occurs in a seed file THE SYSTEM SHALL not report `fixture-name` for that name in any component, a new `@new-cli` included |  |
| AC-4 | process | WHEN this change merges THE SYSTEM SHALL pass `just build` | `just build` exits 0 |

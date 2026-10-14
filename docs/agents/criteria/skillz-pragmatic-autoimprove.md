# skillz-pragmatic-autoimprove: pragmatic autoimprove

Source: spec skillz-pragmatic-autoimprove (durable spec corpus, created 2026-10-06; landed in PR #161 and later PRs)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN the Claude transport starts on Linux THE SYSTEM SHALL keep `HOME` as the isolated workspace home, set `CLAUDE_CONFIG_DIR` to a temporary directory outside the workspace that holds only a symlink to the user's `.credentials.json`, forward no token variable, and ask for no key or token. |  |
| AC-2 | test | WHEN a Claude run ends THE SYSTEM SHALL delete the temporary config directory, and SHALL stop with the coded error `credential-changed` when the linked credential path no longer resolves to the same file. Refined by ADR-001 (`docs/agents/decisions/skillz-pragmatic-autoimprove.md`): `credential-rotated` (a replaced login file) and `credential-refreshed` (a refreshed login) are resumable warnings; every other change stops with `credential-changed`. |  |
| AC-3 | test | WHEN the Claude transport starts on macOS THE SYSTEM SHALL use the real config directory, and the live preflight SHALL stop the run when any skill other than the candidate, a user setting, or `CLAUDE.md` loads. |  |
| AC-4 | test | WHEN the Claude Bash sandbox cannot start THE SYSTEM SHALL stop before any task call with the coded error `sandbox-unavailable` that names the cause and the fix, and SHALL never weaken the sandbox. |  |
| AC-5 | test | WHEN autoimprove makes a model call THE SYSTEM SHALL use only headless `claude -p` or `codex exec` with the existing login. |  |
| AC-6 | test | WHEN the Codex transport starts THE SYSTEM SHALL reuse the existing `auth.json` through its link and ask for no key. |  |
| AC-7 | test | WHEN the search runs THE SYSTEM SHALL run GEPA for more than one iteration with Pareto parent selection and a minibatch pre-gate, and SHALL pick the final candidate by mean validation score. |  |
| AC-8 | process | WHEN the user gives no editable-set option THE SYSTEM SHALL edit only Markdown files; WHEN the user selects prose+cli THE SYSTEM SHALL also edit the helper scripts in the same single search. | Superseded: PR #308 made prose+cli the default with a prose fallback; see test_default_edit.py. |
| AC-9 | test | WHEN the gate runs THE SYSTEM SHALL score baseline and winner on holdout cases that no selection or reflection step saw, with 3 repeats per case by default, and SHALL report `promote` only when the case-clustered paired mean delta exceeds 2·SE, else `inconclusive` or `reject` with the delta, SE, and case count. |  |
| AC-10 | test | WHEN the SE of the case deltas is 0 THE SYSTEM SHALL report `promote` only when every case delta is positive. |  |
| AC-11 | test | WHEN the deterministic simulation self-test runs at equal call budget for 6 to 20 holdout cases THE SYSTEM SHALL show a false-promotion rate at or below 6% under no true effect, and below the select-and-gate-on-one-holdout rule. |  |
| AC-12 | process | WHEN the live head-to-head benchmark runs on one skill's description triggers THE SYSTEM SHALL run skill-creator's `run_loop.py` and autoimprove on the same queries and one sealed test set, report each score and promotion decision, and pass only when autoimprove's sealed-test accuracy is no worse than skill-creator's; the strict advantage comes from AC-11. | pre-gate: PR #161 review; live benchmark only, no automated test. |
| AC-13 | test | WHEN a user starts autoimprove without cases THE SYSTEM SHALL assign splits in code from a recorded seed so that no family crosses splits, and ask one approval question before any model call. |  |
| AC-14 | test | WHEN the case draft holds cases from past sessions THE SYSTEM SHALL show them in the same approval question, each marked with its source. |  |
| AC-15 | test | WHEN a run is sized THE SYSTEM SHALL show the estimated calls and seconds before any model call, ask one approval question, permit up to 200 calls and 7200 seconds only after approval, and stop with the coded error `budget-unapproved` otherwise. |  |
| AC-16 | test | WHEN model calls run THE SYSTEM SHALL run at most 2 at once. |  |
| AC-17 | test | WHEN a first run starts THE SYSTEM SHALL need no hand-written contract or manifest, two CLI commands (`run`, `export`), at most three questions (harness and model, cases, budget), and docs that introduce at most seven concepts. |  |
| AC-18 | test | WHEN a user calls the removed `dataset`, `baseline`, `search --mode`, or `evaluate` commands THE SYSTEM SHALL exit with a coded error that names `run` as the replacement. |  |
| AC-19 | test | WHEN `run` opens a `run.json` from the old schema THE SYSTEM SHALL stop with a coded error that asks for a new run. |  |
| AC-20 | process | WHEN a user starts autoimprove without cases THE SYSTEM SHALL draft should-trigger, should-not-trigger, and task cases from the target skill. | `skills/skillz/references/experiments.md`, section "Draft the cases" (agent drafting procedure). |
| AC-21 | process | WHEN session analytics is available THE SYSTEM SHALL draft extra cases from past sessions with `source: session`. | `skills/skillz/references/experiments.md`, section "Draft the cases" (agent drafting procedure). |

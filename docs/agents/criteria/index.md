# criteria

Acceptance criteria that tests trace to. The gate `.github/scripts/check_ac_trace.py` runs in `just build` and `just ci`.

## Format

One file per source, named `<source>.md`. A source is a durable spec slug (`skillz-wedge-first-class`) or a ticket (`gh-<issue number>`). The file holds one H1, one `Source:` line, and one table:

```markdown
| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN ... THE SYSTEM SHALL ... |  |
| AC-2 | process | WHEN ... THE SYSTEM SHALL ... | pre-gate: PR #1 review |
```

- `id` keeps the original criterion id (`AC-3`, `AC-W1`). It is unique in its file.
- `kind` is `test` or `process`.
- A `test` row needs at least one test marked `@pytest.mark.ac("<source>:<id>")`.
- A `process` row names its evidence in `verified by` and needs no marked test. A criterion of a source that merged before the gate, with no marked test, is `process` with `pre-gate: PR #<n> review`.
- The `Source:` line holds the spec slug, the issue URL, or `reconstructed from <where>` when the spec text is absent from the repository.

## Markers

A marker argument is `<source>:<id>`. A bare id such as `AC-3` fails pytest collection in skillz, wedge, and fromargs. The gate scans `packages/{skillz,wedge,fromargs}/tests` and `.github/scripts/test_*.py` with `ast`. It counts a decorator, a module or class `pytestmark`, and `pytest.param(marks=...)`.

The gate fails on a `test` row with no marked test, a marker for an unknown source or id, a `process` row without `verified by`, a kind other than `test` or `process`, a non-literal marker argument, a duplicate id, and a malformed table.

A ticket's pull request adds `gh-<n>.md` and its markers together. The decision record is `docs/agents/decisions/ac-traceability.md`.

<!-- HALLOUMINATE:INDEX-START -->
- [autoimprove-authoring-headroom](./autoimprove-authoring-headroom.md) — autoimprove-authoring-headroom: stop a run when the seed already saturates a family
- [autoimprove-live-proof](./autoimprove-live-proof.md) — autoimprove-live-proof: the autoimprove journey promotes a planted-defect fix with a suggested CLI
- [fromargs-benefit-smoke](./fromargs-benefit-smoke.md) — fromargs-benefit-smoke: autoimprove writes its own eval and ships a fromargs CLI
- [fromargs-cli-library](./fromargs-cli-library.md) — fromargs-cli-library: fromargs public contract
- [gh-131](./gh-131.md) — gh-131: the network preflight probes and the macOS live path
- [gh-176](./gh-176.md) — gh-176: the Read tool cannot read host files outside the workspace
- [gh-223](./gh-223.md) — gh-223: skillz_experiments module layout and borrow waves
- [gh-235](./gh-235.md) — gh-235: freeze a run compatibility fingerprint (W2.4)
- [gh-236](./gh-236.md) — gh-236: deterministic proposal leakage check (W3.1)
- [gh-238](./gh-238.md) — gh-238: move the reflection prompt into search/_proposer.py (W4.1)
- [gh-239](./gh-239.md) — gh-239: annealed edit budget and run-status block in the reflection prompt (W4.2)
- [gh-240](./gh-240.md) — gh-240: detect search stalls and stop GEPA through stop_callbacks (W4.3)
- [gh-241](./gh-241.md) — gh-241: record per-proposal attempts and show broke/fixed history to the proposer (W4.4)
- [gh-242](./gh-242.md) — gh-242: show rejected approaches and proposer notes in the reflection prompt (W4.5)
- [gh-243](./gh-243.md) — gh-243: keep private cross-run attempt history for compatible runs (W4.6)
- [gh-244](./gh-244.md) — gh-244: steer a stalled search toward the least-tested editable component (W4.7)
- [gh-247](./gh-247.md) — gh-247: move the evaluator into evaluation/ and memoize check_candidate per candidate (W5.3)
- [gh-267](./gh-267.md) — gh-267: acceptance-criterion traceability gate
- [gh-279](./gh-279.md) — gh-279: reuse recorded search outcomes on resume
- [gh-285](./gh-285.md) — gh-285: isolate the temp root of the journey sandbox tests
- [gh-287](./gh-287.md) — gh-287: retry a contract check that fails by sandbox timeout
- [gh-290](./gh-290.md) — gh-290: the settings floor and the approval block in the preflight
- [gh-291](./gh-291.md) — gh-291: keep the preflight event transcript when the preflight fails
- [gh-292](./gh-292.md) — gh-292: seeded trigger and near-miss cases default family to the case id
- [gh-293](./gh-293.md) — gh-293: read block-scalar descriptions in full in the facts audit
- [gh-294](./gh-294.md) — gh-294: --edit prose with non-Markdown editable names stops with editable-needs-cli
- [gh-295](./gh-295.md) — gh-295: the doctor checks the run's claude executable, and a nonzero inventory exit stops with host-not-ready
- [gh-296](./gh-296.md) — gh-296: the cases-missing stop writes the seed to a file and shows only counts
- [gh-298](./gh-298.md) — gh-298: document code-enforced fixture and case rules in experiments.md
- [gh-299](./gh-299.md) — gh-299: the shipped skillz contract scores task cases
- [gh-300](./gh-300.md) — gh-300: the train split gets at least 4 scored cases from 2 families
- [gh-301](./gh-301.md) — gh-301: finish the judge rationale record
- [gh-302](./gh-302.md) — gh-302: prose+cli proposals leak runner context into candidate code comments
- [gh-305](./gh-305.md) — gh-305: Codex task temp files have no .git ancestor
- [gh-311](./gh-311.md) — gh-311: assert where the journey press test creates its workspace
- [gh-312](./gh-312.md) — gh-312: a contract check returns one typed result, and checks of different candidates run at the same time
- [gh-316](./gh-316.md) — gh-316: a run that is mostly provider faults stops with a code and scores no fault as zero
- [gh-317](./gh-317.md) — gh-317: score the baseline on the holdout when the search retains the seed
- [gh-318](./gh-318.md) — gh-318: the Codex adapter retries a rate limit and keeps diagnostic text
- [gh-319](./gh-319.md) — gh-319: one model call cannot use the whole search window, and calls reconcile with outcomes
- [gh-322](./gh-322.md) — gh-322: sandboxed Bash cannot read host files outside the workspace
- [gh-323](./gh-323.md) — gh-323: sandboxed Bash cannot reach a host pathname Unix socket
- [gh-327](./gh-327.md) — gh-327: make _validate_answer strict and order-insensitive for nullable required keys
- [gh-332](./gh-332.md) — gh-332: the doctor detail of a missing host tool says that the tool is missing
- [gh-336](./gh-336.md) — gh-336: export writes no empty patch when the winner equals the baseline
- [gh-339](./gh-339.md) — gh-339: the live smoke test fails on an unexpected preflight stop
- [gh-340](./gh-340.md) — gh-340: the live smoke resume scenario reports a stop before the interrupt
- [gh-364](./gh-364.md) — gh-364: a runner SIGKILL of the Gemini wrapper group does not orphan the sandbox child
- [gh-366](./gh-366.md) — gh-366: explicit eval kinds and one evals.json validator
- [gh-367](./gh-367.md) — gh-367: author grader gates as files under evals/gates
- [gh-368](./gh-368.md) — gh-368: typed journey expected and multiple journey kinds
- [gh-369](./gh-369.md) — gh-369: check in journey cases as evals/journeys.json
- [gh-370](./gh-370.md) — gh-370: two author files, a validate command, and generated schemas
- [gh-384](./gh-384.md) — gh-384: run journeys from a copy of skills/skillz
- [gh-385](./gh-385.md) — gh-385: score journeys by their gate and judge
- [gh-388](./gh-388.md) — gh-388: the skillz body exceeds its 5k-token budget
- [gh-389](./gh-389.md) — gh-389: move single-consumer root modules into their slices
- [gh-390](./gh-390.md) — gh-390: a task case stays unscored in a journey-only run
- [gh-391](./gh-391.md) — gh-391: skillz imports the wedge API from the wedge package, not from private modules
- [gh-392](./gh-392.md) — gh-392: the journey gate accepts only a runner budget stop as a budget question
- [gh-394](./gh-394.md) — gh-394: omp and pi context_tokens counts cache tokens
- [gh-395](./gh-395.md) — gh-395: a macOS sandboxed command forges a sandbox setup failure through stderr
- [gh-396](./gh-396.md) — gh-396: split the live preflight and the converse loop out of ClaudeCode
- [gh-397](./gh-397.md) — gh-397: tool_use and tool_result pairing is written once
- [gh-398](./gh-398.md) — gh-398: Close the run provider port
- [gh-399](./gh-399.md) — gh-399: the run slice reaches git and the history store through ports
- [gh-400](./gh-400.md) — gh-400: the run Session delegates each phase to its own module
- [gh-401](./gh-401.md) — gh-401: the search stops on an uncoded proposer exception
- [gh-402](./gh-402.md) — gh-402: the Gemini wrapper refuses an infer model that is empty or looks like an option
- [gh-403](./gh-403.md) — gh-403: links inside code fences and code spans are not local link targets
- [gh-404](./gh-404.md) — gh-404: an input below a symlink loop stays inside the inspect error contract
- [gh-405](./gh-405.md) — gh-405: the journey gate reads run flags after an unquoted command substitution
- [gh-406](./gh-406.md) — gh-406: wedge strips install metadata only inside *.dist-info
- [gh-407](./gh-407.md) — gh-407: the codex adapter builds the row envelope once
- [gh-408](./gh-408.md) — gh-408: harness adapters and table SQL live outside _ingest.py
- [gh-409](./gh-409.md) — gh-409: the skillz package content id covers file paths and fails closed
- [gh-410](./gh-410.md) — gh-410: the protected train cases follow the recorded identity of the best validated candidate
- [gh-411](./gh-411.md) — gh-411: fromargs bounds its traceback files in a per-user directory
- [gh-412](./gh-412.md) — gh-412: wedge check reports a key failure as a per-skill issue
- [gh-413](./gh-413.md) — gh-413: the wedge publish policy runs against a release store, not the gh CLI
- [gh-414](./gh-414.md) — gh-414: inline links with a title or an angle-bracketed destination are local link targets
- [gh-415](./gh-415.md) — gh-415: a session that stops in its constructor leaves no provider open
- [gh-416](./gh-416.md) — gh-416: the attempt history and the session share one fault rule and one validation-mean rule
- [gh-417](./gh-417.md) — gh-417: a non-finite delta keeps the family rule and names its reason
- [gh-418](./gh-418.md) — gh-418: a Gemini wrapper timeout stops the container first and bounds the pipe drain
- [gh-419](./gh-419.md) — gh-419: the Gemini dotenv guard stays inside the temporary directory and keeps existing files
- [gh-420](./gh-420.md) — gh-420: the journey gate resolves `--out` and `--target` against a `cd` in the same command
- [gh-448](./gh-448.md) — gh-448: Claude harness accepts a built-in skill that registers late
- [gh-459](./gh-459.md) — gh-459: the planner admits a run that the search phase cannot start
- [gh-460](./gh-460.md) — gh-460: stop a live preflight call timeout with a resumable code
- [gh-493](./gh-493.md) — gh-493: a run stops when its --out directory is inside the --target skill
- [gh-494](./gh-494.md) — gh-494: tag each slice in SLICES with an ADR id and freeze the slice set
- [gh-498](./gh-498.md) — gh-498: add an architecture-drift item to the review-bot instructions
- [gh-499](./gh-499.md) — gh-499: reject proposals that copy harness text into the target (#320 Part 3)
- [gh-500](./gh-500.md) — gh-500: repeat validation runs and gate the incumbent with the 2-SE rule
- [gh-501](./gh-501.md) — gh-501: give the proposer unmet criteria, edit hints, and gate output, and log each reflection (#320 Part 1)
- [gh-508](./gh-508.md) — gh-508: export a completed run from an earlier engine when its replay patch digest matches
- [gh-512](./gh-512.md) — gh-512: the shared redactor redacts dotted, multiply escaped, and Unicode-quoted credential keys
- [gh-529](./gh-529.md) — gh-529: promote-cheaper uses the SE of the losses, and a cheaper tie is not a stall
- [gh-537](./gh-537.md) — gh-537: the proposer feedback carries task tokens when the run sets a token rule
- [gh-538](./gh-538.md) — gh-538: the live smoke builds the skill archives before the tests
- [gh-563](./gh-563.md) — gh-563: a large editable set gets a longer reflection timeout, a prose fallback or a warning, and an idle stop trigger
- [gh-566](./gh-566.md) — gh-566: Codex skill discovery accepts the canonical skill path on macOS
- [gh-568](./gh-568.md) — gh-568: Live.kill treats an exited child as a gone group
- [gh-571-calibrate](./gh-571-calibrate.md) — gh-571-calibrate: the plan estimate charges the measured per-call time
- [gh-571](./gh-571.md) — gh-571: the run estimate shows the prompt-scaled reflection time
- [gh-580](./gh-580.md) — gh-580: a fixture that many cases share counts once against the seed bound
- [gh-582](./gh-582.md) — gh-582: The doctor names the nested-sandbox fix before the AppArmor fix
- [gh-591](./gh-591.md) — gh-591: CodeRabbit includes the source branch
- [gh-596](./gh-596.md) — gh-596: reduce pure-Python CLI startup imports
- [judge-criteria-verdicts](./judge-criteria-verdicts.md) — judge-criteria-verdicts: the judge grades typed case criteria, and code computes the score
- [kit-harder-journeys](./kit-harder-journeys.md) — kit-harder-journeys: harder journeys for the self-autoimprove kit
- [proposer-cli-early](./proposer-cli-early.md) — proposer-cli-early: the reflection prompt offers @new-cli early and keeps room for its two edits
- [skillz-autoimprove-claims](./skillz-autoimprove-claims.md) — skillz-autoimprove-claims: validate the three autoimprove claims separately
- [skillz-experiments-end-to-end](./skillz-experiments-end-to-end.md) — Skillz experiments end-to-end prototype criteria
- [skillz-pragmatic-autoimprove](./skillz-pragmatic-autoimprove.md) — skillz-pragmatic-autoimprove: pragmatic autoimprove
- [skillz-search-free-repeats](./skillz-search-free-repeats.md) — skillz-search-free-repeats: free repeats and skipped minibatches do not end the search
- [skillz-search-token-tie](./skillz-search-token-tie.md) — skillz-search-token-tie: a token-rule search can find a cheaper candidate on saturated cases
- [skillz-wedge-first-class](./skillz-wedge-first-class.md) — skillz-wedge-first-class: wedge first-class in autoimprove
- [wedge-site-layer-cache](./wedge-site-layer-cache.md) — wedge-site-layer-cache: reuse a verified site layer instead of a fresh install on every build
- [wedge-skill-packaging](./wedge-skill-packaging.md) — wedge-skill-packaging: wedge skill packaging
<!-- HALLOUMINATE:INDEX-END -->

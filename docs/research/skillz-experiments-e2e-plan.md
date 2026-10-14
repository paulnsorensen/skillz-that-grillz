---
slug: skillz-experiments-end-to-end
status: draft
source: mold-handshake
created: 2026-10-12
confidence: high
leverage: [invariant-gap]
gates_overridden: []
execution_holds: ["Implementation is not requested.", "Select repairs before adding regressions that cannot pass on the current tree.", "Approve any new guard or statistical policy separately.", "Confirm a real OS isolation lane before claiming sandbox coverage."]
agent_introduced_scope: []
goal_coverage:
  G-1: covered
  G-2: covered
  G-3: covered
  G-4: covered
  G-5: covered
  G-6: covered
  G-7: covered
entity_referent_bindings: [{"noun": "arm", "verdict": "Bound", "referent": "Session.gate baseline and winner", "citation": "packages/skillz/src/skillz_experiments/run/_session.py:686-690", "note": "Test variants are not new public search arms."}, {"noun": "edit mode", "verdict": "Bound", "referent": "search.Edit", "citation": "packages/skillz/src/skillz_experiments/search/_engine.py:13", "note": "Only prose and prose+cli remain supported."}]
agent_resolution:
  - role: grounding
    selected_type: explorer
    effort: medium
    degraded: false
  - role: fork-coherence
    selected_type: taste-tester
    effort: medium
    degraded: false
landing:
  shape: single
  layers: []
  per_layer_green: required
  review_fixes: fold
---

# Skillz experiments end-to-end test plan

## Problem

This document records the original planning task.
The [prototype implementation report](skillz-experiments-e2e-prototype.md) records current coverage, verification, and remaining gaps.
Its upstream integration also updates the historical RRSI annealing statement below.

Build an end-to-end test plan for each current experimental arm and write it down.

Existing tests prove many components, but not every production path from a vendor process to a promoted executable patch.
This plan joins those paths without calling a real model.
It preserves real CLI, adapter, evaluator, GEPA, gate, build, and export behavior.[^audit]
It proposes tests only. It does not authorize production fixes, guard changes, or a new statistical policy.

Grounded revision: `6749d9541a4d13a62422311bb572700ae00e1865`.
The tracked tree is clean when planning starts.

## Goals

- G-1: Cover every current edit variant and both baseline and winner arms.
- G-2: Join actual process adapters, evaluators, GEPA, holdout, and export.
- G-3: Check exact verdicts and mathematical evidence through the production path.
- G-4: Prove that exported changes reproduce evaluated executable behavior.
- G-5: Cover approval, isolation, budget, resume, and failure boundaries.
- G-6: Define implementation order, file ownership, and repair dependencies.
- G-7: Save the plan without implementing tests or production changes.

## Non-goals

These boundaries preserve the user's planning-only request.

- Do not implement this plan in this session.
- Do not restore removed `cli`-only, `wedge`, or multi-arm search commands.[^modes]
- Do not treat stub performance as evidence of real-model improvement.
- Do not change promotion thresholds, missingness policy, scheduling, or guard behavior without separate approval.
- Do not add the deferred RRSI history, annealing, critic, or pruning loops.
- Do not publish a PR, install a winner, or change the user's real login.

## Deferred follow-ups

- **D-1** — Apply selected findings from the experiment assessment.
  - Destination: local_draft
  - State: linked
  - Reference: `.cheese/age/skillz-experiments-audit-body.md`
- **D-2** — Decide statistical policy for missing repeats, family dependence, token uncertainty, and temporal drift.
  - Destination: local_draft
  - State: prepared
  - Reference: Open questions and dependency table below.
- **D-3** — Measure live model quality and out-of-distribution performance after orchestration tests pass.
  - Destination: local_draft
  - State: prepared
  - Reference: Non-goals; this plan makes no live-quality claim.

## Grounding

| Probe | Outcome | Evidence |
| --- | --- | --- |
| wiki | hit | `docs/agents/decisions/skillz-autoimprove.md:7-10,77-96` removes old arms and defines journeys; no retrieval warnings |
| explorer | hit | `e2e_ground` reads actual fixture and adapter paths; source ranges appear in References |

Reused assessment evidence remains valid because source and configuration are unchanged.
The earlier assessment covers all twelve review dimensions.
This plan does not reopen that review.

## Approach

### 1. Define the axes before building tests

An **arm** is the baseline or winner evaluated on the same sealed holdout.
An **edit mode** is `prose` or `prose+cli`.
A **variant** selects a concrete target and case kind.
A **harness** is Claude, Codex, or a trusted command wrapper.

This distinction prevents test names from recreating retired public search arms.

### 2. Build this production path

```text
source or installed-bundle CLI subprocess
  -> cases and budget approvals
  -> Configuration.create
  -> actual Claude/Codex/command adapter
  -> deterministic external process stub
  -> Harness.evaluate and real grader
  -> GEPA 0.1.4 with real validation
  -> baseline and winner holdout, three repeats
  -> exact gate verdict and evidence
  -> export
  -> apply patches to a disposable target copy
  -> execute the exported helper or replay the exported prose
```

Keep `Configuration.create`, `Harness.evaluate`, `_evaluator.evaluate`, GEPA, the gate, and export unpatched.
Do not inject a winner, fabricate a completed run, or return a canned case score.
The current CLI completion test replaces the provider factory; the new suite must not.[^factory]

### 3. Create a test-only fixture layer

Proposed location: `packages/skillz/tests/skillz_experiments/e2e/`.

| File or directory | Responsibility |
| --- | --- |
| `conftest.py` | Isolated environment, real CLI subprocess, target factories, dependency staging, snapshots |
| `support.py` | Approval driver, public-result parsing, independent evidence calculations, patch replay |
| `fixtures/vendor_common.py` | Actual file/tool execution, bounded protocol logs, task behavior |
| `fixtures/claude.py` | Version/inventory, print mode, stream mode, init/results, task/judge/reflection/simulator |
| `fixtures/codex.py` | Version, exec help, app-server discovery, sandbox boundary, exec events and last-message file |
| `fixtures/command.py` | Versioned sandbox/discover/infer requests and response envelopes |
| `test_protocols.py` | Validate the external stubs before using them as an oracle |
| `test_prose.py` | Prose-only variant |
| `test_helpers.py` | Ordinary helper mutation |
| `test_wedge.py` | Existing target mutation, including sources inside the skill |
| `test_new_cli.py` | Generated targets and frozen-path protection |
| `test_journeys.py` | Multi-turn, helper, and mixed journeys |
| `test_verdicts.py` | Promote/reject/inconclusive/cheaper and mathematical evidence |
| `test_failures.py` | Resume, missing telemetry, malformed output, collisions, and cleanup |
| `test_bundle.py` | Source versus installed-bundle parity and launch outside the checkout |

Reuse existing target layouts, streaming protocols, and patch helpers.
Do not make a new general-purpose mock framework.

The existing parent `conftest.py` installs no-op harness binaries and fakes host readiness.[^autouse]
A CLI subprocess does not inherit Python monkeypatches, but it does inherit environment changes.
Give it explicit fixture binaries, fake HOME/login, private temporary directories, and bounded runtime dependencies.
Do not let parent no-op binaries or the real user's vendor executable win PATH resolution.

### 4. Stub only external boundaries

| Boundary | Allowed substitute | Required real behavior |
| --- | --- | --- |
| Claude/Codex model process | Local executable speaking the real protocol | Adapter command construction, schema handling, parsing, and evaluator |
| Reflection model | Complete deterministic proposals from current candidate and train feedback | Real GEPA eligibility, Pareto parents, and validation |
| Judge model | Deterministic rule over the prompt's actual output/reference | Real judge prompt construction, schema, normalization, and pillar aggregation |
| Simulator model | Bounded answers from its supplied hidden brief | Real role routing and journey conversation |
| Vendor token usage | Explicit complete or intentionally incomplete telemetry | Real parsing, aggregation, validation, and token gate |
| OS sandbox primitive | Test double in the orchestration profile only | Actual helper, grader, and command execution in a private fixture workspace |
| Build dependencies | Locally staged source and offline dependency cache | Real build, archive contents, frozen bytes, and post-export execution |

Task stubs must read only the staged skill, visible request, and task files.
They must never read `run.json`, the sealed manifest, expected answers, or the judge rubric.
A reflection stub must not read holdout data.
A simulator must not read expected answers or the rubric.
The judge may read the expected reference because that is its declared role.

For prose-only tests, the stub interprets a small fixture instruction and transforms actual input.
The grader derives correctness from the resulting output.
Do not score words such as “improved,” candidate hashes, or archive presence.

For helper tests, the stub executes the staged helper.
It forwards actual stdout, exit status, and tool traces.
The command adapter must obtain its real execution witness.
Do not fabricate a ledger to make activation pass.[^activation]

Protocol controls:

- Emit the exact load marker for the staged candidate.
- Emit a completed SKILL.md read event backed by a real file read.
- Preserve the actual helper argv, exit status, and output in the trace.
- Return judge percent scores through the real schema.
- Write Codex's `--output-last-message` file and JSON event stream.
- Implement Codex app-server discovery, not only `exec`.
- Keep normal, malformed, unavailable, and missing-usage modes distinct.[^protocols]

The orchestration profile proves wiring, not OS isolation.
Its sandbox substitute must execute actual commands rather than return their expected output.
Mark any synthetic isolation-probe response as a test boundary.
A separate real-isolation profile uses actual OS primitives and fixture vendor binaries.
CI must fail, not silently skip, when that required profile lacks its declared sandbox prerequisite.

### 5. Cover every current variant

Each mandatory positive cell runs through both source CLI and installed bundle.
Each changed winner then reaches both baseline and winner holdout evaluation.
Use one shared scenario runner; entrypoint parameters must not duplicate scenario logic.

| ID | Variant | Edit mode | Claude | Codex | Command | Actual behavior under test |
| --- | --- | --- | --- | --- | --- | --- |
| P0 | Prose only | prose | required | required | required | Edited instruction changes output on actual task files |
| H0 | Ordinary existing helper | prose+cli | required | required | required | Changed script executes and improves output |
| W0 | Existing wedge, source outside skill | prose+cli | required | required | required | Real rebuild and both exported patches |
| W1 | Existing wedge, source inside skill | prose+cli | required | required | required | One physical source matches evaluated bundle and exported source |
| N0 | New `@new-cli` target | prose+cli | required | required | required | Generated target builds, executes, and exports |
| J0 | Multi-turn prose journey | prose | required | unsupported check | unsupported check | Stream, simulator, command gate, transcript judge |
| J1 | Journey with helper mutation | prose+cli | required | unsupported check | unsupported check | Conversation and executed helper determine score |
| M0 | Mixed task/journey manifest | prose | required | unsupported check | unsupported check | Every eligible holdout kind counts and runs |

This is 18 supported harness/variant cells.
Two entrypoints produce 36 mandatory positive runs.
Unsupported cells assert the current coded refusal before task inference.
They are not missing happy-path coverage.

Audit-graded intake also asserts its current unsupported error.
Trigger and near-miss cases assert pending status and exclusion from scoring.
No test claims to measure activation quality from pending cases.

The command column uses the real generic command adapter.
The Gemini reference wrapper receives an additional P0 and H0 wrapper smoke with fake Gemini/runtime executables.
Do not label generic command coverage as Gemini coverage.

### 6. Use small, independent fixture families

Default positive fixture:

- Ten families contain one task case each.
- The recorded split seed yields two train, two validation, and six holdout cases.
- No family crosses a split.
- The baseline passes local candidate/helper checks but fails the task expectation.
- The winner passes those same local checks and solves the task.
- Both holdout arms run three repeats.
- The independent test oracle expects delta 1, SE 0, coverage 1, and `promote`.

Confirm the actual split counts before using this fixture.
If the split algorithm changes, fail the fixture contract rather than silently changing the expected sample.
The test uses the displayed budget estimate for approval; it does not hard-code a maximum call count.

A broken helper cannot serve as the baseline.
Baseline rejection stops before search, so it cannot exercise promotion.
Use a helper with a valid CLI and fixture contract but incomplete task behavior.

A broken proposal that fails its helper check should receive no task-model call.
It should not reach validation or replace the seed.
Do not expect a `reject` holdout verdict when search correctly retains the seed.

### 7. Check exact verdicts without replacing the gate

| ID | Deterministic scenario | Expected public result |
| --- | --- | --- |
| V-P | Changed winner solves held tasks; baseline does not | `promote`, delta 1, SE 0, all planned observations present |
| V-R | Winner improves train/validation but fails a held input feature | `reject`, negative delta outside the band |
| V-I | Changed winner improves search; both arms solve held tasks equally | `inconclusive`, delta 0 |
| V-S | No validated improvement | Seed retained; `winner-equals-baseline`; no holdout task calls |
| V-C | Search improves; held correctness ties; task usage halves; saving enabled | `promote-cheaper`, token_delta −0.5, token_SE 0 |
| V-C-OFF | Same V-C scenario without the saving option | `inconclusive`; no cost-only promotion |
| V-U | Correctness gain but active token rule has unknown usage | `inconclusive`, `unknown-usage` |
| V-L | Three of six planned held cases have both arms scored | `inconclusive`, `low-coverage` at default 0.6 |
| V-F | Positive gain below the declared minimum | `inconclusive` with the exact floor reason |
| V-B | Overall gain but two scored cases in the same family breach its budget | `reject`, exact family-regression reason |
| V-T | Correctness gain exceeds the token-cost budget | `reject`, `token-cost` |
| V-2SE | Mean delta equals exactly twice SE | No ordinary promotion |
| V-ZERO | Zero effective SE with zero delta | No ordinary promotion |

V-B uses a separate manifest with five families and two cases per family.
The recorded seed assigns one train family, one validation family, and three holdout families.
Each held family therefore has two scored cases.
Use actual-output command grading with deterministic repeat deltas `[1,1,1,1,-0.1,-0.1]`.
The two negative deltas belong to the same family with budget 0.
The overall delta is 0.633333, and twice SE is 0.463801.
Ordinary score evidence would promote, but the family's zero-SE regression forces `reject`.
Assert the exact family name and absence of a skip for that family.
The one-case-per-family default fixture cannot test this rule.

V-R must select a genuinely worse held winner through real search.
Give held cases a task feature that exposes a generalization failure.
Do not inject the selected winner or teach the stub which split a case occupies.

V-C deliberately uses a search correctness gain.
Pure cost-only search is a separate F06 regression because it is currently blocked.
Do not bypass the strict pre-gate to make a cheap-winner test pass.

Use complete telemetry such as baseline `input=120,cached=20,output=20`.
That yields 120 task tokens.
Winner `input=70,cached=20,output=10` yields 60.
Judge and simulator usage do not enter this charge.

Recompute expected deltas, uncertainty, tokens, and coverage independently from raw adapter logs.
Do not call the production `verdict`, `holdout_deltas`, or token helper as the test oracle.
Use enough nonzero variance to test strict boundary behavior.
Keep mathematical helper unit tests; the joined tests verify that real observations reach those formulas.

Run the full verdict set through representative real adapters and graders.
Require V-P, V-R, V-I, and V-U on every supported one-shot harness.
Use pairwise coverage for remaining grader/options combinations rather than a full Cartesian product.
Cover exact-json, judge, command, hybrid, journey, capture, and pillars explicitly.

### 8. Prove mutation and export fidelity

For every positive cell:

1. Snapshot every target path, type, byte digest, and executable mode before the run.
2. Complete approval, search, gate, and export.
3. Assert that the original target snapshot is unchanged.
4. Assert private report and patch permissions.
5. Assert `installed=False` and the exact gate verdict.
6. Apply all returned patches to a disposable copy.
7. Rebuild existing or generated wedge targets from the patched source.
8. Execute the patched helper on held public task inputs.
9. Compare behavior with the selected candidate's recorded task output.
10. Compare rebuilt archive content with the evaluated bundle.
11. Keep frozen paths and unrelated targets byte-identical.
12. Confirm that held requests and references do not appear in exported reports or patches.

Archive-byte equality uses the existing content-digest normalization where packaging embeds variable metadata.
Do not weaken equivalence to “the archive exists.”

W1 additionally rejects inconsistent edits to two names for one physical source.
N0 additionally checks collisions with frozen source and foreign bundle paths.
New-target tests verify deletion/addition hunks by applying the patch, not only `git apply --check`.
Export can produce an inconclusive candidate for inspection; that is not permission to install it.

### 9. Join journey execution to promotion

Use the existing Claude stream protocol, but run it through the CLI and real evaluator.[^journeys]

- The first turn reads the actual staged skill.
- The task asks a question determined by the input.
- The simulator answers from its hidden brief.
- The task produces a real output file or executes its helper.
- The command grader checks output files and the staged event trace.
- The judge scores the actual transcript.
- The search, holdout, and export path remain real.

Give task, reflection, simulator, and judge distinct sentinel inputs and logs.
Assert that each role sees only its declared data.
Inject a failed command gate and prove the judge never runs.
Inject missing usage on a later turn and preserve unknown totals.
Assert that unsupported Codex/command journeys stop without task inference.

M0 uses six scored held cases containing both task and journey kinds.
It must not satisfy the minimum with trigger or near-miss cases.
This row depends on the existing mixed-count repair.

### 10. Cover lifecycle and failure evidence

- No model inference occurs before case and budget approval.
- A bad approval hash starts no inference.
- A completed run is reused without another vendor task or search.
- Kill a CLI process at a controlled checkpoint boundary.
- Resume preserves approved identities, remaining calls, and elapsed deadline.
- Completed arm/case/repeat tuples are not scored again.
- Failed eligible attempts follow the separately approved retry/missingness policy.
- A changed contract, engine, source, harness, or site layer receives the current coded refusal.
- A constructor failure closes temporary login and transport state.
- Transient reflection failures persist sanitized attempt metadata.
- Concurrency never exceeds two active vendor calls.
- Slow-helper time accounting uses a test clock or bounded controlled waits, not minutes of sleep.
- Missing, malformed, zero-score, and activation-rejected results remain distinguishable.
- A planned family with no observations appears in skip reporting.
- Journey trace fixtures cannot replace or block runtime trace files.

Use external stub counters and synchronized fault markers for deterministic interruption.
Keep them outside the target and hidden from model roles.
They must not contain held expected answers.

## Decisions

These are proposed test-organization decisions, not new runtime policies.

- Keep the current edit modes and baseline/winner semantics.
- Test real CLI subprocesses, not only in-process `main`.
- Use actual task output and helper execution as the scoring input.
- Separate orchestration coverage from real OS isolation evidence.
- Reuse one scenario driver for source and installed-bundle entrypoints.
- Keep the positive matrix complete; reduce only redundant option combinations.
- Use the existing canonical `just build` gate.
- Keep known-defect regressions separate from existing-behavior tests until repair scope is approved.
- Do not hide a known-defect assertion behind permanent skip, xfail, or fabricated provider scores.

Alternatives considered:

| Option | Benefit | Cost | Disposition |
| --- | --- | --- | --- |
| Keep current component tests only | No new fixture maintenance | Does not prove promotion/export across adapter boundaries | Do nothing comparison; insufficient |
| Add a full model-driven suite | Exercises vendor behavior | Cost, nondeterminism, credentials, and timing hide orchestration failures | Deferred live validation |
| Add deterministic process stubs with real internals | Offline, repeatable, mutation-sensitive assertions | Protocol fixtures need maintenance | Recommended |
| Test only generic command adapter | Simpler process protocol | Misses Claude/Codex parsing and loading defects | Supplement, not replacement |

## Acceptance

- AC-1: WHEN the variant matrix runs THE SYSTEM SHALL cover both evaluation arms in every supported cell. (G-1)
- AC-2: WHEN a source CLI scenario runs THE SYSTEM SHALL retain real adapter, evaluator, GEPA, gate, and export calls. (G-2)
- AC-3: WHEN a task receives a changed candidate THE SYSTEM SHALL grade actual output or helper behavior. (G-2, G-4)
- AC-4: WHEN a verdict scenario completes THE SYSTEM SHALL assert its exact verdict, reasons, delta, SE, and coverage. (G-3)
- AC-5: WHEN a positive patch is applied THE SYSTEM SHALL reproduce the evaluated behavior without changing the original target. (G-4)
- AC-6: WHEN a wedge variant completes THE SYSTEM SHALL build and execute the patched source with preserved frozen ownership. (G-1, G-4)
- AC-7: WHEN a journey completes THE SYSTEM SHALL preserve role secrecy and score the real command gate and transcript. (G-1, G-2, G-5)
- AC-8: WHEN a lifecycle fault occurs THE SYSTEM SHALL expose the approved failure, budget, cleanup, and resume behavior. (G-5)
- AC-9: WHEN missing or invalid evidence reaches a gate THE SYSTEM SHALL satisfy the separately approved evidence policy. (G-3, G-5)
- AC-10: WHEN the installed bundle runs outside the checkout THE SYSTEM SHALL match the source scenario's declared result. (G-1, G-2)
- AC-11: WHEN implementation starts THE SYSTEM SHALL use the ownership, dependencies, and repair holds below. (G-6)
- AC-12: WHEN this planning task ends THE SYSTEM SHALL save the plan without implementing code. (G-7)

## Test Contracts

| Acceptance ID | Interface referent | Outermost stable seam | Expected failure | Mode | Interface version | Matrix rows |
| --- | --- | --- | --- | --- | --- | --- |
| AC-1 | run CLI | CLI process and public summary | A supported cell omits one evaluation arm | tracer | | |
| AC-2 | run/export CLI | CLI process and vendor protocol log | A patched provider factory returns success without adapter calls | tracer | | |
| AC-3 | declared grader | Real output through CLI | Broken executable earns the same score as working output | tracer | | |
| AC-4 | gate verdict | Public summary and raw observation log | A verdict differs while the test still passes | tracer | | |
| AC-5 | export CLI | Applied patch and executed target copy | Patch applies but cannot reproduce candidate behavior | tracer | | |
| AC-6 | wedge target | Rebuilt patched archive and executable | Frozen path changes or source/bundle behavior diverges | tracer | | |
| AC-7 | Claude journey | Streaming process through full run | Hidden data leaks or command failure still invokes judge | tracer | | |
| AC-8 | run resume | Killed/resumed CLI process | Repeated work resets calls or extends deadline | tracer | | |
| AC-9 | evidence eligibility | Faulted CLI run and public verdict | Incomplete evidence strengthens promotion | tracer | | |
| AC-10 | installed bundle | Bundle subprocess outside checkout | A source-only test passes while shipped behavior differs | tracer | | |
| AC-11 | implementation scope | File ownership and required project gate | A blocked repair silently enters test-only work | tracer | | |
| AC-12 | planning artifact | Saved repository document | Production or test code changes during planning | tracer | | |

## Interface sketches

```text
slice:            test-only e2e fixtures around existing run/search/gate/harness
spine step:       existing CLI run/export
public interface: unchanged run, export, doctor, self-test, and wrapper commands
private:          scenario driver, protocol stubs, independent oracle, snapshots
crust delta:      none
arrows:           tests -> existing public CLI; no new production dependency
```

Shape check skipped for this documentation-only task.
Implementation changes no public signature.
An explorer supplies the existing call-path evidence.
A later production repair receives its own scope and caller check.

## Risks

- Vendor stubs can drift from real protocols. Protocol contract tests must fail on unknown events and missing required fields.
- A fabricated stub score can make the suite circular. Scores must come from actual output and real grading.
- Synthetic isolation is not security evidence. Label it and retain a real OS lane.
- Offline builds can accidentally fetch dependencies. Provision local inputs and fail on attempted network access.
- Real GEPA can choose different parents. Use deterministic fixtures and avoid brittle exact reflection-call counts.
- Known defects prevent selected rows from passing today. Green component tests do not waive those holds.
- Process logs can expose hidden references. Keep private artifacts bounded and exclude them from exports.
- The complete matrix can slow the gate. Measure runtime before deduplication; retain every required cell.

## Open questions

These questions block relevant repairs, not writing the plan.

- [TBD] What repeat-level evidence policy should follow unavailable evaluations? This governs audit F01.
- [TBD] Should cost-only equal-correctness proposals reach validation? This governs audit F06.
- [TBD] Should held scheduling become interleaved or counterbalanced? This governs audit F12.
- [TBD] Should statistical units become families, or remain cases? Existing accepted policy remains case-based.
- [BLOCKED] Which existing guards may change? Each guard repair requires separate approval.
- [BLOCKED] Which CI host provides the required real OS sandbox profile? Do not infer it from stub readiness.
- [BLOCKED] Which production repairs join the failing regression tests? Do not silently expand test-only implementation.

## Quality gates

- `just build`: required after each implemented work package; no new failures, no hidden required skips, and bundle parity checked.
- `just ci`: verifies the integrated branch without autofixes.
- Existing focused component tests remain; the new suite runs through the existing skillz pytest collection.
- Planning verification uses `mold.pyz validate-spec --strict` and a fresh-context taste test.
- No project tests run for this documentation-only planning task.
- No new test command replaces the canonical gate.

## Curds

The work packages below are proposed sequencing, not authorization to implement.
Only the test-owned files are in scope.
Production repairs remain separate named dependencies.

| ID | Wave | Owner and files | Depends on | Observable completion |
| --- | --- | --- | --- | --- |
| C0 | 0 | Fixture/driver owner: e2e conftest.py, support.py, fixtures/, test_protocols.py | None | Real CLI approvals and all process protocols work; no internal provider replacement |
| C1 | 1 | Prose/verdict owner: test_prose.py, test_verdicts.py | C0 | P0 and representative exact verdicts pass on three harnesses |
| C2 | 1 | Helper owner: test_helpers.py | C0 | H0 executes changed scripts and applied exports |
| C3 | 2 | Existing-wedge owner: test_wedge.py | C2 | W0/W1 build, run, export, and preserve physical ownership |
| C4 | 2 | New-target owner: test_new_cli.py | C2 | N0 executes actual generated targets and protects frozen paths |
| C5 | 2 | Journey owner: test_journeys.py | C1, C2 | J0/J1/M0 join streaming execution to gate and export |
| C6 | 3 | Failure owner: test_failures.py | C1, C3, C4, C5 | Approved defect regressions, process-kill resume, and cleanup are asserted |
| C7 | 4 | Bundle/isolation owner: test_bundle.py and profile-specific test files | C1-C6 | All required cells pass both entrypoints; real OS lane reports no required skips |

C0 owns shared fixture edits.
Later packages request fixture extensions through that owner.
Concurrent writers use separate worktrees pinned to the grounded revision.
Integrate before running the project-wide gate.

### Repair dependencies

Audit IDs refer to the saved experiment assessment.

| Audit finding | Test witness | Current implementation consequence | Approval boundary |
| --- | --- | --- | --- |
| F01 failed repeats | Partial-repeat inferior winner must not gain certainty | Cannot assert a new eligibility rule until chosen | Statistical/evidence policy |
| F02 partial journey usage | Missing second-turn counts remain unknown | Joined unknown-usage regression needs repair | Existing telemetry contract; guard approval if validation changes |
| F03 frozen collisions | New target cannot replace frozen paths | N0 negative collision assertion needs repair | Explicit guard approval |
| F04 source aliases | Evaluated helper and patched bundle share one source | W1 divergent-alias assertion needs repair | Source ownership repair scope |
| F05 stale means | Repeated candidate scores select the actual best snapshot | Ranking regression needs repair | Snapshot policy selection |
| F06 pure cost-only search | Equal correctness plus lower cost reaches validation | Search currently excludes the candidate | Search contract approval |
| F07 first inventory | Persistent foreign non-skill entries fail actual calls | First-init rejection needs repair | Explicit guard approval |
| F08 helper time | Gate reserve includes declared fixture work | Slow-helper reserve assertion needs repair | Planner repair scope |
| F09 mixed holdout | Three task plus three journey held cases are eligible | M0 preparation currently undercounts | Existing eligibility repair scope |
| F10 checkpoint size | Completed large journey remains reloadable/exportable | Aggregate transcript regression needs repair | Storage repair scope; preserve read guard |
| F11 reflection failure | Skipped proposal has a sanitized failure record | Failure-persistence assertion needs repair | Telemetry repair scope |
| F12 arm order | Drift-null result does not look like candidate gain | Existing schedule is arm-grouped | Scheduling approval |
| F13-F15 assertion gaps | Runtime, parent ancestry, and expected errors are asserted | Test-only improvements | No runtime policy change |
| F16 skipped families | Zero-observation family appears in reporting | Reporting assertion needs repair | Existing report contract |
| F17 constructor cleanup | Rebuilder failure closes provider and fake login state | Cleanup assertion needs repair | Lifecycle repair scope |
| F18 trace collision | trace fixture is rejected before task inference | Validation currently allows it | Explicit guard approval |

Do not introduce permanent xfails for these rows.
A defect-regression work package includes its approved repair or remains unselected.
Every selected package must finish with a green canonical gate.
The complete plan is not done while required cells remain blocked, skipped, or synthetic at a claimed real boundary.

## References

[^audit]: `.cheese/age/skillz-experiments-audit-body.md` — prior flow assessment, eighteen verified findings, and integration contract at revision `6749d9541a4d13a62422311bb572700ae00e1865`.
[^modes]: `docs/agents/decisions/skillz-autoimprove.md:7-10,77-96`; `packages/skillz/src/skillz_experiments/search/_engine.py:13,31-70`.
[^factory]: `packages/skillz/tests/skillz_experiments/test_cli_run.py:84-155`.
[^autouse]: `packages/skillz/tests/skillz_experiments/conftest.py:31-115`.
[^activation]: `packages/skillz/src/skillz_experiments/_evaluation.py:14-17,35-45`; `packages/skillz/src/skillz_experiments/_evaluator.py:179-200`; `packages/skillz/src/skillz_experiments/harness/_command.py:226-260`.
[^protocols]: `packages/skillz/tests/skillz_experiments/fixtures/fake_claude.py:251-388`; `packages/skillz/src/skillz_experiments/harness/_codex.py:25-68,163-249`; `packages/skillz/src/skillz_experiments/harness/_discovery.py:17-47,73-92`; `packages/skillz/src/skillz_experiments/_protocol.py:9-15`.
[^journeys]: `packages/skillz/tests/skillz_experiments/test_journey_fixtures.py:128-175,267-302,361-383`.

# Skillz experiments end-to-end prototype

This prototype joins public CLI execution to real grading, GEPA, holdout, export, and patch replay.
It does not complete every requirement in the [original plan](skillz-experiments-e2e-plan.md).
Production behavior, guards, and statistical policies remain unchanged by this PR.

## Implementation scope

The suite lives in `packages/skillz/tests/skillz_experiments/e2e/`.
It uses deterministic vendor processes, not an injected provider or fabricated winner.
Tasks read staged files and execute actual helpers.
Graders score actual task output.
Reflection receives the current candidate and training feedback.

The command, Claude, and Codex adapters remain real.
The suite also retains real case approval, budget approval, GEPA 0.1.4, validation, three-repeat holdout, and export.
Bundled scenarios copy the shipped archive outside the checkout and run Python with `-I -S`.

| Variant | Harnesses | Entrypoints | Status |
| --- | --- | --- | --- |
| P0: prose | Command, Claude, Codex | Source, bundle | Implemented |
| H0: ordinary helper | Command, Claude, Codex | Source, bundle | Implemented |
| W0: external wedge source | Command, Claude, Codex | Source, bundle | Implemented |
| W1: skill-local wedge source | None | None | Source-alias repair remains open |
| N0: generated target | Command, Claude, Codex | Source, bundle | Implemented |
| J0: prose journey | Claude | Source, bundle | Implemented |
| J1: helper journey | Claude | Source, bundle | Implemented |
| M0: mixed tasks and journeys | Claude | Source, bundle | Implemented with a larger manifest |

This gives 30 positive runs from the original 36-run matrix.
Four additional Gemini-wrapper runs cover P0 and H0 through both entrypoints.
A malformed Gemini response fails with the exact error and charged calls.
The six missing W1 runs remain explicit.
The mixed manifest has enough task cases to satisfy the current kind-specific minimum.
It does not repair the minimal mixed-count defect.

## Mutation and lifecycle evidence

Target tests apply every declared patch to a disposable copy.
They rebuild and execute exported sources.
The rebuilt archive content digest matches the archive executed during winner evaluation.
Full path, type, mode, and content snapshots prove that original wedge targets remain unchanged.
Generated-target tests preserve unrelated frozen targets.

Journey tests join streaming tasks, simulator replies, command gates, transcript judging, holdout, and export.
A failed command gate prevents judge calls.
Unsupported command and Codex journeys refuse before journey inference.
The helper journey executes its applied export.

Lifecycle tests refuse inference before approvals.
They kill a process group after a durable holdout checkpoint.
Resume preserves identities, deadline origin, call accounting, and completed observations.
A completed run makes no new vendor inference calls.

## Verdict evidence

The joined scenarios cover these current results:

- Ordinary promotion has delta 1, SE 0, and complete paired observations.
- A changed winner can lose every held case and receive ordinary rejection.
- A protected family can force rejection despite positive overall delta.
- A changed winner can tie on holdout and remain inconclusive.
- Unknown task usage blocks token-dependent promotion on all three one-shot harnesses.
- Excess task-token cost rejects an otherwise improving winner.
- Cheaper promotion needs explicit opt-in and sufficient token saving.
- Gain and lower-bound floors block positive evidence.
- Nonzero case-level uncertainty can prevent promotion.
- Invalid helper and leakage proposals retain the seed without unauthorized candidate evaluation.

Token scenarios recompute scores, uncertainty, and token change from raw vendor answer and usage logs.
They do not call production verdict or token helpers as their oracle.
The full fractional family, missing-case coverage, and repeat-variance matrices remain open.

## RRSI-related paths

The prototype tests implemented RRSI-inspired controls, not a full RRSI algorithm.
It checks complete validation, reflection feedback, sealed holdout, leakage rejection, helper validation, and compatibility refusal.
It also tests current advisory edit-budget annealing and run-status fields.

The initial plan grounds revision `6749d9541a4d13a62422311bb572700ae00e1865`.
Implementation fast-forwards to `ebc64456c3e9dc8e866027e4b537807856ea45d5` before final verification.
That upstream revision adds advisory annealing and acceptance-test traceability.
The original plan's deferred annealing statement is therefore historical, not current behavior.

Edit-history conditioning, stall exploration, a semantic critic, and component pruning remain outside current implemented coverage.
A complementary-specialist Pareto ancestry witness also remains open.
The attempted fixture does not demonstrate a non-greedy parent, so no weak test replaces it.

## Isolation boundary

All new tests use an explicitly synthetic OS-isolation profile.
The substitute executes actual helper and grader commands.
Only the isolation probe receives a synthetic success response.
This proves orchestration, not host containment or network isolation.

Namespace creation fails inside the harness sandbox but succeeds with approved execution outside it.
No test claims that a real OS sandbox passes.
A required real-isolation CI profile remains unimplemented.

## Remaining plan requirements

- W1 needs one authoritative component for each physical source and reproducible export patches.
- Failed-repeat eligibility, temporal arm scheduling, and pure cost-only search need separate policy decisions.
- Later-turn missing journey usage, frozen target collisions, first-call foreign inventory, and trace-path collisions need selected repairs.
- Aggregate transcript storage, constructor cleanup, helper-time reservation, and reflection-failure reporting remain separate repair dependencies.
- Planned unscored-family reporting remains open.
- The complete grader/options matrix and remaining exact-variance/family/coverage scenarios need further joined tests.
- Protocol-negative coverage includes malformed Gemini output; broader independent stub-contract tests remain incomplete.
- Stub quality does not establish real-model improvement or a universal false-promotion bound.

No required row is hidden behind a permanent skip or xfail.
The existing component suites remain in the canonical gate.

## Verification

The prototype contains 69 collected E2E tests.
`UV_CACHE_DIR=/tmp/skillz-e2e-uv-cache just build` passes on the integrated prototype.
The gate includes acceptance traces, type checks, existing suites, E2E tests, and shipped-bundle parity.
The PR body records the final non-autofix CI gate and fresh-context review results.

The tests use private temporary homes and deterministic fake credentials.
Child environments remove ambient Claude and Codex authentication-directory overrides.
Sentinel-directory regressions verify that private fixture credentials remain authoritative.
Target tests consume public wedge build output and retain independent vendor archive witnesses.
Local socket probes require approved execution outside this harness's restricted process sandbox.
The native namespace prerequisite passes, but the required real-isolation CI profile remains unimplemented.

## Ownership and assumptions

The parent owns scope, integration, final gates, and publication.
Coders own separate fixture, target, journey, RRSI, verdict, and lifecycle files.
Shared fixture ownership transfers before corrective edits.
This prototype uses one checkout after an expensive dependency bootstrap.
The parent preserves unrelated files and imports existing upstream commits without rewriting history.

The user authorizes assumptions, prototype implementation, and a PR.
That authorization does not select a new guard or statistical policy.
The PR uses the plan's single-PR landing shape.
[Acceptance traceability](../agents/criteria/skillz-experiments-end-to-end.md) records the selected prototype criteria.

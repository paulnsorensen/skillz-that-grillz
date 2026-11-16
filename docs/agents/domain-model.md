# Domain model

This page defines the terms for experiment planning and case proof.
It records accepted decisions. A local implementation does not imply release.[^free-plan-design]

## Experiment planning

**Free plan** — A preview of diagnostics, edit choices, editable files, assignments, and available estimates without model calls.[^free-plan-design]
_Avoid_: dry run
_Code_: `packages/skillz/src/skillz_experiments/run/_preview.py:26`

A free plan does not grant approval or certify execution readiness.
Existing diagnostic probes still execute trusted local gate code.

**Planning-only cap** — A requested task call ceiling used only to calculate a preview.[^free-plan-design]
_Avoid_: run approval
_Code_: `packages/skillz/src/skillz_experiments/run/_preview.py:35`

**Offline sizing configuration** — Structurally validated harness settings read without executable lookup or harness invocation.[^free-plan-design]
_Code_: `packages/skillz/src/skillz_experiments/harness/_harness.py:60`

Offline sizing includes static adapter capability checks.
Runtime configuration still performs execution checks.

**Seed projection** — Usable seed information shown before a valid draft supports run-matched sizing.[^free-plan-design]
_Code_: `packages/skillz/src/skillz_experiments/_seed.py:130`

A seed projection is not a frozen case set.
It cannot alone support a numeric estimate.

**Draft assignment** — The family and split assigned from a parsed case draft for free planning.[^free-plan-design]
_Code_: `packages/skillz/src/skillz_experiments/_validate.py:207`

A draft assignment does not grant case approval.
An invalid draft can still show assignments but cannot support a numeric estimate.

## Case proof

**Case proof** is an optional pair of pass and fail output references on an authored case.
The validator checks the selected gate with actual case inputs.
It proves neither model solvability nor a judge verdict.

**Diagnostic proof** is proof metadata and files checked without changing case approval or frozen run data.
Validation checks malformed metadata on every loaded case.
Normal run loading ignores it while retaining whole-input bounds.
Only selected cases receive proof-file and command checks.

**Proof origin** is the internal author file and original JSON Pointer for the authored proof.
It adds no seed JSON field, prompt data, frozen content, or case identity.

**Bounded local probe** is a trusted local grader process with a clean environment, limits, and process-group cleanup.
The caller's PATH selects local tool versions.
These controls are not OS isolation; host-file and network access remain possible.

## Related decisions

See [experiment decisions](./decisions/skillz-autoimprove.md) and [architecture gate decisions](./decisions/architecture-gates.md).

[^free-plan-design]: User decisions F-1, F-2, F-3, F-16, F-17, and F-18, Mold session 2026-11-11. Reviewed original contract: `specs/skillz-free-plan.md` in the durable project corpus, SHA256 `ca378af4abc15df71fd305946848477730565a975ba59513b8865a0cc11de645`.

_Source: Approved free-plan decisions and the shared seed projection helper · Updated: 2026-11-11 · Supersedes: Seed projection code citation only; its definition stays unchanged._

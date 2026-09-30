# optimize_anything: A Universal API for Optimizing any Text Parameter

The GEPA team's API article, published 2026-02-18 and verified 2026-09-28, describes optimization of scored text artifacts.
Canonical source: [optimize_anything: A Universal API for Optimizing any Text Parameter](https://gepa-ai.github.io/gepa/blog/2026/02/18/introducing-optimize-anything).

## Candidate and evaluator contract

GEPA's `optimize_anything` accepts a string, named text components, or an empty seed.
The application supplies an evaluator, objective, configuration, and optional training and validation datasets.
An evaluator returns a score or a score with diagnostic side information.
The application can also emit diagnostics through `oa.log()`.
Code is an eligible text artifact; the application must execute and grade it.[^1]

Named components can represent a prompt, reference files, and CLI source.
That representation does not define permission to edit those files.
The optimizer does not supply the application's sandbox, immutable tests, or release approval.

## Components are not objectives

GEPA components are candidate text fields.
Objectives are measured outcomes.
The gskill guide uses side information's `scores` for multi-objective tracking.
The [Creating Adapters guide](./gepa-adapters.md) exposes `EvaluationBatch.objective_scores` and component-specific reflection records.[^2][^3]
A bare two-number return must not be mistaken for two objectives: the documented tuple means score and side information.

## Version and quality constraints

[GEPA release notes](./gepa-releases.md) for v0.1.4 add `batch_evaluator` and `reflection_strategy`.
They remove `EngineConfig.num_parallel_proposals`.
Pin and test the selected version before integration; this article is not an exhaustive current signature.[^4]

GEPA's [Frequently Asked Questions](./gepa-faq.md) states that brevity needs an explicit objective.
It also describes example memorization and output-contract drift.
Thus a lower token count is not evidence of better task performance.[^5]
The [feature proposal](../ideas/session-driven-skill-optimization.md) now has a bounded [local implementation](../../../lib/src/skillz_experiments/_workflow.py).
[Bundled self-tests](../../../lib/tests/skillz_experiments/test_bundle.py) exercise real GEPA, fixed evaluation, and paired holdouts.
These tests do not establish statistical improvement or validate this source's examples.

[^1]: [API article](https://gepa-ai.github.io/gepa/blog/2026/02/18/introducing-optimize-anything), fetched 2026-09-28.
[^2]: [gskill source note](./gepa-gskill.md), based on the official guide fetched 2026-09-28.
[^3]: [Creating Adapters](https://gepa-ai.github.io/gepa/guides/adapters/), fetched 2026-09-28.
[^4]: [GEPA release notes](https://github.com/gepa-ai/gepa/releases), fetched 2026-09-28.
[^5]: [Frequently Asked Questions](https://gepa-ai.github.io/gepa/guides/faq), fetched 2026-09-28.

_Source: GEPA API article, guides, FAQ, and release notes · Updated: 2026-09-28_

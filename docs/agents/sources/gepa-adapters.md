# Creating Adapters

The GEPA team's adapter guide, last verified 2026-09-28, supports the following finding.
GEPA adapters separate candidate evaluation from component-specific reflection data.
Canonical source: [Creating Adapters](https://gepa-ai.github.io/gepa/guides/adapters/).

## Supported finding and limits

The adapter protocol provides evaluate and make_reflective_dataset. Candidates contain named text components. EvaluationBatch.objective_scores contains metric values. Components and objectives are different concepts. An integration should prove that its pinned GEPA interface records both correctly.[^1]

## Skillz relevance

This source informs [the GEPA research synthesis](../research/gepa-session-optimization.md).
The [feature proposal](../ideas/session-driven-skill-optimization.md) now has a bounded [local implementation](../../../lib/src/skillz_experiments/_workflow.py).
[Bundled self-tests](../../../lib/tests/skillz_experiments/test_bundle.py) exercise real GEPA, fixed evaluation, and paired holdouts.
These tests do not establish statistical improvement or validate this source's examples.

[^1]: [Creating Adapters](https://gepa-ai.github.io/gepa/guides/adapters/), fetched 2026-09-28. Documentation inspected; no GEPA optimization run executed.

_Source: https://gepa-ai.github.io/gepa/guides/adapters/ · Updated: 2026-09-28_

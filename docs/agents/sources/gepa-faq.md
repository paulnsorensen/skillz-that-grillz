# Frequently Asked Questions

Frequently Asked Questions is the GEPA team's guide to common optimization questions, last checked for search budgets on 2026-11-23.
The FAQ answers questions about prompt length, overfitting, and output contracts.
It states that GEPA requires an explicit brevity objective and can overfit examples or change output formats.
Canonical source: [Frequently Asked Questions](https://gepa-ai.github.io/gepa/guides/faq).

## Supported finding and limits

The FAQ discusses examples copied into prompts and changes to required output formats. Validation and preservation constraints address these risks, but prose constraints do not enforce an execution boundary. This distinction motivates the immutable evaluator in the [local implementation](../../../packages/skillz/src/skillz_experiments/evaluation/_evaluator.py) and the held-out tests.[^1]

## Search budget and example count

The FAQ suggests 15–30 metric calls per validation example to explore up to 15 new candidates.[^budget]
It recommends 30–300 examples, but reports gains with as few as three. These are planning guidelines, not minimum requirements.[^budget]
A metric call scores an example. It need not cost one model call.
See [the review-skill run](../research/gepa-session-optimization.md#search-budget-and-baseline-value-in-a-review-skill) for a measured example.

## Skillz relevance

This source informs [the GEPA research synthesis](../research/gepa-session-optimization.md).
The [feature proposal](../ideas/session-driven-skill-optimization.md) now has a bounded [local implementation](../../../packages/skillz/src/skillz_experiments/run/_run.py).
[Bundled self-tests](../../../packages/skillz/tests/skillz_experiments/test_bundle.py) exercise real GEPA, fixed evaluation, and paired holdouts.
These tests do not establish statistical improvement or validate this source's examples.

[^1]: [Frequently Asked Questions](https://gepa-ai.github.io/gepa/guides/faq), fetched 2026-09-28. Documentation inspected; no GEPA optimization run executed.

[^budget]: [GEPA FAQ: budget and small datasets](https://gepa.dev/guides/faq/#what-does-a-single-gepa-iteration-cost), checked 2026-11-23.

_Source: GEPA FAQ and the attached “GEPA usage versus the skillz search” report · Updated: 2026-11-23 · Supersedes: none_

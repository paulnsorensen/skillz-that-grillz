# GitHub - itsmostafa/gskill

itsmostafa's repository README, last verified 2026-09-28, supports the following finding.
The standalone gskill implementation seeds instructions from repository analysis and exports a Claude skill file.
Canonical source: [GitHub - itsmostafa/gskill](https://github.com/itsmostafa/gskill).

## Supported finding and limits

The README loads SWE-smith tasks and uses mini-SWE-agent in Docker to grade FAIL_TO_PASS tests. Its run command requires available SWE-smith instances. These facts describe this repository, not GEPA's official module.[^1]

## Skillz relevance

This source informs [the GEPA research synthesis](../research/gepa-session-optimization.md).
The [feature proposal](../ideas/session-driven-skill-optimization.md) now has a bounded [local implementation](../../../lib/src/skillz_experiments/_workflow.py).
[Bundled self-tests](../../../lib/tests/skillz_experiments/test_bundle.py) exercise real GEPA, fixed evaluation, and paired holdouts.
These tests do not establish statistical improvement or validate this source's examples.

[^1]: [GitHub - itsmostafa/gskill](https://github.com/itsmostafa/gskill), fetched 2026-09-28. Documentation inspected; no GEPA optimization run executed.

_Source: https://github.com/itsmostafa/gskill · Updated: 2026-09-28_

# gskill: Learning Repository-Specific Skills

The GEPA team's official guide, last verified 2026-09-28, describes test-driven learning of repository-specific coding instructions.
Canonical source: [gskill: Learning Repository-Specific Skills](https://gepa-ai.github.io/gepa/guides/gskill).

## Official module and separate implementation

The official gskill module belongs to `gepa-ai/gepa`.
Its documented entry point is `python -m gepa.gskill.train_optimize_anything`.
The guide starts with empty instructions and exports `best_skills.txt` for system-prompt injection.[^1]

The separate [itsmostafa/gskill repository](./itsmostafa-gskill.md) generates a static-analysis seed.
It exports `.claude/skills/{repo}/SKILL.md`.
Its README limits execution to repositories with available SWE-smith instances.[^2]
Do not combine these implementations' commands, defaults, dependencies, or output paths.

## Evaluation mechanism

The official gskill guide uses SWE-smith tasks, Docker environments, agent runs, and executable test outcomes.
Its evaluator accepts `candidate` and `example`, returning a score and diagnostic information.
Feedback includes traces, patches, and test output.
The named `example` parameter matters because GEPA forwards task data by keyword.
The guide separates training, validation, and final testing.[^1]

## Relevance and limits

The official gskill recipe demonstrates the optimization loop, not arbitrary session replay or packaged CLI mutation.
Its system-prompt deployment does not test skill discovery or reference loading.
A skillz integration needs those checks separately.

The September 26 easy-cheese digest used “gskill” for the standalone repository while citing the official guide.
This page supersedes that combined identity description; it does not invalidate the shared test-feedback approach.
See [the research synthesis](../research/gepa-session-optimization.md) for provenance and remaining gaps.

[^1]: [Official guide](https://gepa-ai.github.io/gepa/guides/gskill) and [upstream Markdown](https://raw.githubusercontent.com/gepa-ai/gepa/main/docs/docs/guides/gskill.md), fetched 2026-09-28. Documentation inspected; training command not executed.
[^2]: [Standalone implementation](https://github.com/itsmostafa/gskill), README fetched 2026-09-28.

_Source: official GEPA guide and standalone gskill README · Updated: 2026-09-28 · Supersedes: combined gskill identity in the 2026-09-26 easy-cheese digest_

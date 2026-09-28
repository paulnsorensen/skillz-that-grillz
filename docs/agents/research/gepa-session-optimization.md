# GEPA and session analytics research for skillz

GEPA can search skill text and CLI source, but session analytics cannot establish whether a candidate improves task outcomes.
The missing connection is a verified dataset and evaluator, not another prompt-rewrite step.[^gepa][^analytics]
This report gathers prior easy-cheese and vaudeville work and checks current primary sources.
It does not claim a measured improvement or approve an implementation.

## Findings and evidence

The GEPA research supports these claims as of 2026-09-28.

| Claim | Evidence | Source type | Confidence | Caveat |
| --- | --- | --- | --- | --- |
| GEPA can optimize named text artifacts, including code. | [Universal API article](../sources/gepa-optimize-anything.md) | Upstream documentation | certain | The application supplies execution, grading, and isolation. |
| Official gskill and itsmostafa/gskill differ. | [Official guide](../sources/gepa-gskill.md), [standalone repository](../sources/itsmostafa-gskill.md) | Upstream documentation | certain | Do not mix entry points, seed policy, or output paths. |
| Session analytics is proposed inside skillz. | [PR #105 snapshot](../sources/skillz-session-analytics-pr-105.md) | GitHub PR and pinned schema | certain | Open PR; not the checked-out implementation. |
| Easy-cheese has an experimental GEPA agent loop. | PR #722 and pinned `agent_lab.py`.[^prototype] | GitHub PR and code | certain | Synthetic fixtures, prompt append, no demonstrated quality gain. |
| Prior easy-cheese decisions include CLI mutation. | Whole-skill candidate ADR, described below.[^prior] | Local repository knowledge | certain | Accepted there; not automatically accepted for skillz. |
| Vaudeville supplies evaluation-design precedent. | Inline-case and calibration ADRs, described below.[^vaudeville] | Local repository knowledge | certain | Not evidence of a working GEPA integration. |
| A session-derived optimizer could personalize skills. | [Proposed data and evaluation flow](../ideas/session-driven-skill-optimization.md) | Design inference | speculating | Replay quality, useful task volume, and gains remain untested. |

The GEPA team's “optimize_anything: A Universal API for Optimizing any Text Parameter” establishes the candidate/evaluator mechanism.
Its [Creating Adapters guide](../sources/gepa-adapters.md) separates text components from measured objectives.
The [Frequently Asked Questions](../sources/gepa-faq.md) documents overfitting, output drift, and explicit brevity objectives.
The [release notes](../sources/gepa-releases.md) show interface changes that require a pinned integration.

## Recovered easy-cheese work

The easy-cheese research digest dated 2026-09-26 describes GEPA and the standalone gskill pipeline.
Its landscape page identifies real-task grading, installed-skill evaluation, and session analytics as observational evidence.[^prior]

Four local ADRs refine that landscape:

| Record | Recorded direction | Transfer limit |
| --- | --- | --- |
| cook loop ADR-001 | Easy-cheese owns optimization; tilth supplies benchmark measurements. | Skill-loading injection requires a proof run. |
| cook loop ADR-002 | Candidate includes prompt, references, and skill-owned CLI source. Shared code and contracts stay frozen. | The target is cook, not generic skillz. |
| cook loop ADR-003 | Compare accuracy and full token usage; do not hide accuracy loss inside a cost ratio. | Its wording confuses non-domination with a stricter promotion rule. |
| cook loop ADR-004 | Search cheaply, then evaluate on deployment models. | Historical model choices are not requirements for skillz. |

These are recovered decisions, not implementation verification.
The landscape says no decisions are locked, while the narrower ADRs mark their decisions accepted.
Prefer the ADRs for that cook design.
Do not infer that its implementation exists.[^prior]

PR #722 is open at `0ec60bbf6a12e1c0d0a9097fdd6ac8659866330a`.
Its agent loop passes candidates with `--append-system-prompt`, not normal skill discovery.
It disables settings and skips permission prompts inside its disposable workspace.
A disposable checkout alone is not a security sandbox.[^prototype]

The pinned optimizer separates train and validation data from holdout tasks.
However, export checks improvement on combined train and validation data; it does not automatically run the sealed holdout.
Therefore, reuse requires an independent promotion gate.[^prototype]

## Recovered vaudeville work

The vaudeville native-library study finds evaluation building blocks but does not verify a GEPA integration.
Its accepted evaluation ADR keeps labeled `{text, outcome}` cases inline in rule YAML.
It constructs a Pydantic Evals Dataset at runtime.
A separate calibration ADR warns against treating self-reported confidence as calibrated evidence.[^vaudeville]

These records support separating the task dataset, grader, and optimizer.
The typed-rule-core spec explicitly defers tuning-loop redesign.
No standalone gskill study was found in the checked vaudeville Markdown scope.
This is a search limit, not proof that the earlier discussion never happened.

## Corrections to preserve

The research corrects three material ambiguities without rewriting the source repositories.

1. Official `gepa.gskill` and `itsmostafa/gskill` are separate implementations.
2. Non-dominated does not mean “no worse on both axes.” An accuracy/token trade-off can remain non-dominated.
3. A prototype export after validation improvement is not a sealed-holdout promotion.

A stricter proposed promotion rule requires no unacceptable correctness regression, plus an agreed improvement.
Statistical tolerance, deployment models, and cost preferences remain user decisions.
A single repetition does not establish model robustness.

## Repository fit

The checked-out skillz supports add, improve, audit, and self-update.
Its `optimize` alias currently means `improve`; it is not a GEPA command.[^skillz]
A future experiment must not silently replace that behavior.

The existing [CLI design research](../agent-friendly-cli-design.md), [fromargs decisions](../decisions/fromargs-cli-library.md), and [wedge decisions](../decisions/wedge-skill-packaging.md) supply local conventions.
A generic optimizer should accept a target's declared build and test contract.
It must not assume every consumer uses this repository's packaging layout.

## Research method and limits

The research uses Hallouminate for registered wiki retrieval, Tilth for local records, GitHub for PR state, and primary upstream web documentation.
The active Hallouminate configuration does not register the easy-cheese or vaudeville wikis.
Local Markdown retrieval supplies that missing coverage; it does not claim a cross-corpus Hallouminate result.

The external researcher uses three searches and nine retrieved URLs.
The capture budget extension is recorded retrospectively.
The preferred researcher model is unavailable; a fresh-context fallback researcher supplies the evidence.
Raw captures remain outside Git; this curated report is the durable repository artifact.

No private session database or transcript is scanned.
No candidate is executed, no paid evaluation runs, and no improvement rate is measured.
The repository build verifies this documentation change, not GEPA effectiveness.

## Open questions and next step

The research is certain about documented mechanisms and inspected repository state.
Personalization benefits remain speculating because no session-derived benchmark runs.

- Which skill, CLI, and deployment harness should form the first experiment?
- Should the first release expose prompt-only search before enabling guarded CLI mutation?
- Which success labels can the user approve without trusting model self-report?
- What accuracy tolerance, monetary cap, and runtime cap should govern promotion?
- Should skillz own the reusable loop or call an extracted easy-cheese runner?

Next, turn [the feature proposal](../ideas/session-driven-skill-optimization.md) into a bounded proof experiment.
Do not interpret this research PR as implementation approval.

[^gepa]: [GEPA source note](../sources/gepa-optimize-anything.md), primary documentation verified 2026-09-28.
[^analytics]: [PR #105 source note](../sources/skillz-session-analytics-pr-105.md), pinned schema and coverage verified 2026-09-28.
[^prototype]: [Easy-cheese PR #722](https://github.com/paulnsorensen/easy-cheese/pull/722), metadata and body read 2026-09-28; pinned `scripts/agent_lab.py:300-315,552-646` at `0ec60bbf6a12e1c0d0a9097fdd6ac8659866330a`, read through GitHub Contents API.
[^prior]: Local easy-cheese records read 2026-09-28: `.cheese/research/gskill-gepa/gskill-gepa.md:1-128`; `.hallouminate/wiki/skill-optimization-landscape.md:10-135`; `.hallouminate/wiki/adr/cook-skill-optimization-loop-001.md:5-37`, `-002.md:5-37`, `-003.md:5-34`, and `-004.md:5-32`. This page preserves the relevant findings without requiring those external files.
[^vaudeville]: Local vaudeville records read 2026-09-28: `.cheese/research/pydantic-ai-native/pydantic-ai-native.md:9-36`; `docs/adr/pydantic-evals-calibration-001.md:7-26`; `docs/adr/pydantic-evals-calibration-002.md:7-29`; `docs/specs/typed-rule-core.md:20-32`.
[^skillz]: `skills/skillz/SKILL.md:25-39` and `skills/skillz/references/analytics-ceremony.md:3-9` at `b068dacbae5b8afd8c3abe2f531adc5193a99e25`.

_Source: recovered local research, pinned PR evidence, and primary GEPA documentation · Updated: 2026-09-28 · Supersedes: combined gskill identity and automatic-holdout implications in prior summaries_

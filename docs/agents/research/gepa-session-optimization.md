# GEPA and session analytics research for skillz

GEPA can search skill text and CLI source, but session analytics cannot establish whether a candidate improves task outcomes.
The missing connection is a verified dataset and evaluator, not another prompt-rewrite step.[^gepa][^analytics]
This report gathers prior easy-cheese and vaudeville work and checks current primary sources.
The initial research does not claim a measured improvement.
The user subsequently approves implementation and the bounded self-test described below.

## Findings and evidence

The GEPA research supports these claims as of 2026-09-28.

| Claim | Evidence | Source type | Confidence | Caveat |
| --- | --- | --- | --- | --- |
| GEPA can optimize named text artifacts, including code. | [Universal API article](../sources/gepa-optimize-anything.md) | Upstream documentation | certain | The application supplies execution, grading, and isolation. |
| Official gskill and itsmostafa/gskill differ. | [Official guide](../sources/gepa-gskill.md), [standalone repository](../sources/itsmostafa-gskill.md) | Upstream documentation | certain | Do not mix entry points, seed policy, or output paths. |
| PR #105 proposes session analytics inside skillz. | [PR #105 snapshot](../sources/skillz-session-analytics-pr-105.md) | GitHub PR and pinned schema | certain | Open PR; not the checked-out implementation. |
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
The search finds no standalone gskill study in the checked vaudeville Markdown scope.
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

At that revision, the checked-out skillz supports add, improve, audit, and self-update.
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
This report records the capture budget extension retrospectively.
The preferred researcher model is unavailable. A fresh-context fallback researcher supplies the evidence.
Raw captures remain outside Git. This curated report is the durable repository artifact.

The research scans no private session database or transcript.
The initial research phase executes no candidate and measures no improvement rate.
A repository build alone does not establish GEPA effectiveness.

## Accepted first experiment

The user approves skillz as its own first optimization target on Codex.
The approved live profile uses the existing ChatGPT login and an explicit deployment model.
One global budget permits at most 20 Codex invocations, including reflection, within 20 minutes.
An isolation failure stops execution rather than enabling an unsafe fallback.
The first comparison uses public fixtures, not private session transcripts.

The selected implementation uses optional GEPA 0.1.4 and its installed `GEPAConfig`, `EngineConfig`, and `ReflectionConfig` interface.
A custom candidate proposer supplies the Codex reflection call instead of GEPA's default model client.[^runtime]

OpenAI's [Codex 0.154 skill discovery and isolation](../sources/codex-skill-discovery-isolation.md) evidence changes the isolation design.
The `--ignore-user-config` option does not disable user skill discovery.
Isolation requires separate host-discovery and generated-command checks.
A local model-free check passes with isolated home directories and a restricted Codex command profile.
That check does not establish live optimization success.[^runtime]

The [feature design](../ideas/session-driven-skill-optimization.md) distinguishes this bounded experiment from broader personalization goals.
Session-derived benefit, repeated-run robustness, monetary pricing, and generic deployment adapters remain unverified.

## First live self-test result

The authenticated Codex self-test completes on 2026-09-28 with public repository fixtures.
It evaluates three distinct candidate packages through GEPA's two search modes.
Both search modes retain the original candidate (the seed) after validation.
The result does not demonstrate improvement or real-session personalization.[^selftest]

| Measurement | Observed result |
| --- | --- |
| Model and harness | `gpt-6-astra`, Codex CLI 0.154.0. |
| Charged invocations | 19 total: one failed startup, then 18 successful-run invocations. |
| Reflection invocations | Two, included in the total. |
| Task evaluations | 16 passed out of 16. |
| Paired holdout evaluations | Six passed out of six; all locked arms retain the original. |
| Budget check | The run finishes within 920 seconds of the original start. The limit is 1,200 seconds. |
| Export | Both private exports succeed; no candidate installs. |
| Improvement | Inconclusive bounded smoke test; the original is retained. |

The first startup attempt reveals that `codex exec` does not accept sandbox's `-P` flag.
The corrected runner uses `default_permissions="skillz"` and validates exec arguments without calling a model.
The retry preserves the original overall deadline and reduces its allowance to 19 invocations.

GEPA evaluation caching remains disabled so the application records independent validation executions.
The runner suppresses GEPA's raw proposal output because proposed text can contain private training content.
Exact JSON grading distinguishes booleans from numbers.
These corrections have regression coverage in the canonical build.[^runtime]

[^selftest]: Authenticated local run `skillz-live-20260928-retry`, 2026-09-28; aggregate evidence computed from private run records. Seed hash `40d663101dc626aa1b55cf6d97ffb32e1603830814f924d858d91bd614b217da`. Frozen public cases: `lib/src/skillz_experiments/fixtures/self-test.json:1-106`. Implementation and verification are published in [PR #107](https://github.com/paulnsorensen/skillz-that-grillz/pull/107).

[^runtime]: GEPA 0.1.4 installed public signatures, inspected 2026-09-28; `lib/src/skillz_experiments/_search.py` and `_codex.py`. Model-free `Codex.preflight()` returns `isolation: passed` with zero live calls on Codex 0.154.0, 2026-09-28.

[^gepa]: [GEPA source note](../sources/gepa-optimize-anything.md), primary documentation verified 2026-09-28.
[^analytics]: [PR #105 source note](../sources/skillz-session-analytics-pr-105.md), pinned schema and coverage verified 2026-09-28.
[^prototype]: [Easy-cheese PR #722](https://github.com/paulnsorensen/easy-cheese/pull/722), metadata and body read 2026-09-28; pinned `scripts/agent_lab.py:300-315,552-646` at `0ec60bbf6a12e1c0d0a9097fdd6ac8659866330a`, read through GitHub Contents API.
[^prior]: Local easy-cheese records read 2026-09-28: `.cheese/research/gskill-gepa/gskill-gepa.md:1-128`; `.hallouminate/wiki/skill-optimization-landscape.md:10-135`; `.hallouminate/wiki/adr/cook-skill-optimization-loop-001.md:5-37`, `-002.md:5-37`, `-003.md:5-34`, and `-004.md:5-32`. This page preserves the relevant findings without requiring those external files.
[^vaudeville]: Local vaudeville records read 2026-09-28: `.cheese/research/pydantic-ai-native/pydantic-ai-native.md:9-36`; `docs/adr/pydantic-evals-calibration-001.md:7-26`; `docs/adr/pydantic-evals-calibration-002.md:7-29`; `docs/specs/typed-rule-core.md:20-32`.
[^skillz]: `skills/skillz/SKILL.md:25-39` and `skills/skillz/references/analytics-ceremony.md:3-9` at `b068dacbae5b8afd8c3abe2f531adc5193a99e25`.

_Source: recovered local research, pinned PR evidence, and primary GEPA documentation · Updated: 2026-09-28 · Supersedes: combined gskill identity and automatic-holdout implications in prior summaries_

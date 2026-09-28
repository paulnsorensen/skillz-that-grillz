# Session-driven skill prompt and CLI optimization

Skillz could turn a user's task history into a bounded GEPA experiment for a complete skill package.
Session analytics would select cases; an independent evaluator would measure improvement; the user would approve deployment.
Status: proposal, not an accepted specification or implemented feature.
The goal is “best measured for this use case,” not a globally optimal skill.

## Evidence behind the proposal

The [GEPA research synthesis](../research/gepa-session-optimization.md) separates verified capabilities from design inference.
The GEPA team's [gskill guide](../sources/gepa-gskill.md) demonstrates test-feedback skill learning.
Its [Universal API article](../sources/gepa-optimize-anything.md) supports named prompt and code components.
[PR #105](../sources/skillz-session-analytics-pr-105.md) supplies the proposed analytics input, not evaluation labels.

## User workflow

The proposed skillz workflow has six explicit stages.
The following command names illustrate a possible interface; none exists today.
Do not replace the existing `optimize` alias, which currently means `improve`.[^alias]

| Stage | Illustrative action | Observable result |
| --- | --- | --- |
| Profile | `skillz experiment profile <skill>` | Target paths, use case, deployment harnesses, success criteria, and spend limits. |
| Dataset | `skillz experiment dataset <profile>` | Redacted, provenance-backed task cases; explicit exclusions and coverage. |
| Baseline | `skillz experiment baseline <dataset>` | Paired seed measurements and verified graders. |
| Search | `skillz experiment search <run>` | Budgeted candidate history, reflection feedback, and best measured candidates. |
| Evaluate | `skillz experiment evaluate <run>` | Locked-candidate holdout comparison and promote/hold/inconclusive result. |
| Export | `skillz experiment export <run>` | Reviewable patch and evidence report; no automatic install, merge, or push. |

A user could start from a written use case when session data is absent.
User-approved cases would supply the evaluator before search begins.
Synthetic cases may test plumbing, but cannot establish personalization from real work.

## From sessions to evaluation cases

The proposed dataset builder would treat session history as sampling evidence, not ground truth.
It would first filter by project, time, harness, and target skill.
It would sample successful, failed, corrected, and ambiguous episodes.
It would report missing signals rather than convert them into success.

Each approved case would record:

- A stable case ID and private provenance pointer.
- Repository revision, fixture or environment digest, and dependency snapshot.
- The user request before the solution appears.
- The target skill version and observed harness; inferred attribution stays labeled.
- Required tools and permitted external services.
- An independent success check and evidence for its expected result.
- A task-family group, dataset split, and privacy classification.

Analytics cannot reliably reconstruct every field.
PR #105 truncates materialized result text and omits some native events.
Native transcript access therefore needs a separate consent boundary.[^analytics]
A case lacking its request, reproducible state, or trustworthy oracle would remain diagnostic-only.

The builder would deduplicate repeated sessions, retries, forks, and related issue variants.
All variants of one task family would stay in one split.
A later time window could test drift.
The optimizer would see training and selection-validation data, never sealed holdout answers or reference patches.

## Optimization loop

The proposed GEPA loop would optimize a map of approved text components.
It would not mutate the evaluator that assigns fitness.

```text
use case or approved sessions
  -> frozen dataset and evaluator
  -> seed baseline
  -> GEPA candidate
  -> path, contract, build, and test checks
  -> isolated real-agent runs
  -> scores plus redacted failure feedback
  -> next candidate within budget
  -> locked winner and sealed holdout
  -> human-reviewed export
```

The runner would install each candidate through the deployment harness's real skill-loading path.
A fixture would prove that the intended candidate loaded and its references and CLI resolved.
System-prompt injection alone would not prove activation or package behavior.

## Prompt and CLI mutation boundary

The proposed candidate scope would include `SKILL.md`, selected references, and declared skill-owned CLI source.
It could move repeated mechanical instructions into tested CLI behavior.
Existing public commands, schemas, exit codes, and safety rules would remain frozen unless the user approves a separate contract change.

Generated archives, command indexes, and packaging metadata would be rebuilt from source.
The optimizer would not patch a binary `.pyz` directly.
For targets that use them, [fromargs](../decisions/fromargs-cli-library.md) and [wedge](../decisions/wedge-skill-packaging.md) supply established CLI and packaging conventions.
Other consumers would supply their own build and test contract.

The runner would reject edits to graders, hidden tests, dataset splits, shared libraries, dependency policy, and permission configuration.
Path normalization would reject traversal and symlink escapes.
Changed-file checks would run outside the candidate environment.

Builds and generated CLI execution would require an enforced disposable sandbox.
A Git worktree alone would not provide isolation.
The sandbox would exclude host credentials and unrelated files, restrict network access, and enforce resource limits.
Model calls would use a controlled broker rather than expose provider keys to generated code.

## What the evaluator measures

The proposed evaluator would use task-specific outcomes rather than a universal prose-quality score.

| Target behavior | Primary check | Useful diagnostic |
| --- | --- | --- |
| Skill activation | Labeled should-trigger and should-not-trigger cases, where automatic invocation is supported | False activations and missed activations. |
| Coding task | Immutable fail-to-pass tests plus regression tests | Test output, patch scope, and missing checks. |
| Review task | Labeled defects and false-positive controls | Recall, precision, and unsupported findings. |
| Research task | Verified evidence requirements and output contract | Missing claims, unsupported citations, and trace failures. |
| CLI task | Actual command behavior and error contracts | Invalid arguments, parser retries, exit status, and structured output. |

A CLI could receive direct command tests and end-to-end agent tasks.
A parser improvement is not sufficient if the skill stops teaching the correct invocation.
A prose improvement is not sufficient if its referenced command fails.

A model judge, when unavoidable, would remain frozen and calibrated against human labels.
Its score would not replace executable checks where those checks exist.
Transcript instructions and candidate-generated feedback would remain untrusted data.

## Personalization and promotion

The proposed user profile would define the task distribution, deployment models, tools, latency constraints, and privacy requirements.
Observed frequency could suggest weights; the user would approve them.
Rare, high-consequence cases would remain mandatory gates.
Global discovery precision would not be an objective for a user-invoked-only skill.

The evaluator would report correctness, policy violations, full run tokens, latency, and cost separately.
Token accounting would include input, output, cache reads, and cache writes where available.
Missing token fields would remain unknown, not zero.
Costs would use pinned prices rather than treat all tokens as equally priced.

GEPA could retain candidates that trade accuracy against cost.
The release gate would apply the user's stricter policy.
A Pareto-front position alone would not authorize promotion.[^gepa]
The selected candidate and seed would run on the same holdout tasks and deployment harness.
Repeated runs and paired uncertainty estimates would distinguish improvement from noise.

Candidate selection would finish before holdout evaluation.
Holdout results would not feed reflection or another search round.
Repeated human selection against one holdout would also contaminate it; later experiments would need a fresh sealed set.

The default proposed result would be hold or inconclusive when coverage, replay quality, or evidence is insufficient.
No candidate would install itself.
An export would include rollback instructions and a profile-specific compatibility statement.

## Budget and experiment record

The proposed budget would cover baseline, candidate builds, agent runs, reflection, validation, final evaluation, and retries.
A metric-call limit alone would not bound total spend.
The runner would reserve final-evaluation budget and stop before that reserve is consumed.

The run record would pin dataset and candidate hashes, model identifiers, harness versions, tools, seeds, evaluator version, and environment.
It would record actual usage, failed infrastructure runs, resume state, and provenance.
Infrastructure failures would not silently become zero-quality skill outcomes.
Resuming with changed inputs would start a new experiment identity.

Private session text would remain local by default.
Redaction would remove credentials, unrelated content, and private identifiers before any approved provider submission.
A public PR would contain only approved summaries and non-sensitive fixtures.
Revoked cases would be excluded from future datasets and retained exports according to the user's retention policy.

## First proof experiment and unresolved decisions

The proposed first experiment would target one skill-owned CLI with reproducible tests and enough approved real cases.
It would compare the seed, prompt-only search, and prompt-plus-CLI search under the same frozen evaluator.
This comparison would test whether CLI mutation adds value beyond prompt rewriting.

Before implementation, choose the pilot skill, deployment harness, execution sandbox, budget, and acceptable regression threshold.
Confirm whether skillz owns the reusable loop or consumes an extracted easy-cheese runner.
Validate PR #105's integration after it lands; this documentation PR does not depend on merging its code.

Done would mean a reproducible dataset, verified candidate loading, enforced mutation boundaries, and an independently graded comparison.
A successful result could be “no improvement”; that is better than promoting an unproven candidate.

[^alias]: `skills/skillz/SKILL.md:25-39` at `b068dacbae5b8afd8c3abe2f531adc5193a99e25`.
[^analytics]: [Session analytics source and limitations](../sources/skillz-session-analytics-pr-105.md), verified 2026-09-28.
[^gepa]: [GEPA research corrections](../research/gepa-session-optimization.md), including the distinction between non-domination and promotion.

_Source: user request, recovered local research, and the linked primary-source synthesis · Updated: 2026-09-28_

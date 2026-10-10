# Skill behavior specifications for evals

Separate author intent, concrete cases, execution policy, and grading evidence.
NVIDIA's `evals/EVAL.md` is the closest upstream intent document found in this bounded research.[^1]
It is not a universal Agent Skills behavior schema.
The sources were checked on 2026-10-10.

## What each source provides

| Source | Role | Important limit |
| --- | --- | --- |
| Agent Skills specification | Package metadata and discovery format | The instruction body remains free-form.[^2] |
| Agent Skills evaluation guide | Prompts, expected output, fixtures, and evidence-backed assertions | Qualitative results still need judgment.[^3] |
| Anthropic skill-creator | Authoring interviews, paired comparisons, and activation tests | Its example schema differs from other guides.[^4] |
| NVIDIA SkillEvaluator | Author intent plus dataset and execution artifacts | Extensions belong to its runner.[^1] |
| OpenAI eval-skills | Outcome, process, style, and efficiency goals before authoring | A method and example, not a universal schema.[^5] |
| Firebase evaluation workflow | Baseline, activation, single-skill, and integrated user journeys | Reported practice, not independently reproduced here.[^6] |

These sources complement each other.
Packaging conformance does not prove that a skill performs its task correctly.

## NVIDIA's upstream intent document

The generator reads `Questions`, `Behaviors`, `Negative Cases`, and `Notes` headings.
Author-specified behaviors remain requirements when trial agents omit them.[^1]
That rule protects expected behavior from becoming a description of current failures.

NVIDIA separates these artifacts:[^1]

- `evals/EVAL.md`: author guidance for case generation.
- `evals/evals.json`: prompts, expected outputs, and optional assertions.
- `evals/config.yml`: runtime policy, attempts, and thresholds.
- `evals/files/`: input fixtures.
- `evals/grader.py` or `evals/grader.sh`: custom checks.
- `evals/environment/`: reproducible execution setup.

Optional routing fields include `expected_skill`, `expected_script`, and `acceptable_skills`.
A null expected skill represents a negative routing case.
These fields do not establish cross-runner compatibility.
Refinement can derive expected outputs from trajectories.
Preserve independent author requirements when reviewing those outputs.

## Format differences must remain explicit

The inspected Anthropic schema uses `expectations`.
The Agent Skills guide uses `assertions`.
Their grading examples also differ.[^3][^4]
Select and validate the consumer before generating machine-readable cases.

No inspected source mandates a universal requirement-to-scenario-to-evidence traceability format.
This is a bounded finding, not proof that no such format exists.

## Authoring order and evaluation layers

OpenAI starts with measurable success dimensions before skill authoring.[^5]
Firebase starts with expected outcomes and a no-skill baseline.[^6]
The Agent Skills guide refines detailed assertions after observing initial outputs.[^3]
These sequences differ without contradicting packaging requirements.

Anthropic compares a new skill with no skill, or an update with the previous revision.
It also tests activation with positive queries and near-miss negatives.[^4]
Activation, task success, and multi-skill workflow success are separate questions.
A strong task result does not establish correct activation.

## Proposed local practice

This section is synthesis, not a claim of upstream standardization.

1. Write stable behavior requirements before the candidate skill.
2. Derive cases with explicit inputs and observable expected artifacts.
3. Record the requirement IDs that each case checks.
4. Use executable checks for mechanical facts.
5. Use evidence-backed review for scenario altitude and behavioral meaning.
6. Preserve rejected actions, unchanged-state assertions, and other negative cases.
7. Record environment, revision, commands, failures, and unrun checks.
8. Keep observed baseline failures separate from intended behavior.
9. Keep performance comparisons separate from correctness claims.
10. Use independent holdout cases before claiming general improvement.

For BDD generation, inspect [suite design](./behavioral-bdd-gherkin.md).
Test that generated scenarios detect a representative real regression.
A passing text rubric alone cannot establish that result.

## Relationship to the existing skillz runner

This research does not replace the existing runner's contract or approval workflow.
[Autoimprove decisions](../decisions/skillz-autoimprove.md) describe contracts and command/hybrid graders.
[Pragmatic autoimprove decisions](../decisions/skillz-pragmatic-autoimprove.md) describe approved cases, family splits, budgets, and holdout comparisons.
Follow current runner instructions when adapting these requirements.
Do not label a runner-neutral case document as a compatible runner dataset without validation.

## Sources

[^1]: https://docs.nvidia.com/skills/skillevaluator/eval-datasets
[^2]: https://agentskills.io/specification
[^3]: https://agentskills.io/skill-creation/evaluating-skills
[^4]: https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md and https://github.com/anthropics/skills/blob/main/skills/skill-creator/references/schemas.md
[^5]: https://developers.openai.com/blog/eval-skills
[^6]: https://firebase.blog/posts/2026/08/eval-driven-development-agent-skills

_Source: Skill behavior specification research and linked primary sources · Updated: 2026-10-10 · Supersedes: none_

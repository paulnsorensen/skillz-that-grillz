# LLM judge scoring and binary criteria for skillz autoimprove

The skillz autoimprove judge returns one 0-100 number per case, but the evidence favours decomposed binary criteria over a wide numeric scale.
This page is research, not a decision.
A `/mold` design session on the judge verdict shape and on criteria provenance is in progress.
This page records no design choice.
The sources were checked on 2026-10-19.

## Current autoimprove grading behaviour

The skillz autoimprove grader contract has six grader types: `exact-json`, `command`, `judge`, `hybrid`, `journey`, and `audit`.
`packages/skillz/src/skillz_experiments/_contract.py:16` declares them.
`packages/skillz/src/skillz_experiments/evaluation/_evaluator.py:228` scores the `command`, `judge`, `hybrid`, and `journey` types.
A `hybrid` or `journey` case runs its command gate first.
A failed gate skips the judge (`_evaluator.py:245`).

The judge function is `judge` in `packages/skillz/src/skillz_experiments/_graders.py:230`.
Without pillars, the judge returns `score_percent`, one integer from 0 to 100.
With pillars, the judge returns one 0-100 integer for each pillar.
The function averages the integers and divides the mean by 100 (`_graders.py:264`).
It rejects a response with a missing field, an extra field, or an integer outside 0-100.

The default task rubric already asks for a binary result.
`TASK_RUBRIC` in `packages/skillz/src/skillz_experiments/_intake.py:30` says "Score 100 for a correct answer and 0 for a wrong answer."
The answer schema still accepts any integer from 0 to 100.

A journey case has a typed `expected` object with `must` and `must_not` lists.
ADR-023 in [autoimprove decisions](../decisions/skillz-autoimprove.md) records this type.
The judge reads these lists as `reference` fields and folds them into one number.
It returns no verdict for each item in a list.

The evaluator makes one judge call for each scored case (`_evaluator.py:259`).
`judge` does not sample the judge more than once.
The run-to-run variance of this judge on skillz cases is not measured.

## Judge scale research

A 2026 grading-scale study (arXiv 2601.03444) found that a 0-5 scale aligns best with human ratings and a 0-10 scale aligns worst.
The same study found that 0-100 stays close to 0-5.
It also found that scale effects dominate sampling-temperature effects.
It reports that LLM judge reliability drops on subjective tasks, while human panels stay consistent.
Confidence: `speculating`, because this is a single study with no binary arm.
Source: https://arxiv.org/pdf/2601.03444

No fetched source compares a 0-100 scale with a binary verdict directly.
Thus the evidence does not show that 0-100 is the more reliable choice.

The MT-Bench paper reports that a reference answer cuts judge failure on math and reasoning from 70% to 15%.
Confidence: `speculating`, because that result is for pairwise grading, not a single 0-100 score.
Source: https://arxiv.org/pdf/2306.05685

## Decomposed binary checklist research

Decomposed binary checklists have the strongest support among LLM judge formats in this research.
Three sources agree.

- CheckEval (arXiv 2403.18771) raises the average agreement across evaluator models by 0.45 and reduces score variance against Likert scoring. Confidence: `certain`. Source: https://arxiv.org/abs/2403.18771
- InFoBench (arXiv 2401.03601) reports higher reliability for decomposed yes/no requirement questions (DRFR). Confidence: `certain`. Source: https://arxiv.org/abs/2401.03601
- Anthropic's "Develop tests" guide tells graders to output only "correct" or "incorrect", or a 1-5 score, with a detailed rubric and reasoning first. Confidence: `certain` for Claude graders. Source: https://docs.claude.com/en/docs/test-and-evaluate/develop-tests

A rubric-reward paper (arXiv 2507.17746) reports that rubric rewards beat direct Likert judge rewards and reduce variance across judge sizes.
Confidence: `speculating`, because the setting is RL rewards, not prompt optimization.
Source: https://arxiv.org/abs/2507.17746

## Generated instance checklist research

LLM-generated, instance-specific yes/no checklists help when no labels exist, but the gain is modest and the criteria need human checks.

- TICK (arXiv 2410.03608) raises exact agreement with human preference from 46.4% to 52.2% over direct scoring. Confidence: `speculating`, because the labels are preferences, not correctness. Source: https://arxiv.org/abs/2410.03608
- RocketEval (arXiv 2503.05142) gets high correlation only after it reweights checklist items against supervised annotations. That step does not carry to a no-label setting. Confidence: `speculating`. Source: https://arxiv.org/abs/2503.05142
- EvalGen (arXiv 2404.12272) reports that LLM-generated evaluators inherit the problems of the LLMs they evaluate and need human validation. It also reports "criteria drift": some criteria depend on the observed outputs, so not all criteria can be fixed before grading. Confidence: `speculating`, because it is a qualitative study. Source: https://arxiv.org/abs/2404.12272

No fetched source tests an optimizer that sees or games the generated criteria.
No fetched source tests a judge that writes and then grades its own criteria.

## EARS as judge criteria research

EARS (Easy Approach to Requirements Syntax) fits trigger-and-response behavioural criteria and fits poorly for quality criteria that are not conditional.

- Alistair Mavin's EARS page gives a fixed clause order: zero or many preconditions, zero or one trigger, one system name, and one or many system responses. Confidence: `certain`. Source: https://alistairmavin.com/ears
- Kiro writes acceptance criteria in EARS and claims that each requirement translates directly into test cases. This is a claim, not a measurement. A Martin Fowler practitioner report saw vague criteria in practice. Confidence: `speculating`. Sources: https://kiro.dev/docs/specs/feature-specs and https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html
- The Wikipedia EARS article says EARS is less appropriate for non-functional requirements that cannot be written as conditional behaviour. Confidence: `speculating`, because it is a secondary source. Source: https://en.wikipedia.org/wiki/Easy_Approach_to_Requirements_Syntax
- A KIT paper reports that LLM-converted EARS requirements are rated similar to human rephrasings. Confidence: `speculating`, because only the abstract page was read. Source: https://publikationen.bibliothek.kit.edu/1000194588

No fetched source uses EARS as LLM judge criteria.
The repository already writes acceptance criteria in a WHEN / THE SYSTEM SHALL form, as in [criteria/gh-368.md](../criteria/gh-368.md).

## Open questions for the judge design

These questions are open, and the `/mold` session owns them.

- Should the judge return one boolean per criterion and compute the score as the pass fraction, or keep one holistic verdict?
- For cases without labels, should a separate model call write the criteria, should the case hash freeze them, and should a human approve them like a draft contract?
- Should EARS be the form for behavioural criteria only, with plain yes/no questions for quality criteria such as idiomatic style?
- Should the optimizer proposer see the criteria, or only the failures for each criterion?

The research proposes three small local checks.

| Question | Smallest local check |
| --- | --- |
| Per-criterion booleans versus 0-100 | Score one candidate on five cases ten times each with the current judge and with a boolean-checklist prompt. Compare the standard deviation for each case. |
| Judge-generated criteria | Generate criteria three times for one case and measure the overlap. Grade a known-bad output against each set. |
| EARS as a criteria form | Rewrite the `must` list of one journey case in EARS. Compare judge agreement with the plain list on known-good and known-bad transcripts. |

## Related pages

- [Skill behavior specifications for evals](./skill-behavior-specifications-for-evals.md) covers author intent and grading evidence.
- [Behavioral BDD and Gherkin suite design](./behavioral-bdd-gherkin.md) covers scenario altitude.
- [Autoimprove decisions](../decisions/skillz-autoimprove.md) record the accepted grader contract.

_Source: brie research report `llm-judge-binary-criteria-ears` (2026-10-19, source hash 82aa7d68c88aff27) and the code paths cited above · Updated: 2026-10-19 · Supersedes: none_

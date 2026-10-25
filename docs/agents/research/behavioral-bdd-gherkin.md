# Behavioral BDD and Gherkin suite design

Use domain-level scenarios and select execution boundaries separately.
A readable scenario does not prove that a test exercises integrated production behavior.
This research compares authoring guidance, runner skills, and regression evidence.
The sources were checked on 2026-10-10.

## Two altitude decisions

Scenario altitude describes the business rule and its observable result.
Execution altitude identifies the real components that the test crosses.
Keep incidental UI, SQL, and storage mechanics out of scenario prose.
Keep mechanics visible when an API, CLI, or protocol is itself the public contract.[^1]

Cucumber supports testing through ports below the UI.
Substitutes need contract fidelity.
Selected full-stack tests still check real connections.[^2]
Therefore, integration-first does not mean browser-only or unit-test-free.

## Candidate comparison

These assessments compare inspected instructions, not measured agent performance.

| Candidate | Useful scope | Limit |
| --- | --- | --- |
| intent-driven-dev `gherkin-authoring` | Domain language and observable outcomes | Authoring guidance does not select execution boundaries.[^3] |
| vitalets `playwright-bdd` | Feature-to-step-to-Playwright execution | Runner-specific; main-flow advice does not establish negative coverage or regression detection.[^4] |
| FradSer `behavior-driven-development` | Observed RED failures and scenario independence | Blanket deletion of existing production code makes unchanged adoption unsafe.[^5] |
| pproenca `acceptance-pipeline-feature-design` | Composition within its acceptance pipeline | Requires that pipeline and catalog; changing example data is not production mutation evidence.[^6] |
| Automation Panda guidelines | Detailed Gherkin language and assertion rules | A context document, not a packaged execution skill.[^1] |

Intent is the strongest standalone authoring fit in this bounded comparison.
No inspected candidate establishes the complete requested suite-design contract.
This is not an ecosystem-wide absence claim.

## Adoption evidence

The discovery snapshot records directory installs, repository stars, and MIT licenses.
Counts describe activity, not quality or regression detection.

| Candidate | Directory installs | Repository stars |
| --- | ---: | ---: |
| intent-driven-dev | 37 | 9 |
| vitalets | 316 | 803 |
| FradSer | 145 | 593 |
| pproenca | 123 | 215 |
| Automation Panda | Not a packaged skill | 56 |

These counts belong to the 2026-10-10 research snapshot, not live rankings.[^7]
Repository stars do not measure one skill.

## Proposed evaluation contract

The following requirements are synthesis, not upstream guarantees:

- Map each requirement to a scenario, production boundary, assertion, and failure probe.
- Exercise real application wiring and durable state where those boundaries carry risk.
- Separate setup shortcuts from actions that bypass the behavior under test.
- Check exact external outcomes, not mock calls or private implementation state.
- Check negative outcomes and unchanged state after rejection.
- Isolate scenarios with temporary resources and deterministic input.
- Prove that a representative production regression fails the intended assertion.
- Restore the changed production behavior and confirm the same scenario passes.
- Retain focused unit tests for algorithms and edge cases.
- Measure feedback cost without removing behavioral coverage.

Local `tdd-assertions` and `press` address assertion quality and adversarial testing.
They complement initial suite authoring rather than replace it.[^8]

## Relation to skill evaluation

Use [skill behavior specifications](./skill-behavior-specifications-for-evals.md) to preserve author intent before case generation.
Evaluate generated tests by execution and failure sensitivity, not Gherkin appearance alone.
No candidate suite executes during the source research.
Comparative effectiveness remains unverified.

## Sources

[^1]: https://github.com/AutomationPanda/gherkin-guidelines-for-ai/blob/main/gherkin-guidelines.md
[^2]: https://cucumber.io/docs/guides/testable-architecture
[^3]: https://github.com/intent-driven-dev/skills/blob/main/.agents/skills/gherkin-authoring/SKILL.md
[^4]: https://github.com/vitalets/playwright-bdd/blob/main/skills/playwright-bdd/SKILL.md
[^5]: https://github.com/FradSer/dotclaude/blob/main/superpowers/skills/behavior-driven-development/SKILL.md
[^6]: https://github.com/pproenca/dot-skills/blob/master/skills/.experimental/acceptance-pipeline-feature-design/SKILL.md
[^7]: Research snapshot: `bdd-gherkin-behavioral-suite-skills`, `candidate-manifest.json`, `raw/skills-discovery.txt`, and `raw/panda-metadata.json`; collected 2026-10-10.
[^8]: Installed `tdd-assertions/SKILL.md:15-38` and `press/SKILL.md:39-45`; inspected 2026-10-10.

_Source: BDD/Gherkin research synthesis and linked primary sources · Updated: 2026-10-10 · Supersedes: none_

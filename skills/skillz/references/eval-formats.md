# Eval file formats

This page is the one reference for the files that you write for an autoimprove run.
Read it before you edit `evals/evals.json`, `evals/journeys.json`, or `evals/autoimprove.json`.
The run steps are in the `autoimprove` workflow that `SKILL.md` names.

## The files you write

| File | Schema | How it is checked |
| --- | --- | --- |
| `evals/evals.json` | `references/schemas/evals.schema.json` | CI reads it strictly. The seed reads it leniently. |
| `evals/journeys.json` | `references/schemas/journeys.schema.json` | CI reads it strictly. The seed reads it with the same code. |
| `evals/autoimprove.json` | `references/schemas/autoimprove.schema.json` | `run` and `validate` load it as the contract. |
| `evals/gates/*.py` | none | The contract names a gate file in a kind `gate` field. |

The Python readers are the source of truth. A schema gives your editor completion and a first check.
A schema cannot express a duplicate id, a family count, or a missing file. Run `validate` for those rules.

The schema files are generated from the Python constants. Do not edit them by hand.
To print one, run `skillz-experiment.pyz schema evals`, `journeys`, `contract`, or `draft`.
A test fails when a committed schema differs from the generated text, and it prints the command to regenerate it.

### The `$schema` key

An `evals.json` or `autoimprove.json` file may start with a `$schema` key.
Its value is a relative path from the directory of the file, such as `../references/schemas/evals.schema.json`.
The runner ignores the key and never stores it, so it does not change the identity of a contract.
`scaffold` writes the key only when the skill ships `references/schemas/autoimprove.schema.json`. Otherwise map the file in your editor settings.

The file `journeys.json` is a bare JSON list, so it has no place for a `$schema` key.
Map it to its schema by file name in your editor settings. In VS Code, add an entry to `json.schemas` with `fileMatch` set to `**/evals/journeys.json`.

## `evals.json`

The file is an object with a `skill_name` and a nonempty `evals` list. Other top-level keys are allowed.
Each entry in `evals` has these fields:

| Field | Meaning |
| --- | --- |
| `id` | A whole number, unique in the file. |
| `name` | Unique text. It becomes the case id. |
| `prompt` | The request that the user sends. |
| `expected_output` | What a good answer does, in prose. |
| `files` | Fixture paths. Each starts with `evals/files/`. |
| `kind` | `trigger`, `near-miss`, or `task`. |
| `assertions` | Checks for a `task` entry. A `trigger` or `near-miss` entry has none. |
| `family` | A group of related cases. A `task` entry needs it for the split. |
| `expected_skill` | The skill that must start. `null` marks a `near-miss`. |

The three kinds differ in what they check:

- A `trigger` entry passes when the named skill starts for the prompt.
- A `near-miss` entry passes when the skill stays out. Its `expected_skill` is `null`.
- A `task` entry passes when the work meets its `assertions` and `expected_output`.

## `journeys.json`

The file is a JSON list with at least one case. Each case has these fields:

| Field | Meaning |
| --- | --- |
| `id` | Unique text. It is unique across `evals.json` and `journeys.json`. |
| `family` | A group of related cases. The seed uses the `id` when it is absent. |
| `kind` | A contract kind that uses the journey grader. It is never `trigger`, `near-miss`, or `task`. |
| `request` | The first user message. |
| `brief` | What the simulated user wants and knows. |
| `max_turns` | The most turns in the conversation. The default is 8. |
| `requirements` | Unique requirement ids that the case covers. |
| `files` | An inline map of path to text. It is optional. |
| `expected` | The closed reference object below. |
| `source` | `skill` for a case drafted from the skill, or `session` for a case mined from past sessions. The seed uses `skill` when it is absent. |

CI requires `id`, `family`, `kind`, `request`, `brief`, `expected`, and `source`. Any other key is an error.

The `expected` object takes only these keys. It needs a nonempty `must` or `must_not`. The `criteria` list is optional in `journeys.json`, but intake requires it before a run scores a judged case:

| Key | Meaning |
| --- | --- |
| `must` | Each item is an observable that the transcript must show. |
| `must_not` | Each item is an observable that the transcript must not show. |
| `questions` | The only questions that the agent may ask. An empty list means none. When absent, any question is allowed. |
| `final_stop` | The stop or state in which the conversation must end. |
| `criteria` | The approved EARS statements that the judge grades. |

### Criteria

Each judged case (grader `judge`, `hybrid`, or `journey`) needs `expected.criteria`. The judge gives each criterion a level from 1 to 4, and code computes the score. Each criterion has these fields:

| Field | Meaning |
| --- | --- |
| `id` | Unique in the case. It matches `[A-Za-z0-9_.-]{1,64}`. |
| `text` | One EARS statement: at most one leading `WHEN`, `WHILE`, `IF`, or `WHERE` clause, then one `THE <subject> SHALL <response>`. |
| `must` | Optional. `true` marks a dealbreaker: level 1 or 2 scores the case 0. The default is `false`. |
| `from` | Optional. The source item, such as `assertion:2`, `must:0`, `questions`, or `final_stop`. |

A judged case without criteria stops with `case-criteria-missing`. A criterion with a bad shape, a duplicate id, or text that is not EARS stops with `case-criteria-invalid`.

## `autoimprove.json`

The contract tells the runner how to grade the cases. These top-level fields exist:

| Field | Meaning |
| --- | --- |
| `schema_version` | The integer `1`. |
| `status` | `approved` or `draft`. A draft stops the run. |
| `skill` | The skill directory name. |
| `invocation` | The request that calls the skill. It supports `{skill}` and `{path}`. |
| `kinds` | A map of case kind to a kind object. |
| `helper` | Optional. A bundled helper script and its fixtures. |
| `editable` | Optional. Relative paths that search can change. |
| `surface` | Optional. The extra tool surface of the task role. |
| `statistics` | Optional. Promotion thresholds for the holdout gate. |
| `harness_phrases` | Optional. Sandbox-only phrases of the requests, each of at least two words. Search rejects a proposal that adds one. |

A kind object has these fields:

| Field | Meaning |
| --- | --- |
| `grader` | `exact-json`, `judge`, `command`, `hybrid`, `audit`, or `journey`. |
| `argv` | The grader command. The `command`, `hybrid`, and `journey` graders take it. |
| `gate` | A gate file under `evals/gates/`. It stands for `argv`. Do not set both. |
| `rubric` | The judge rubric. The `judge`, `hybrid`, and `journey` graders need it. |
| `capture` | A command that saves files for the judge to view. |

A kind object has no `pillars` field. A contract that declares it stops with `contract-pillars-removed`. Move each pillar into case criteria.

The `helper` object has these fields:

| Field | Meaning |
| --- | --- |
| `path` | The helper script, relative to the skill. |
| `input` | The file name that the helper reads. The default is `fixture.md`. |
| `fixtures` | Recorded inputs, return codes, and outputs. |

The `surface` object has these fields. Each path is relative to the skill and names a file that the candidate holds:

| Field | Meaning |
| --- | --- |
| `tools` | Extra tool names for the task role. |
| `mcp_config` | A JSON file of MCP servers. The run never edits it. |
| `agents` | Markdown files of agents. The run never edits them. |
| `companions` | Skill directories that stay read-only. |

The `statistics` object takes these fields. Every number is finite and at least 0:

| Field | Meaning |
| --- | --- |
| `min_gain` | The smallest mean holdout delta that can promote. |
| `min_lower_bound` | The margin that the delta must clear beyond 2 SE. |
| `family_budget` | The regression budget of every holdout family. `null` turns it off. |
| `family_budgets` | A map of family name to its own budget. |
| `max_token_increase_per_gain` | The largest token change that one unit of gain can buy. `null` turns it off. |
| `min_token_saving` | The smallest token saving, above 0 and below 1, that can promote a cheaper winner. |
| `min_coverage` | The least share of holdout cases that both arms must score, from 0 to 1. |
| `max_quality_loss` | The largest quality loss that `promote-cheaper` accepts. |

The `autoimprove` workflow section "Set promotion thresholds" gives the effect of each threshold.

Search also rejects a proposal that adds a sandbox path from a train or validation request, such as `output/plan.json` or `capture/log.txt`.
A `<n>` placeholder in a path, as in `output/pr-<n>.md`, matches any number.
A path or phrase that a seed file already holds stays allowed.

## The minimums

A run needs at least four scored families. Train needs at least four scored cases from two families.
Validation needs one family. The holdout split needs at least six scored cases.
The split gives each family to one split only. It shuffles the families with the run seed.
The first two families in that order go to train and validation.
If the rest hold fewer than six scored cases, the split orders the families smallest first instead.
The next family always goes to the holdout split. More families join it until it holds six cases.
The remaining families alternate between validation and train.
If this split misses a minimum, validation takes the smallest family of at least two scored cases that leaves the other minimums met.
A validation split of one case gives one paired delta, and a promotion needs two.
If no such family exists, the smallest family goes to validation.
Train then takes the smallest set of the other families that meets the train minimums. The holdout takes the rest.

The promotion gate needs the paired delta to exceed twice its standard error over three repeats.
Fewer than two holdout cases is always inconclusive.
A simulation with 3 repeats and 5 candidates gave these rates for 3 and for 6 holdout cases:

| Holdout cases | False promotion | Power at a 0.3 effect |
| --- | --- | --- |
| 3 | 1.95% | 17% |
| 6 | 1.35% | 43% |

Four families are structural: two train families, one validation family, and one holdout family.
A train split of fewer than four cases gives the search too little signal to choose a candidate.

A worked example: the scored families are `a` with 1 case, `b` with 2, `c` with 2, and `d` with 2.
The smallest family, `a`, goes to validation. Train takes `b` and `c`, which hold 4 cases.
The holdout holds the 2 cases of `d`, so the deficit is 4.
Add 4 scored cases to `d`, or add one new family of at least 4 cases.
The run stops with `cases-too-few`, and `validate` reports the same finding before any model call.

## Check the files offline

Run `skillz-experiment.pyz validate --target SKILL` before the first `run`.
It makes no model call, no host check, and no write. It reports every problem in one pass.
Each finding has `code`, `severity`, `file`, `pointer`, `message`, and `fix`.
The command exits 1 when any finding has severity `error`.

## The files the runner writes

The run directory `RUN` holds files that the runner writes. Edit only the draft.

- `RUN/cases.seed.json` holds the cases that the runner reads from your files. Do not edit it.
- `RUN/cases.draft.json` holds the cases that the agent drafts. You approve it by its hash. Its schema is `references/schemas/draft-case.schema.json`.
- `RUN/cases.json` holds the approved cases with their splits. Do not edit it.
- `RUN/run.json` holds the frozen settings and the run state. Do not edit it.

A draft case has the same fields as a journey case, with these differences.
The `kind` may also be `trigger`, `near-miss`, or `task`.
A `trigger` or `near-miss` case needs neither `brief` nor `expected`.
A `task` case needs `files` and `expected` but no `brief`.

The seed, the draft, and `RUN/cases.json` keep each fixture text once.
The seed and `RUN/cases.json` are objects with a `fixtures` map (path to text) and a `cases` list.
In these files, a case `files` value is a list of paths into `fixtures`.
The draft is a list of cases with inline `files` maps, or an object with `fixtures` and `cases`.
A path that is not in `fixtures` stops the run, and the message names the case.
The seed bound counts each distinct fixture once, however many cases list it.

## Repository-internal formats

These files are test fixtures. They never ship in a skill.

- `packages/skillz/tests/skillz_experiments/fixtures/mode/mode-fixtures.json` is the mode-fixture manifest. Its `target` block is a test convention, and `run` never reads it.
- `packages/skillz/tests/skillz_experiments/fixtures/self-update.json` is the case manifest of the `skillz-self-update` skill.
- `packages/skillz/tests/skillz_experiments/fixtures/mode-outputs.json` holds the recorded golden and seeded-bad outputs.

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

The `expected` object takes only these keys, and it needs a nonempty `must` or `must_not`:

| Key | Meaning |
| --- | --- |
| `must` | Each item is an observable that the transcript must show. |
| `must_not` | Each item is an observable that the transcript must not show. |
| `questions` | The only questions that the agent may ask. An empty list means none. When absent, any question is allowed. |
| `final_stop` | The stop or state in which the conversation must end. |

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

A kind object has these fields:

| Field | Meaning |
| --- | --- |
| `grader` | `exact-json`, `judge`, `command`, `hybrid`, `audit`, or `journey`. |
| `argv` | The grader command. The `command`, `hybrid`, and `journey` graders take it. |
| `gate` | A gate file under `evals/gates/`. It stands for `argv`. Do not set both. |
| `rubric` | The judge rubric. The `judge`, `hybrid`, and `journey` graders need it. |
| `capture` | A command that saves files for the judge to view. |
| `pillars` | One to eight names of judge scores. The kind score is their mean. |

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

## The minimums

A run needs at least three scored families and a holdout split of at least six scored cases.
The split gives each family to one split only. The two smallest families go to train and validation.
The other families form the holdout split.

The promotion gate needs the paired delta to exceed twice its standard error over three repeats.
Fewer than two holdout cases is always inconclusive.
A simulation with 3 repeats and 5 candidates gave these rates for 3 and for 6 holdout cases:

| Holdout cases | False promotion | Power at a 0.3 effect |
| --- | --- | --- |
| 3 | 1.95% | 17% |
| 6 | 1.35% | 43% |

Three families are structural: one family for each split.

A worked example: the scored families are `a` with 1 case, `b` with 2, `c` with 2, and `d` with 2.
The two smallest, `a` and `b`, go to train and validation. The rest hold 4 cases, so the deficit is 2.
Add 2 scored cases to `c` or `d`, or add one new family of at least 2 cases.
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

A draft case has the same fields as a journey case, with two differences.
The `kind` may also be `trigger`, `near-miss`, or `task`. The `source` may be `skill` or `session`.

## Repository-internal formats

These files are test fixtures. They never ship in a skill.

- `packages/skillz/tests/skillz_experiments/fixtures/mode/mode-fixtures.json` is the mode-fixture manifest. Its `target` block is a test convention, and `run` never reads it.
- `packages/skillz/tests/skillz_experiments/fixtures/self-update.json` is the case manifest of the `skillz-self-update` skill.
- `packages/skillz/tests/skillz_experiments/fixtures/mode-outputs.json` holds the recorded golden and seeded-bad outputs.

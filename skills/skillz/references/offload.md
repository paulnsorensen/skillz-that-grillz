# Offload fixed work to a CLI

Read this when `wedge` runs, or when `improve` implements an approved offload.
Move repeatable computation into code. Keep interpretation and decisions in the skill.

**Iron law:** no CLI without an explicit behavior contract and a verified invocation line.

The benefit is less repeated code generation and less raw output in context.
Do not claim measured token savings without a comparison.

## 1. Find

A candidate is a fixed parse, validation, count, filter, sort, or projection that every run repeats.
A bundled script without an output contract is also a candidate.
Classification, recommendations, and user decisions stay in prose.
Reuse an existing command when it already satisfies the contract.

Input → Output: repeated reading and counting of text records → one `records counts PATH` command returning stable counts.

## 2. Contract

Write seven fields before you edit: command, inputs, output shape, ordering with tie-breaks, empty result, errors, and side effects.
Name the prose that stays and the target line that will call the command.

## 3. Implement

Use fromargs by default.
Read the fromargs contract before you write handlers.
Copy `assets/records.py` from this skill into the target skill.
Then replace the example names and behavior.
The template carries PEP 723 metadata, so it also runs without a uv project.

1. Keep the CLI noninteractive with explicit inputs and concise generated help.
2. Validate domain constraints, not only argument types.
3. Sort results explicitly, including tie-breaks.
4. Return data from handlers; let fromargs serialize it.
5. Return a top-level sequence when decorator truncation is appropriate.
6. Use a bounded default. Document how callers request complete output.
7. Keep diagnostics off stdout.
8. For writes, define authorization, preview behavior, and retry safety before you implement them.

The template counts nonempty, stripped UTF-8 lines.
It sorts by descending count, then by case-sensitive value.
Its output limit does not bound input size or computation.
For large inputs, define and test a separate resource limit.

When the user declines fromargs, write a stdlib `argparse` script that runs as `python3 -I`.
A shell pipeline over existing binaries is also acceptable.
Keep the same contract: one JSON document on stdout, and one JSON error line on stderr.
Use exit status 2 for a usage error and 3 for a failed input contract.

## 4. Deliver

Wedge the CLI when all of these conditions are true:

- The target repository has a uv project with `pyproject.toml` and a committed `uv.lock`.
- `uv` is on `PATH`.
- Every dependency is a pure-Python wheel without a platform marker.
- No dependency is a path or URL requirement.
- fromargs resolves: it is in the project's `pyproject.toml` and `uv.lock`, or the manifest vendors it through `include`.
- The user does not decline wedging.

A dependency write is a change to the target's `pyproject.toml` or `uv.lock`.
Name each dependency write in the approval question.
Wedge each CLI from its own directory, as `<skill>/<cli>/wedge.toml`; the wedge packaging reference shows the layout.

A CLI that runs an external binary through a subprocess can still be wedged.
Read the wedge packaging reference before you create a manifest or run the builder.
Then generate the launcher and lock, and verify the installed path.

Otherwise, ship the CLI unpackaged.
When `uv` is missing, ship the stdlib `argparse` CLI or a shell pipeline, not fromargs.
Resolve each script path against the loaded skill directory, as `<this-skill-directory>` does in `SKILL.md`.
The call site in the target `SKILL.md` stays relative.

| CLI | Invocation line | Host needs |
| --- | --- | --- |
| fromargs | `uv run --script scripts/<name>.py` | `uv`; network on first run |
| `argparse` | `python3 -I scripts/<name>.py` | Python 3.11 or later |
| Shell | `bash scripts/<name>.sh` | the binaries it runs |

The PEP 723 block pins `fromargs>=0.2,<0.3` and every other dependency.
State why the run did not wedge.
The reasons are: no uv project, no committed `uv.lock`, no `uv` on `PATH`, fromargs not resolvable, a platform wheel, a path or URL requirement, or the user declined.

Do not silently copy shared libraries or change shared dependencies.
Do not claim that installing wedge creates the required project.
In this repository, do not change the `lib/fromargs` closure to make packaging pass.

### Prove the delivered CLI

1. Test valid, empty, and invalid input through the invocation line.
2. Compare repeated outputs for the same input.
3. Test default truncation and `--full` separately.
4. Run the invocation line from a directory outside the source checkout, with the script path resolved against the loaded skill directory.
5. For a wedged CLI, build a local archive and run it through the launcher with `WEDGE_PYZ`.
6. For a wedged CLI, change one archive member and confirm that the launcher exits with status 3.
7. Run the project's canonical gate and inspect the final diff.

## 5. Wire

Replace the prose step with the invocation line and the output contract.
Show the path relative to the target's `SKILL.md`.
State when to run the command and the recovery action for each error status.
Keep the interpretation in prose.
Tell the agent to run the CLI, not to read its source on each use.

## Red flags

Stop when you see a hand-copied `.pyz`, a `.pyz` beside a launcher, a handler-owned `--full`, or an assumed dependency installation.

| Rationalization | Why it fails | Required action |
| --- | --- | --- |
| "Copy the archive; it already runs." | A hand-copied `.pyz` bypasses the launcher and the committed lock. | Generate the launcher and lock, or use `wedge bundle` for direct mode. |
| "The handler needs a full flag." | fromargs reserves `--full` and `--json`. | Use decorator limits and the global flags. |
| "The parser makes it deterministic." | Types do not define domain validation or ordering. | Validate constraints and sort explicitly. |
| "A mapping containing rows is bounded." | Decorator limits do not truncate nested sequences. | Return a top-level sequence or bound the mapping explicitly. |
| "Wedge can package any Python project." | Its builder needs a uv project with `uv.lock` and a pure-Python wheel closure. | Ship the CLI unpackaged and state the blocker. |

## Completion report

Report the extracted behavior, the command contract, the delivery, and the changed files.
Report the verification results.
When the run did not wedge, report the reason.
Keep local launcher success separate from remote asset availability.

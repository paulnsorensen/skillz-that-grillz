---
name: wedge
description: >-
  Extracts repeatable deterministic work into a bundled skill CLI with wedge.
  Use when the user says "wedge this", "turn this repeated analysis into a CLI",
  "bundle a Python helper with this skill", "use fromargs for this skill CLI",
  or "stop regenerating this script".
  Not for arbitrary Python application packaging or release management.
license: MIT
---

# Wedge a skill CLI

Move repeatable computation into code. Keep interpretation and decisions in the skill.

This skill wraps the `wedge` packaging CLI. `fromargs` is the Python library used by the packaged helper.

## Iron Law

**No bundled CLI without an explicit behavior contract and a verified installed invocation.**

## Find the useful boundary

1. Read the repeated workflow and its existing helpers.
2. Identify fixed transformations: parsing, validation, counting, filtering, sorting, or projection.
3. Leave ambiguous classification, recommendations, and user decisions in prose.
4. Define inputs, output shape, ordering, empty results, error cases, and side effects before editing.
5. Reuse an existing command when it already satisfies that contract.

Input → Output: repeated reading and counting of text records → one `records counts PATH` command returning stable counts.

The benefit is less repeated code generation and less raw output in context.
Do not claim measured token savings without a comparison.

## Check packaging support first

Read [packaging](references/packaging.md) before creating a manifest or running wedge.
Verify the actual builder layout and dependency closure.

The current builder requires a checkout with `lib/fromargs` sources, lockfile, and project metadata.
It does not package arbitrary skill dependencies.
If the target repository lacks these inputs, report the blocker before editing.
Do not silently copy shared libraries, change shared dependencies, or claim that installing wedge fixes the layout.

Resolve this skill's resources from the loaded `SKILL.md` location, not the current working directory.
Keep the installed teaching skill separate from the target skill you edit.

## Implement the smallest command

Read [fromargs](references/fromargs.md) before writing handlers.

Use [the Python template](assets/records.py) for a small typed, read-only CLI.
Use [the manifest template](assets/wedge.toml) only after verifying packaging support.
Copy them into the target skill, then replace the example names, repository, and behavior.
Do not create a manifest for this teaching skill itself.

1. Keep the helper noninteractive with explicit inputs and concise generated help.
2. Validate domain constraints, not only argument types.
3. Sort results explicitly, including tie-breaks.
4. Return data from handlers; let fromargs serialize it.
5. Return a top-level sequence when decorator truncation is appropriate.
6. Use a bounded default and document how callers request complete output.
7. Keep diagnostics off stdout.
8. For writes, define authorization, preview behavior, and retry safety before implementing them.

The template counts nonempty, stripped UTF-8 lines.
It sorts by descending count, then case-sensitive value.
Its output limit does not bound input size or computation.
For large inputs, define and test a separate resource limit.

## Package and prove the installed path

Follow the command sequence in [packaging](references/packaging.md).

1. Test valid, empty, and invalid input through the real CLI.
2. Compare repeated outputs for the same input.
3. Test default truncation and `--full` separately.
4. Generate the launcher and lock with `wedge lock`.
5. Build a local archive and verify it through the installed launcher with `WEDGE_PYZ`.
6. Run that launcher from a directory outside the source checkout.
7. Confirm a modified archive is rejected.
8. Run the project's canonical gate and inspect the final diff.

Ship source, manifest, generated launcher, and lock.
Do not commit or instruct users to copy the `.pyz` into the skill.
Keep publication separate from local verification; publication changes remote release state.

In the target skill, show the launcher path relative to its loaded `SKILL.md`.
Explain when to invoke it, its output contract, and its recovery actions.
Load references only when needed; execute the helper instead of reading its source on every use.

Use [the pressure scenarios](evals/evals.json) when checking changes to this skill.

## Red flags and rationalizations

Stop when you see a copied `scripts/records.pyz`, a handler-owned `--full`, or an assumed dependency installation.

| Rationalization | Why it fails | Required action |
| --- | --- | --- |
| "Copy the archive; it already runs." | This bypasses the launcher and committed lock contract. | Generate the launcher and lock. |
| "The handler needs a full flag." | fromargs reserves `--full` and `--json`. | Use decorator limits and the global flags. |
| "The parser makes it deterministic." | Types do not define domain validation or ordering. | Validate constraints and sort explicitly. |
| "A mapping containing rows is bounded." | Decorator limits do not truncate nested sequences. | Return a top-level sequence or bound the mapping explicitly. |
| "Wedge can package any Python project." | Its builder uses a specific layout and frozen dependency closure. | Check support and report unsupported inputs. |

## Completion report

Report the extracted behavior, command contract, changed files, verification results, and any publication or portability blocker.
Distinguish local launcher success from remote asset availability.

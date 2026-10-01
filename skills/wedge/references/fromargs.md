# The fromargs contract

Read this before implementing or changing a command.
Use the bundled template for executable syntax.

## Inputs and execution

Create `fromargs.App("name", help="...")`.
Register a typed handler with `@app.command` or `@app.command(limit=n)`.
Use `app.run(argv)` to return an integer process status.
A source entry point uses `raise SystemExit(main())`; `main()` returns `app.run()`.

A handler returns data, not an exit status or preformatted JSON.
An integer handler result becomes JSON data and still exits successfully.
Use `None` for successful commands with no stdout.

Use explicit arguments for paths and domain options.
The parser handles types; the handler still validates semantic constraints.
Prefer the standard library and existing helpers.
Do not add a second argument parser.

## Output and errors

| Event | stdout | stderr | Status |
| --- | --- | --- | --- |
| Non-`None` return | One JSON document | Optional truncation note | 0 |
| `None` return | Empty | Empty | 0 |
| Parse failure or default `CliError(message)` | Empty | JSON error | 2 |
| `contract_error(exc, context=...)` | Empty | JSON error | 3 |
| Unexpected failure | Empty | JSON error, with traceback path when available | 1 |

JSON stdout can span multiple lines.
Parse it as one document, not JSON Lines.
Each error is one JSON line containing `error` and `exit_code`.
Plain `note:` lines can also appear on stderr.
Do not assume the entire stderr stream is JSON.

Catch expected boundary failures narrowly.
Use `CliError` for correctable user constraints and `contract_error` for failed external input contracts.
Do not catch every exception and return an empty successful result.

## Global flags and bounded results

`--json` is an accepted no-op; output is already JSON.
`--full` disables decorator truncation.
Both flags are reserved and stripped before parsing, except after the end-of-options marker.
Do not declare either flag in a handler.

`limit=n` truncates only a top-level non-string sequence.
It does not truncate mappings, nested lists, or strings.
For example, `{"rows": many_rows}` remains unbounded.
Return `many_rows` directly or implement an explicit bounded mapping contract.

A decorator limit bounds output, not input reads, memory, or computation.
Specify separate resource controls when the workload requires them.
There are no automatic `--schema`, `--fields`, or `--json-input` interfaces.

## Determinism and repairs

Use explicit sorting and stable tie-breaks before truncation.
Define case handling, normalization, duplicate rules, and empty results.
Do not rely on filesystem enumeration order, locale defaults, random values, or wall-clock time.

Self-healing repairs only a uniquely verified shell-merged argument.
It does not guess ambiguous values or split free-text strings and paths.
Write correct invocations; treat repair notes as diagnostics, not a workflow dependency.

## Worked example

Input → Output: `gouda\nbrie\nedam\nbrie\n` → counts sorted by frequency and value.

With the template copied into a target skill, run it in the target's uv project.
Set `TARGET_PROJECT` to the project that has fromargs as a locked dependency.
In the skillz-that-grillz repository only, use `$REPO_ROOT/lib`.

```bash
uv run --locked --project "$TARGET_PROJECT" python "$SKILL_DIR/records.py" counts "$INPUT"
uv run --locked --project "$TARGET_PROJECT" python "$SKILL_DIR/records.py" counts "$INPUT" --full
uv run --locked --project "$TARGET_PROJECT" python "$SKILL_DIR/records.py" counts "$INPUT" --minimum 0
```

The first call returns brie and edam, plus a truncation note.
The second returns all three values.
The third exits 2 with no stdout.
An empty file returns `[]`; an unreadable or invalid UTF-8 file exits 3.

## Sources

The [fromargs README](https://github.com/paulnsorensen/skillz-that-grillz/blob/main/lib/fromargs/README.md) defines the library contract.
The [Agent Skills script guide](https://agentskills.io/skill-creation/using-scripts) explains explicit inputs, concise help, bounded output, and actionable errors.

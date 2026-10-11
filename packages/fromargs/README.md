# fromargs

`fromargs` is a self-healing, agent-friendly wrapper around
[Cyclopts](https://cyclopts.readthedocs.io/). It composes one `cyclopts.App`,
forces every command's return value to JSON, and repairs the argv mistakes an
LLM agent tends to make, without ever guessing at intent it cannot verify.

## Install

```bash
uv add fromargs
# or
pip install fromargs
```

`fromargs` pins `cyclopts>=5,<6`.

## Quick start

```python
from typing import Annotated

import fromargs

app = fromargs.App("cheese-cave", help="Track wheels of cheese as they ripen.")


@app.command
def age(
    name: str,
    *,
    weeks: Annotated[int, fromargs.Parameter(help="Number of weeks to age.")],
    dry_run: bool = False,
) -> dict[str, object]:
    """Age one wheel for more weeks."""
    if weeks < 1:
        raise fromargs.CliError(f"--weeks must be at least 1, got {weeks}")
    return {"name": name, "weeks": weeks, "dry_run": dry_run}


if __name__ == "__main__":
    app.main()
```

```console
$ python cheese_cave.py age brie --weeks 2
{
  "name": "brie",
  "weeks": 2,
  "dry_run": false
}
```

See `examples/cheese_cave.py` for a fuller example, with a command group and
a truncated list result.

## Output contract

- A handler returns data, not text. A non-`None` return value prints as one
  JSON document on stdout, then the process exits `0`.
- A `None` return value means exit `0` with no stdout.
- A `set` or `frozenset` prints as a sorted JSON list. Items that cannot
  sort together order by their canonical JSON text, so the output stays
  stable. `bytes`, `bytearray`, `memoryview`, and iterators (such as
  generators) raise `TypeError`: return a `str` or a `list` instead.
- Every error is one JSON line on stderr: `{"error": <message>, "exit_code": <n>}`.
- Raise `fromargs.CliError(message)` for exit code `2`. Pass
  `exit_code=n` for a code from `2` to `255`. `CliError` rejects any other
  code with an error that is neither `ValueError` nor `TypeError`.
  Cyclopts reports those as bad input. Exit `0` means success, and exit `1`
  means an unexpected exception.
- Use `fromargs.contract_error(exc, context=...)` to wrap a caught exception
  at exit code `3`. A Cyclopts parse error reports at exit code `2`.
- A converter or validator that raises `ValueError`, `TypeError`, or
  `AssertionError` is a usage error at exit code `2`. Any other exception
  from a converter, a validator, the handler, or the result serialization
  reports at exit code `1`. Its envelope adds a `traceback` key with the
  path of a temporary file. `fromargs` keeps the file, so the caller can
  read it after the process exits.
- `sys.exit(n)` sets the exit status, and `run()` catches it while it
  parses, runs the handler, and prints the result. `0` or `None` exits `0`
  with no output. An `int` from `1` to `255` reports an error envelope at
  that exit code. An `int` outside that range reports
  `exited with status <code>` at exit code `1`. Any other `sys.exit` value
  reports its text at exit code `1`. `KeyboardInterrupt` reports
  `{"error": "interrupted"}` at exit code `130`.
- A quote-split repair (below) prints one plain-text `note:` line on stderr;
  it never changes stdout or the exit code.

## Global flags

`fromargs` strips two flags from argv before Cyclopts ever sees them, from
anywhere before the end-of-options marker:

- `--json` is a no-op. Agents that append it by habit get plain JSON either
  way, so the flag costs nothing and fails nothing.
- `--full` turns off result truncation for the current call.

Neither flag reaches a handler, and neither is a real Cyclopts option.

The rule is literal. Every bare `--json` or `--full` token before the marker
is global, even where a parameter would take a hyphen-leading value (for
example `Parameter(allow_leading_hyphen=True)`). To pass one of these tokens
as data, put it after the marker (`--` by default): `wrap jq -- --json x`.
The `--full=1` and `--json=true` forms are not global flags. Cyclopts
rejects them as unknown options.

## `limit`

`@app.command(limit=n)` truncates a sequence or set result to its first `n`
items, unless the caller passes `--full`. A set truncates after sorting.
Truncation prints a `note:` line on stderr and never applies to a mapping or
a string. `App.default` accepts the same `limit` keyword.

## `App.default`

`@app.default` (bare or called, matching `@app.command`) registers the
handler that runs when argv names no subcommand at that app or group level.
It is rejected at registration if it declares a `json` or `full` parameter,
the same rule `@app.command` enforces. Registering a second default on the
same app or group raises `ValueError`.

## Self-healing

An agent's shell layer sometimes merges two arguments into one quoted token,
for example `--weeks "2 --dry-run"` instead of `--weeks 2 --dry-run`. When
Cyclopts rejects an argv, `fromargs` shell-splits each option's value once
and re-parses. It applies a split only when:

- the split has at least two pieces, and one looks like a flag; and
- the option can take a split value (not a boolean flag, not free-text `str`
  or `Path`); and
- exactly one split candidate among all options parses cleanly.

It prints `note: split quoted argument ... into ...` on stderr when it
applies a repair. `fromargs` refuses to guess when a split is ambiguous
(more than one candidate parses), when the option takes free text, or for
any token after the end-of-options marker (`--` by default). In every
refusal case, the original parse error is reported unchanged.

## Version resolution

`fromargs.App(name)` reports the version of the *calling* module, not the
version of `fromargs` itself. It resolves, in order:

1. an explicit `version=` argument, if the caller passes one;
2. `importlib.metadata.version(...)` for the caller's installed distribution;
3. the caller module's `__version__` attribute;
4. `"0.0.0"`, if none of the above resolve.

`App.group(name, version=..., **cyclopts_kwargs)` forwards every extra
keyword, including `version`, to the nested `cyclopts.App`, so a group can
report its own version independently of the root app.

## Cyclopts options

`App`, `App.group`, and `App.command` forward keyword arguments to Cyclopts.
`run()` owns error output, the exit status, and the result. So these three
raise `ValueError` for a Cyclopts option that `run()` never honors:
`error_formatter`, `result_action`, `suppress_keyboard_interrupt`,
`print_error`, `exit_on_error`, `help_on_error`, and `error_console`.

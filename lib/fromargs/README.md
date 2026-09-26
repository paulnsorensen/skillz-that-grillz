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

`fromargs` pins `cyclopts>=4.25.3,<5`; it does not yet track Cyclopts 5.

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
- Every error is one JSON line on stderr: `{"error": <message>, "exit_code": <n>}`.
  Raise `fromargs.CliError(message)` for exit code `2`, or
  `fromargs.contract_error(exc, context=...)` to wrap a caught exception at
  exit code `3`. An unhandled Cyclopts parse error also reports at exit
  code `2`.
- A quote-split repair (below) prints one plain-text `note:` line on stderr;
  it never changes stdout or the exit code.

## Global flags

`fromargs` strips two flags from argv before Cyclopts ever sees them, from
anywhere before the end-of-options marker:

- `--json` is a no-op. Agents that append it by habit get plain JSON either
  way, so the flag costs nothing and fails nothing.
- `--full` turns off result truncation for the current call.

Neither flag reaches a handler, and neither is a real Cyclopts option.

## `limit`

`@app.command(limit=n)` truncates a sequence result to its first `n` items,
unless the caller passes `--full`. Truncation prints a `note:` line on
stderr and never applies to a mapping or a string. `App.default` accepts the
same `limit` keyword.

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

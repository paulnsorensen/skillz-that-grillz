# Changelog

This file records the notable changes to the `fromargs` distribution.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
A `0.x` minor release can contain breaking changes.

The `publish fromargs` workflow reads the section for the tagged version.
It uses that section as the GitHub release notes.
To release, move the `Unreleased` entries into a new version section with the date.
When the minor version changes, bump the `fromargs` version range in `pyproject.toml` and `lib/pyproject.toml`, and relock.
Then set the same version in `pyproject.toml` and push a `fromargs-v<version>` tag.

## [Unreleased]

## [0.2.0] - 2026-10-04

### Changed

- **Breaking:** require Cyclopts 5 (`cyclopts>=5,<6`).
  Cyclopts 5 removes the v4 fuzzy command fallback.
  A command name that differs only in dashes, underscores, or case no longer resolves.
  It reports an `Unknown command ... Did you mean` error with exit code 2.
- **Breaking:** `CliError` accepts only an `int` `exit_code` from 2 to 255.
  It raises a dedicated `Exception` subclass for 0, 1, an out-of-range code, a `bool`, or a non-`int` code.
  Cyclopts reports a `ValueError` or `TypeError` as bad input at exit 2.
  The dedicated subclass is neither, so a bad code in a converter or validator exits 1 with a traceback.
- **Breaking:** `App`, `App.group`, and `App.command` raise `ValueError` for the
  Cyclopts options that `run()` never honors: `error_formatter`, `result_action`,
  `suppress_keyboard_interrupt`, `print_error`, `exit_on_error`, `help_on_error`,
  and `error_console`.
- A `set` or `frozenset` result prints as a sorted JSON list. Before, it exited 1.
- A `bytes` or iterator result raises `TypeError` with a message that names the fix.
- **Breaking:** `SystemExit` and `KeyboardInterrupt` no longer propagate from `App.run()`.
  `run()` catches them while it parses, runs the handler, and prints the result.
  `sys.exit(n)` sets the exit status with an error line.
  An `int` outside 1 to 255 reports `exited with status <code>` at exit code 1.
  `KeyboardInterrupt` reports exit code 130.
- The `--json` and `--full` flags are literal. Put them after `--` to pass them as data.
- `fromargs` keeps the traceback file after the process exits.

### Fixed

- A converter or validator exception now reports an error line with a `traceback` path.
  Before, it escaped `App.run()`.
  A `ValueError`, `TypeError`, or `AssertionError` is a usage error at exit code 2.
  Any other exception exits 1.
  A quote-split repair candidate that raises such an exception counts as rejected.
- **Breaking:** a `default_parameter` that a command inherits from its app or group now gets
  the reserved `--json`/`--full` check. A reserved flag now raises `ValueError` at registration.
- `--version` resolves the caller's distribution when Python lists it twice
  (for example under `uv run --with`), and under `python -m pkg.cli`.
- A leading `--json` or `--full` no longer hides a nested command's custom
  `end_of_options_delimiter`.

## [0.1.0] - 2026-09-26

### Added

- `fromargs.App`, which composes one `cyclopts.App` and registers commands
  with `@app.command`, `@app.default`, and `app.group(name)`.
- Forced JSON output: a non-`None` return value prints as one JSON document
  on stdout. `None` means exit 0 with no stdout.
- One JSON error line on stderr for every error:
  `{"error": <message>, "exit_code": <n>}`.
- `fromargs.CliError` (exit code 2) and `fromargs.contract_error` (exit code 3).
- An unexpected handler exception reports exit code 1 with a `traceback` file path.
- The global `--json` (no-op) and `--full` (no truncation) flags.
- `limit=n` on `@app.command` and `@app.default` to truncate a sequence result.
- A verified quote-split repair for a shell-merged option value.
- `--version` resolution for the calling distribution, not for `fromargs`.
- Async handlers through `asyncio.run` when the effective backend is asyncio.

[Unreleased]: https://github.com/paulnsorensen/skillz-that-grillz/compare/fromargs-v0.2.0...HEAD
[0.2.0]: https://github.com/paulnsorensen/skillz-that-grillz/compare/fromargs-v0.1.0...fromargs-v0.2.0
[0.1.0]: https://github.com/paulnsorensen/skillz-that-grillz/releases/tag/fromargs-v0.1.0

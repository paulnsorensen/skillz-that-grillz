# Changelog

This file records the notable changes to the `fromargs` distribution.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
A `0.x` minor release can contain breaking changes.

The `publish fromargs` workflow reads the section for the tagged version.
It uses that section as the GitHub release notes.
To release, rename `Unreleased` to the new version and date.
Then set the same version in `pyproject.toml` and push a `fromargs-v<version>` tag.

## [Unreleased]

### Changed

- **Breaking:** require Cyclopts 5 (`cyclopts>=5,<6`).
  Cyclopts 5 removes the v4 fuzzy command fallback.
  A command name that differs only in dashes, underscores, or case no longer resolves.
  It reports an `Unknown command ... Did you mean` error with exit code 2.
- **Breaking:** `CliError` accepts only an `int` `exit_code` from 2 to 255.
  It raises `ValueError` for 0, 1, or an out-of-range code.
  It raises `TypeError` for a `bool` or a non-`int` code.
- **Breaking:** `App`, `App.group`, and `App.command` raise `ValueError` for the
  Cyclopts options that `run()` never honors: `error_formatter`, `result_action`,
  `suppress_keyboard_interrupt`, `print_error`, `exit_on_error`, `help_on_error`,
  and `error_console`.
- A `set` or `frozenset` result prints as a sorted JSON list. Before, it exited 1.
- A `bytes` or iterator result raises `TypeError` with a message that names the fix.
- `sys.exit(n)` in a handler sets the exit status with an error line, and
  `KeyboardInterrupt` reports exit code 130. Before, both escaped `App.run()`.

### Fixed

- A converter or validator exception other than `CliError` now reports the
  exit code 1 error line with a `traceback` path. Before, it escaped `App.run()`.
  A quote-split repair candidate that raises such an exception counts as rejected.
- A `default_parameter` that a command inherits from its app or group now gets
  the reserved `--json`/`--full` check.
- `--version` resolves the caller's distribution when Python lists it twice
  (for example under `uv run --with`), and under `python -m pkg.cli`.
- A leading `--json` or `--full` no longer hides a nested command's custom
  `end_of_options_delimiter`.

### Documentation

- The `--json` and `--full` flags are literal. Put them after `--` to pass them as data.
- `fromargs` keeps the traceback file after the process exits.

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

[Unreleased]: https://github.com/paulnsorensen/skillz-that-grillz/compare/fromargs-v0.1.0...HEAD
[0.1.0]: https://github.com/paulnsorensen/skillz-that-grillz/releases/tag/fromargs-v0.1.0

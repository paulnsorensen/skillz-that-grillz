# Changelog

This file records notable changes to the `skillz` skill and the repository.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The repository tags follow [Semantic Versioning](https://semver.org/).

Each package keeps its own history:

- [`fromargs`](packages/fromargs/CHANGELOG.md)
- [`agent-session-analytics`](packages/agent-session-analytics/CHANGELOG.md)

<!-- markdownlint-configure-file { "MD024": { "siblings_only": true } } -->

## [Unreleased]

## [0.4.0] - 2026-10-25

This release turns the repository into a library repository. It publishes one
Agent Skill, `skillz`, and the `wedge` GitHub Action.

### Added

- The `skillz` skill with the modes `add`, `improve`, and `audit` (#102).
  `improve` runs or reuses an audit, then asks which findings to apply (#155).
- The `/skillz wedge <path>` mode. It finds work that a CLI can do, writes a
  contract for each candidate, and builds the selected CLIs with `fromargs`
  (#114, #158). The `@new-cli` target scaffolds a new `fromargs` CLI (#198).
- The `/skillz autoimprove` mode. It tests candidate edits against approved
  cases and gates the winner on an unseen holdout (#118, #161).
- Multi-turn journey evals for `autoimprove` (#203). `autoimprove` also seeds
  its cases from `evals/evals.json` (#200, #212).
- A free host doctor that finds host problems before the first question, and
  an opt-in `--isolation nono` backend on Linux (#173).
- The options `run --judge-model` (#257) and `run` effort and sandbox read
  roots for Claude (#170).
- The run summary reports tokens and latency for each role (#261). It also
  reports the runner, export, and improvement claims separately (#328).
- A `validate` command, generated schemas, and an eval formats page (#382).
- The `agent-session-analytics` package on PyPI. The `skillz` skill bundles
  it (#105, #216). See its
  [changelog](packages/agent-session-analytics/CHANGELOG.md).
- The `wedge` GitHub Action at `actions/wedge`. It checks and publishes
  Wedge-packaged skill CLIs from any repository (#93).
- `wedge` builds, locks, and publishes many skills in one command, with
  `--jobs` and a shared site layer (#100, #195).
- This changelog, a rewritten README, and a docs guide (#544).

### Changed

- **Breaking:** `/skillz autoimprove` is now one resumable
  `skillz-experiment run` and a write-only `export`. It replaces the
  six-command flow. The Claude run reuses your login, so no token setup is
  needed (#161).
- **Breaking:** the `wedge` lock field `sha256` is now `content_sha256`, and
  asset names contain the content digest. Run `wedge lock` again to
  regenerate consumer locks (#97).
- **Breaking:** `wedge build` and `wedge lock` print `{name: {...}}`. The
  single-skill output was flat (#100).
- **Breaking:** `fromargs` 0.2.0 hardens the library and moves to
  Cyclopts 5. See the [`fromargs` changelog](packages/fromargs/CHANGELOG.md)
  (#96, #121).
- `/skillz` is user-only. "wedge this" no longer invokes it on its own. Type
  `/skillz wedge <path>` (#158).
- The installer reads the default branch, `dist`. CI writes `dist` after each
  merge to `main`. It holds the `main` tree plus the built `scripts/*.pyz`
  archives. `main` holds source only (#517).
- The default analytics database path is now
  `<cache>/agent-session-analytics/sessions.duckdb`. A legacy file stays in
  use when the new file does not exist (#545).

### Removed

- **Breaking:** the skills `release`, `justfile`, `prek`, `oss-hygiene`,
  `safe-settings`, and `github-copilot-repo-instructions` moved to
  [git-gouda](https://github.com/paulnsorensen/git-gouda) (#104).
- **Breaking:** the skills `gh`, `file-handler`,
  `github-copilot-personal-instructions`, and `respond` are retired. Use the
  `gh` CLI directly for GitHub plumbing.
  [easy-cheese](https://github.com/paulnsorensen/easy-cheese) `/plate` covers
  commit, push, and PR creation. Its `/affinage` covers PR review-comment
  triage (#104).
- **Breaking:** the skills `chezmoi`, `bash-shortening`, and `gh-bootstrap`
  moved to a private dotfiles repository (#85).
- **Breaking:** the installer script `scripts/install.sh` is gone (#104).
- The skills `serena-config`, `ralphify-spec`, and `copilot` (#84).

The repo-local skill `.agents/skills/python-authoring/` remains. It is not
published and applies only to work on this repository.

### Fixed

- Harness runs work on current Claude Code and Codex CLIs (#220).
- The Claude live preflight and `just build` pass on macOS (#210, #514).
- The `ingest` log-root defaults expand `~` (#184).
- A run stops when its `--out` directory is inside the `--target` skill
  (#520).
- Credential redaction covers dotted, multiply escaped, and Unicode-quoted
  keys (#521).
- The Gemini sandbox child stays in the wrapper process group (#492).
- `export` writes no empty patch when the winner equals the baseline (#354).
- `wedge` reuses a verified site layer and links it into each build (#524).

## [0.3.0] - 2026-07-18

### Added

- The agent gate suppresses the exit banner of `just`.

### Removed

- **Breaking:** the `commit` and `pr-stack` skills. easy-cheese `/plate`
  replaces them.

See the [v0.3.0 release](https://github.com/paulnsorensen/skillz-that-grillz/releases/tag/v0.3.0).

## [0.2.1] - 2026-06-13

### Fixed

- The release workflow creates the release directly instead of calling
  `gh skill publish --tag`.

See the [v0.2.1 release](https://github.com/paulnsorensen/skillz-that-grillz/releases/tag/v0.2.1).

Earlier tags (v0.0.1 to v0.2.0) appear on the
[releases page](https://github.com/paulnsorensen/skillz-that-grillz/releases).

[Unreleased]: https://github.com/paulnsorensen/skillz-that-grillz/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/paulnsorensen/skillz-that-grillz/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/paulnsorensen/skillz-that-grillz/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/paulnsorensen/skillz-that-grillz/compare/v0.2.0...v0.2.1

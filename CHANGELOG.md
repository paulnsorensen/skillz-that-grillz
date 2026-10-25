# Changelog

This file records notable changes to the `skillz` skill and the repository.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The repository tags follow [Semantic Versioning](https://semver.org/).

Each package keeps its own history:

- [`fromargs`](packages/fromargs/CHANGELOG.md)
- [`agent-session-analytics`](packages/agent-session-analytics/CHANGELOG.md)

## [Unreleased]

### Changed

- The former `/wedge` skill is now `/skillz wedge`. `/skillz` is user-only,
  so "wedge this" no longer invokes it on its own. Type `/skillz wedge <path>`.
- The installer reads the default branch, `dist`. CI writes `dist` after each
  merge to `main`. It holds the `main` tree plus the built `scripts/*.pyz`
  archives. `main` holds source only.

### Moved and retired

This repository no longer publishes other Agent Skills under `skills/`:

- The repository setup and maintenance skills (`release`, `justfile`, `prek`,
  `oss-hygiene`, `safe-settings`, `github-copilot-repo-instructions`) moved to
  [git-gouda](https://github.com/paulnsorensen/git-gouda).
- `gh`, `file-handler`, `github-copilot-personal-instructions`, and `respond`
  are retired, not moved. Use the `gh` CLI directly for GitHub plumbing.
  [easy-cheese](https://github.com/paulnsorensen/easy-cheese) `/plate` covers
  commit, push, and PR creation. Its `/affinage` covers PR review-comment triage.

The repo-local skill `.agents/skills/python-authoring/` remains. It is not
published and applies only to work on this repository.

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

[Unreleased]: https://github.com/paulnsorensen/skillz-that-grillz/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/paulnsorensen/skillz-that-grillz/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/paulnsorensen/skillz-that-grillz/compare/v0.2.0...v0.2.1

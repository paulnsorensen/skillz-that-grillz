# 🧀 skillz-that-grillz 🧀

[![CI](https://img.shields.io/github/actions/workflow/status/paulnsorensen/skillz-that-grillz/validate.yml?branch=main&label=CI&style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/github/license/paulnsorensen/skillz-that-grillz?style=flat-square)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/paulnsorensen/skillz-that-grillz?style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/releases/latest)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow?style=flat-square)](https://www.conventionalcommits.org)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/pulls)

> _Library and tooling home: wedge, fromargs, and the wedge GitHub Action._

This repository publishes no Agent Skills. It hosts two Python libraries under
`lib/` — `wedge` (packages a skill CLI as a content-addressed `.pyz`) and
`fromargs` (the CLI library wedge builds on) — plus the public
[`actions/wedge`](actions/wedge/README.md) GitHub Action that runs `wedge` in
a consumer repository.

The companion repo [easy-cheese](https://github.com/paulnsorensen/easy-cheese)
covers the design / implement / review workflow (mold, cook, press, age, cure)
and local-to-review publication (`/plate`: staging, commits, pushes, and PR
creation — single or stacked).

## Where the skills went

This repo used to publish Agent Skills under `skills/`. It no longer does:

- Repository setup and maintenance skills (`release`, `justfile`, `prek`,
  `oss-hygiene`, `safe-settings`, `github-copilot-repo-instructions`) moved to
  [git-gouda](https://github.com/paulnsorensen/git-gouda).
- `gh`, `file-handler`, `github-copilot-personal-instructions`, and `respond`
  are retired, not moved. Use the `gh` CLI directly for GitHub plumbing;
  easy-cheese's `/plate` covers commit/push/PR creation and `/affinage`
  covers PR review-comment triage.

`.agents/skills/python-authoring/` remains: a repo-local skill (not
published) that only applies to work on this repository, mirrored at
`.claude/skills/python-authoring/` via a symlink.

## wedge

`wedge` (`lib/src/wedge/`) shivs a `fromargs` CLI into a skill as a
content-addressed `.pyz`. See [`lib/README.md`](lib/README.md) for the CLI
and [`docs/agents/decisions/wedge-skill-packaging.md`](docs/agents/decisions/wedge-skill-packaging.md)
for the design decisions.

## wedge GitHub Action

[`actions/wedge`](actions/wedge/README.md) packages a pure-Python skill CLI
as a content-addressed `.pyz` release asset in your own repository. Use it to
check locks on pull requests and to publish assets after a merge:

```yaml
- uses: paulnsorensen/skillz-that-grillz/actions/wedge@<full-commit-sha>
  with:
    command: publish # or: check
    roots: skills
```

## fromargs

`fromargs` (`lib/fromargs/`) is the CLI argument-parsing library `wedge`
builds its CLI on. See [`lib/fromargs/README.md`](lib/fromargs/README.md).

## Development

```sh
git clone https://github.com/paulnsorensen/skillz-that-grillz.git
cd skillz-that-grillz
just build   # runs all formatters, linters, and test suites
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full contribution guide.

## License

MIT — see [LICENSE](LICENSE).

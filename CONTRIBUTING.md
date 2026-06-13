# Contributing to skillz-that-grillz

Thanks for your interest. Contributions of all sizes are welcome — a
typo fix is just as useful as a feature. This document describes how
to get from "I want to help" to "my change is merged".

## Filing issues

- Search [open issues](https://github.com/paulnsorensen/skillz-that-grillz/issues)
  before opening a new one.
- Use the bug-report or feature-request template.
- For security vulnerabilities, do **not** open a public issue — see
  [`SECURITY.md`](./SECURITY.md).

## Setting up locally

```sh
git clone https://github.com/paulnsorensen/skillz-that-grillz.git
cd skillz-that-grillz
just build   # runs all formatters, linters, validators, and test suites
```

`just build` is the only setup step — it installs nothing globally and uses
`uv` to run the Python validators. Install [`just`](https://github.com/casey/just)
first if you don't have it (`brew install just` / `cargo install just`).

## Adding a skill

Skills live under `skills/<name>/` with the layout described in the
[`## Skill layout` section of `README.md`](README.md#skill-layout). The full
authoring contract — frontmatter keys, description-writing guidance, and the
validation rules CI enforces — is in
[`.github/instructions/skills.instructions.md`](.github/instructions/skills.instructions.md).
Read it before opening a skill PR.

The essentials:

- One directory per skill: `skills/<name>/SKILL.md` (plus optional
  `references/`, `scripts/`, `assets/`).
- `SKILL.md` needs YAML frontmatter with at least `name` and `description`, and
  **`name` must match the parent directory name** — the validator fails
  otherwise.
- Add the new skill to the [`## Skills` table in `README.md`](README.md#skills);
  it is the single source of truth for the skill list.
- Run `just build` until it reports 0 failures (this runs
  `validate_skills.py`).

## Running tests

This project has one canonical quality gate:

```sh
just build   # autofix, then verify markdown/YAML/shell + run the test suites
just ci      # the same gate with no autofixes — exactly what CI runs
```

Please run `just build` and verify 0 failures before opening a PR.

## Submitting a pull request

1. Fork the repo and create a topic branch from `main`.
2. Make your change. Keep commits focused; one concern per commit is
   easier to review than a kitchen-sink commit.
3. Use [Conventional Commits](https://www.conventionalcommits.org)
   for the PR title (e.g. `feat: add X`, `fix: handle Y`,
   `docs: explain Z`). Squash-merge will use the PR title as the
   commit subject.
4. Fill out the PR template — the "why" matters more than the "what".
5. Wait for CI to go green and address review feedback.

## Code of Conduct

Participation in this project is governed by the
[Contributor Covenant](./CODE_OF_CONDUCT.md). By contributing you
agree to abide by it.

## Licensing

By submitting a contribution you agree that it will be licensed under
the same terms as the project itself (see [`LICENSE`](./LICENSE)).

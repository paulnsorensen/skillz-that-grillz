# Copilot review instructions — skillz-that-grillz

This repo is a library and tooling collection: `wedge` and `fromargs` under
`lib/`, and the public `actions/wedge` GitHub Action. It publishes no Agent
Skills (see the README's "Where the skills went" section). Review focuses on
**code quality and test coverage** for `lib/` and the Action, not skill
design.

The one exception is `.agents/skills/python-authoring/`, a repo-local skill
(not published) that only applies to work on this repository. Review changes
to it the way the `/skill-creator` skill would: care about whether it will
*trigger* when it should, whether it teaches the model *why* not just
*what*, and whether bundled resources earn their keep.

## What this repo is and isn't

- **Is**: a Python library repo (`lib/wedge`, `lib/fromargs`) and a public
  composite GitHub Action (`actions/wedge`) that packages a skill CLI as a
  content-addressed `.pyz` for other repositories to consume.
- **Isn't**: an agent framework, an orchestrator, an MCP server, or a skills
  collection. No required MCPs.

Flag scope creep the same way you would in any library repo: new
abstractions without a demonstrated need, or coupling between `wedge` and
`fromargs` beyond the documented dependency (see
`docs/agents/decisions/wedge-skill-packaging.md` ADR-006).

## Skill review priorities (in order)

Apply the path-scoped instructions in `.github/instructions/` for the
specifics. The summary order:

1. **Triggering** — does the description tell Claude when to use this skill,
   in language users actually type?
2. **Progressive disclosure** — is `SKILL.md` lean? Does deeper material live
   in `references/`, `scripts/`, `assets/` instead of inline?
3. **Why over what** — does the skill explain reasoning, or just bark MUSTs?
4. **Bundled resources earn their keep** — every `references/<file>.md` and
   `scripts/<file>` should have a clear pointer from `SKILL.md` telling the
   model when to load or run it.
5. **Anti-overfit** — instructions generalize across realistic prompts, not
   just the examples baked into the skill itself.

## What not to flag

- Cheese / Dune / Mad Max flavor in user-facing docs. It's intentional repo
  voice. Commit messages and YAML frontmatter stay neutral.
- Backward-compat shims, deprecation paths, migration plans — this repo is
  early-stage and treats every release as the new baseline.
- Style nits already covered by the validators (`markdownlint`, `yamllint`,
  `validate_skills.py`). CI catches those; reviewers should focus on
  skill-design issues that linters miss.

## How to write review comments

- Lead with user impact: "this description would miss users who say X".
- Quote the smallest section of the skill that needs changing.
- Suggest the rewrite, don't just diagnose.
- If a finding is speculative ("might confuse some users"), say so — don't
  inflate it into a blocker.

# Copilot review instructions — skillz-that-grillz

This repo is a skills-only collection following the
[Agent Skills spec](https://agentskills.io/specification). Every change either
adds, edits, or supports a skill under `skills/<name>/`. There is no
production runtime: review focuses on **skill quality**, not application
behavior.

When reviewing a PR, treat it the way the `/skill-creator` skill would: care
about whether the skill will *trigger* when it should, whether it teaches the
model *why* not just *what*, and whether bundled resources earn their keep.

## What this repo is and isn't

- **Is**: self-contained `SKILL.md` files for everyday plumbing (commit, gh,
  pr-stack, justfile, prek). Each skill wraps a single CLI (pr-stack wraps
  whichever of `gt` / `gh stack` the user has installed).
- **Isn't**: an agent framework, an orchestrator, an MCP server. No required
  MCPs. Skills compose freely; they do not call each other implicitly.

Keep this scope in mind — flag scope creep (orchestration logic, intent
classification, multi-CLI fan-out) inside an individual skill. Cross-skill
handoffs belong in the README's "Suggested flow", not in skill bodies.

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

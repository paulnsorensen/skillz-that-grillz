---
name: github-copilot-personal-instructions
model: haiku
allowed-tools: Read, Glob
description: >
  Add or audit GitHub Copilot **personal** custom instructions — the per-user
  prompt configured at github.com/copilot that applies to every Copilot Chat
  conversation that account has on github.com. Covers where to set them, what
  they do and don't apply to (github.com Chat only — not IDE, not mobile, not
  coding agent), how they merge with repository and organization instructions,
  and verification. Use when the user says "set personal Copilot instructions",
  "configure my Copilot", "Copilot keeps responding in the wrong language",
  "make Copilot use my preferred tone", "personal Copilot prompt", "user-level
  Copilot config", "Copilot is ignoring my preferences", or invokes
  /github-copilot-personal-instructions. Do NOT use for repository-scoped
  instructions — that is /github-copilot-repo-instructions.
license: MIT
---

# github-copilot-personal-instructions

Per-user Copilot Chat preferences set on github.com. Applies to **every**
repository that account uses Copilot Chat against, but only on github.com.

---

## Where they live

1. Open [github.com/copilot](https://github.com/copilot).
2. Click your **profile picture** in the bottom-left.
3. Choose **Personal instructions**.
4. Click the **lightbulb icon** for templates, or paste your own markdown.
5. Click **Save**.

Active immediately. They persist until edited or removed.

---

## Where they apply

| Surface | Applies? |
|---|---|
| Copilot Chat on github.com | Yes |
| Copilot Chat on Mobile | No |
| Copilot Chat in IDE (VS Code, JetBrains, Visual Studio, Xcode, Eclipse) | No |
| Copilot code review | No |
| Copilot coding agent (cloud agent) | No |

**Implication:** for IDE chat or the coding agent to follow the same
guidance, mirror the relevant rules into repository instructions
(`/github-copilot-repo-instructions`).

---

## Precedence

All three layers — when present — are merged and sent to Copilot together.
Stated priority order (highest → lowest):

1. **Personal** (this skill).
2. **Repository** (`/github-copilot-repo-instructions`).
3. **Organization**.

Conflicts are not auto-resolved. If a repo's guidance contradicts personal
preferences, Copilot sees both. To debug, temporarily clear one layer.

---

## What pulls its weight

Personal instructions are best for things that are **about you**, not about
the project:

- Response language: `Always respond in Spanish.`
- Tone and verbosity: `Use a collegial tone. Keep explanations brief but
  include enough context to understand the code.`
- Default code style for examples: `Always provide examples in TypeScript.`
- Prior knowledge: `Assume I'm comfortable with Rust ownership; skip the
  basics.`
- Format preferences: `Prefer fenced code blocks over inline snippets.`

Don't put project conventions here — those go in repository instructions so
your whole team benefits.

---

## Verification

There's no surfaced UI signal beyond "saved". To verify they're applied:

1. Save a distinctive instruction (e.g. `Always end responses with the word
   "verified".`).
2. Ask Copilot Chat on github.com any question.
3. Confirm the marker appears in the reply.
4. Remove or restore the real instruction.

If the marker doesn't appear, the most likely cause is surface mismatch —
you're testing in the IDE, mobile, or against the coding agent.

---

## Audit checklist

When the user says "audit my personal Copilot config":

1. Walk them to github.com/copilot → profile pic → **Personal instructions**.
2. Read out the current text and check:
   - Is anything project-specific? Hoist to repo instructions.
   - Does anything contradict known repo or org instructions?
   - Is anything secret or sensitive? Treat the textbox as moderately
     sensitive — no tokens, API keys, or internal URLs.
3. If the user reports "Copilot ignores my preferences", check **which
   surface** they're using — IDE/mobile/coding-agent will not honor personal
   instructions.

This skill cannot read or edit personal instructions programmatically — they
live in GitHub user settings, not in a repo. The skill walks the user through
the UI.

---

## Rules

- **github.com Chat only.** Don't promise IDE behavior.
- **Don't store secrets.** No tokens, no API keys, no internal URLs.
- **Keep them about you, not the project.** Project guidance belongs in
  `/github-copilot-repo-instructions`.
- **No documented length limit**, but the same "be concise" rule applies —
  long prompts crowd out the actual question.

---

## Gotchas

- IDE chat reads repository instructions but **not** personal — a common
  surprise when developers edit personal instructions and see no IDE change.
- Personal instructions can mask repo guidance. If a teammate says "Copilot
  follows our rules" and you say "no it doesn't", check whether your personal
  instructions contradict the repo file.
- Mobile and IDE Copilot Chat are separate apps from github.com Chat — same
  brand, different config surface.
- Copilot coding agent runs from issues and does not consult personal
  instructions; expect repo instructions to be the only knob there.
- Saving an empty Personal instructions box clears them. Keep a copy
  somewhere else if your prompt is non-trivial.

---

## What this skill is not

- Not repository-scoped — see `/github-copilot-repo-instructions`.
- Not org-scoped — those are configured by org admins in GitHub Enterprise
  settings.
- Not IDE settings — VS Code's `github.copilot.chat.*` settings are separate
  and outside this skill.

---

## Source

- [Add personal custom instructions for GitHub Copilot](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/add-custom-instructions/add-personal-instructions)

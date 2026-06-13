---
name: github-copilot-repo-instructions
model: haiku
allowed-tools: Read, Write, Edit, Glob, Bash(ls:*), Bash(cat:*), Bash(git:*), Bash(mkdir:*), Bash(gh:*)
description: >
  Add, audit, or fix GitHub Copilot repository custom instructions —
  `.github/copilot-instructions.md` (repo-wide) and
  `.github/instructions/*.instructions.md` (path-specific) — so Copilot Chat,
  Copilot code review, and the Copilot coding agent all pick up project-wide
  guidance. Also covers `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` as alternate
  agent instruction files. Use when the user says "add Copilot instructions",
  "set up copilot-instructions.md", "audit our Copilot config", "make Copilot
  follow our conventions", "configure Copilot code review", "add path-specific
  Copilot rules", "applyTo glob", "excludeAgent", "Copilot is ignoring our
  guidance", or invokes /github-copilot-repo-instructions. Do NOT use for
  personal (per-user) Copilot instructions — that is
  /github-copilot-personal-instructions. Do NOT use for opinionated generation
  aligned with this project's opinionated engineering principles — that is the
  setup mode of the multi-mode Copilot driver, `/copilot setup` (if installed).
license: MIT
---

# github-copilot-repo-instructions

Repository-scoped Copilot guidance. Markdown files in `.github/` that GitHub
Copilot reads on every request against the repo.

> Generic, doc-faithful. For an opinionated generator that bakes in specific
> engineering principles, see the `setup` mode of `/copilot` — `/copilot setup`
> (if installed).
>
> For the multi-mode Copilot driver (review / delegate / setup), see `/copilot`
> (if installed) — this skill only writes and audits the instruction files.

---

## File layout

| File | Scope | Frontmatter | Notes |
|---|---|---|---|
| `.github/copilot-instructions.md` | Repo-wide, every request | None | Single file. Keep ≤ 2 pages. |
| `.github/instructions/<name>.instructions.md` | Path-glob | **Required** (`applyTo`) | Multiple files allowed. Filename must end `.instructions.md`. |
| `AGENTS.md` | Nearest-wins | None | Place anywhere in the tree; the file closest to the edited path wins. |
| `CLAUDE.md` | Repo root only | None | Single file at root. |
| `GEMINI.md` | Repo root only | None | Single file at root. |

The repo-wide file and path-specific files **combine** when a path matches —
both sets of instructions are sent to Copilot together.

---

## Frontmatter (path-specific files only)

```markdown
---
applyTo: "**/*.ts, **/*.tsx"
excludeAgent: "code-review"
---

- Prefer `interface` over `type` for object shapes.
- Use named exports.
```

| Field | Required | Values |
|---|---|---|
| `applyTo` | Yes | Glob pattern, or comma-separated globs (`*.py`, `src/**/*.rb`, `**/subdir/**/*.py`). |
| `excludeAgent` | No | `"code-review"` or `"cloud-agent"` — hides this file from that surface. |

---

## Surfaces

Copilot reads repo instructions across these surfaces:

- **Copilot Chat** — `github.com/copilot` (with repo attached) and IDE chat.
- **Copilot code review** — PR reviews. Toggleable per-repo. Reads from the
  **base branch**, not the feature branch.
- **Copilot coding agent (cloud agent)** — issue-driven implementations.

Path-specific files (`.github/instructions/*.instructions.md`) are fully
supported on github.com for code-review and the cloud agent. IDE support for
path-specific files lags — repo-wide file is the safer bet for IDE coverage.

---

## Precedence

When multiple instruction sets exist, all are merged and sent to Copilot.
Stated priority order (highest → lowest):

1. **Personal** instructions (per-user, github.com Chat only).
2. **Repository** instructions (this skill).
3. **Organization** instructions.

Conflicts are not auto-resolved — Copilot sees them all. If quality drops,
temporarily disable a set rather than fighting overlap.

---

## Protocol

### Add repo-wide instructions

1. `mkdir -p .github`
2. Write `.github/copilot-instructions.md`. Markdown only, no frontmatter.
3. Keep it ≤ 2 pages. Whitespace is ignored, so use bullets liberally.
4. Content that pulls weight: stack summary, build/test commands, layout map,
   non-obvious conventions. Skip generic advice Copilot already knows.
5. Commit on the **base branch** (usually `main`) — code review reads from base.

### Add path-specific instructions

1. `mkdir -p .github/instructions`
2. Create `<name>.instructions.md` with `applyTo` frontmatter.
3. One file per scope (e.g. `python.instructions.md`, `frontend.instructions.md`).
4. Use `excludeAgent` to keep review-only or coding-agent-only guidance from
   leaking into the other surface.

### Enable Copilot code review

Repo Settings → **Code & automation** → **Copilot** → **Code review** →
toggle **"Use custom instructions when reviewing pull requests"**.

Default is enabled, but confirm — orgs sometimes flip it off.

### Enable automatic Copilot review on every PR

Two paths — pick one. Both create a branch ruleset; the API path is scriptable.

**UI** — Settings → **Rules** → **Rulesets** → **New branch ruleset**.
Under "Branch rules", select **Automatically request Copilot code review**.
Two sub-toggles: *Review new pushes* (re-review on each push, burns premium
requests) and *Review draft pull requests*.

**API** (`gh`) — the rule type is `copilot_code_review`. It is **not** listed
on the [rulesets schema page](https://docs.github.com/rest/repos/rules#create-a-repository-ruleset)
as of writing, but the endpoint accepts it. Targets work like any other ruleset.

```bash
gh api repos/<owner>/<repo>/rulesets -X POST --input - <<'JSON'
{
  "name": "copilot-auto-review",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    {
      "type": "copilot_code_review",
      "parameters": {
        "review_on_push": false,
        "review_draft_pull_requests": false
      }
    }
  ]
}
JSON
```

Defaults shown are conservative: review once on PR open, skip drafts.
Flip `review_on_push` to `true` to re-review on every push — useful for
agentic workflows, expensive on premium-request quota.

Requires Copilot Business/Enterprise on the org, or Copilot Pro for personal
repos. The ruleset still installs without a license; reviews just don't fire.

For the full knob inventory across repo / org / per-PR — including what
Copilot review **cannot** do (no "request changes" mode, no severity
threshold, no API re-request) — read `references/code-review-knobs.md`.

### Verify Copilot is reading the file

1. Open `github.com/copilot` and attach the repo.
2. Ask any Copilot Chat question.
3. Expand the **References** list at the top of the response.
4. `.github/copilot-instructions.md` should appear; click to confirm it's the
   version you expect. Path-specific files appear when relevant paths are
   mentioned or attached.

For PR review verification, look at the review comment metadata or trigger a
review on a PR that touches a glob covered by a path-specific file.

---

## Audit checklist

When the user says "audit our Copilot config":

1. `ls -la .github/copilot-instructions.md .github/instructions/ AGENTS.md CLAUDE.md GEMINI.md 2>/dev/null` (silence missing files).
2. Read each present file and check:
   - Repo-wide file ≤ 2 pages.
   - Every path-specific file has `applyTo` frontmatter.
   - Filenames end `.instructions.md` (path-specific only).
   - `excludeAgent` values are exactly `"code-review"` or `"cloud-agent"`.
   - No instructions baked into the repo-wide file that should be path-scoped
     (e.g. "always add type hints" — that's Python-only, hoist it).
3. Check the base branch in `git log` — recent edits to instructions on a
   feature branch won't be seen by code review until merged.
4. Flag conflicts with org-level or expected personal instructions.

---

## Rules

- **Repo-wide file: no frontmatter.** GitHub does not document frontmatter on
  `.github/copilot-instructions.md`. Don't add `applyTo` there.
- **Path-specific file: frontmatter is mandatory.** Without `applyTo` the file
  is ignored.
- **Don't put task-specific instructions in either** ("fix bug X", "do
  refactor Y") — they're long-lived configuration, not ticket scope.
- **Don't duplicate org instructions.** If your org already says "use
  Conventional Commits", don't repeat it at repo level.
- **Code review reads base branch.** PR-only changes to instructions don't
  affect that PR's own review.
- **Don't include secrets, tokens, or internal URLs.** These files are part
  of the repo — anyone with read access sees them.

---

## Gotchas

- A path-specific file with malformed YAML frontmatter is silently dropped.
  Validate frontmatter before committing.
- If a glob isn't matching as expected, prefix with `**/` to cover nested
  paths (`**/*.py` covers nested files; `*.py` may only cover the root,
  depending on the glob engine).
- The IDE may cache instructions; reload the window after editing.
- `AGENTS.md` is recursive — a deeply nested `AGENTS.md` overrides a higher
  one for files near it. Useful, but confusing if the user expects merging.
- `CLAUDE.md` and `GEMINI.md` are *also* read by Copilot now, even though they
  originated as other-vendor files. If you maintain both, expect Copilot to
  see all three.
- Personal instructions take precedence over repo guidance for that one
  user on github.com Chat — symptoms look like "Copilot ignores our rules
  for Alice but works for everyone else". Check
  `/github-copilot-personal-instructions`.
- The repo-wide file's only documented size guidance is "≤ 2 pages". Treat
  that as the ceiling — long instructions crowd out the actual question.

---

## What this skill is not

- Not a setup wizard — it advises and edits, does not interview.
- Not the same as `/copilot setup` (if installed) — that mode of the `/copilot`
  driver bakes in a specific style.
- Not personal instructions — see `/github-copilot-personal-instructions`.
- Not org-level instructions — those are configured in GitHub Enterprise
  org settings, outside any repo.

---

## Source

- [Add repository custom instructions for GitHub Copilot](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/add-custom-instructions/add-repository-instructions)

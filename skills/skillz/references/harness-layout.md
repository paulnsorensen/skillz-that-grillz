# Cross-harness skill layout

Checked: 2026-10-05. The self-update queried every source in `## Sources` on that date; per-source dates are in that table.
Read when the Portability lens fires or in `add`. Repository maintenance also uses this reference.

## Layout

- One level deep: `skills/<name>/SKILL.md`. The directory name is the command name on every host; keep `name:` equal to it.
- Sidecars: `references/` (read on a trigger), `scripts/` (dependency-free), `assets/`, and `agents/openai.yaml` (Codex policy).
- Deploy targets: Claude `~/.claude/skills`, OMP `~/.omp/agent/skills`, and `~/.agents/skills` for Codex, Pi, and Zed. OMP also reads `.agents` through its `agents` provider. `npx skills add -g` installs to `~/.codex/skills`, `~/.cursor/skills`, and `~/.copilot/skills` for Codex, Cursor, and Copilot. The Codex docs list `~/.agents/skills`, not `~/.codex/skills`; whether Codex reads the latter is unverified. A dotfiles sync may drive all trees from one selection list, which is then the single source of truth.
- OMP scans providers non-recursively. The higher-precedence skill keeps the bare name (native `.omp` > Claude > Codex/`.agents`). A differing lower-precedence skill stays reachable as `<namespace>/<name>`. An identical copy collapses.
- Zed's own agent reads `~/.agents/skills` and `<worktree>/.agents/skills` (flat layout, trusted worktrees only). External agents inside Zed use their native skill trees; Zed Skills do not apply to them. Nothing extra is needed for Zed.
- Pi reads `~/.agents/skills` and `.agents/skills`, and discovers `SKILL.md` recursively; commands render as `/skill:<name>`. The Pi path `~/.pi/agent/skills` is unverified: the current Pi page does not name it.
- A dotfiles sync that vendors skills from source repos copies them after its local selection, so a vendored skill with a local name overwrites it. Remove the name from one side, or give that source an explicit skill list.

## Registration

`add` is done only when the applicable registration requirement for its repository type is satisfied.
The README is an index, not a discovery gate. `npx skills` discovers both `.agents/skills` and `.claude/skills` without a README entry.

- A skills repository (for example `skillz-that-grillz`): the `## Skills` table in `README.md`, plus its Scope table when the skill wraps a CLI.
- A dotfiles repository: the harness selection list its sync reads (for example `claude.skills` in the chezmoi data file); that list feeds every harness.
- A plain harness skills directory: nothing to register; the directory is the index.

## Repo-local skills

Keep a skill for one repository only out of every global selection list.

- Source: `.agents/skills/<name>/`. Set `metadata.internal: true` in a distributed skills repository to exclude it from default installation. Codex, Pi, Zed, and other `.agents` hosts read it directly.
- For each host without `.agents` project discovery, add a relative symlink in its project skill directory. Claude Code reads `.claude/skills/<name>`.
- When `.gitignore` ignores these trees, re-include only the named paths: `dir/*`, then `!dir/skills/`, `dir/skills/*`, `!dir/skills/<name>`.

## Frontmatter matrix

| Field | Spec | Claude | Codex | OMP | Pi | Zed |
|---|---|---|---|---|---|---|
| `name` (≤64, kebab) | required | yes | yes | yes | yes | yes |
| `description` (≤1024) | required | yes; listing truncates `description`+`when_to_use` at 1536 and drops descriptions past a budget of 1% of context | yes (implicit match); the skill list uses at most 2% of context or 8,000 characters | yes | yes | yes; longer text loads with a warning; catalog capped at 50KB |
| `license`, `compatibility` (≤500), `metadata` (string map) | optional | accepted; Claude Code does not act on them | unverified | kept as unknown metadata | kept | unverified; Zed lists only `name`, `description`, and `disable-model-invocation` |
| `allowed-tools` | optional | permission grant without prompts; not a deny list | unverified | accepted; verify enforcement | accepted; verify enforcement | unverified |
| `disallowed-tools` | — | removes listed tools for the current turn | unverified | host-specific; verify | host-specific; verify | unverified |
| `disable-model-invocation` | — | yes | **no** → `agents/openai.yaml` `policy.allow_implicit_invocation: false` | yes (`disableModelInvocation`) | yes | yes |
| `user-invocable`, `argument-hint`, `arguments`, `model`, `effort`, `context: fork`, `agent`, `background`, `hooks`, `paths`, `shell`, `when_to_use` | — | yes | unverified | kept as unknown metadata by conventional scanners; not accepted in Agent Plugins packages (closed Agent Skills field set) | unverified | unverified |
| `$ARGUMENTS`, `$0`, `$N` substitution | — | yes | unverified; free text follows the mention | no; the prose around `/skill:<name>` is passed as arguments | no; text after `/skill:<name>` is appended as a user request | unverified |

Unknown keys never break a load on the verified hosts (Claude, OMP conventional scanners, Pi). Codex and Zed are unverified.
OMP Agent Plugins packages (`agent-plugins` provider) are an exception: they validate against a closed Agent Skills field set and do not accept other keys.
Claude's cloud Skills API rejects non-spec keys; that surface is out of scope for repo skills.

## Rules (the Portability lens)

1. Use only spec fields plus the Claude extensions above. Do not invent keys.
2. Claude skills use `disallowed-tools` to remove tools for the current turn. Their `allowed-tools` grants permission without prompts. Claude agent files use `disallowedTools` for denials. For every other limit, state whether the host enforces it or only receives prose.
3. A user-only skill sets `disable-model-invocation: true` **and** ships `agents/openai.yaml` with `allow_implicit_invocation: false`.
4. State arguments with `argument-hint`. Parse them in prose as "the text after the skill name". Never depend on `$ARGUMENTS`.
5. Reference helpers by repo-relative path (`skills/<name>/scripts/...`) or by the loaded `SKILL.md` directory. Never use `${CLAUDE_SKILL_DIR}`.
6. Cross-reference skills by `/name`. Never `@file`.
7. Name a sub-agent dispatch by contract first (fresh context, read-only, tier, synchronous), then show the host syntax as an example (`Agent(...)`, Codex `spawn_agent`, OMP `task(...)`).
8. State a GitHub action first, then the transport: host primitive, then `gh`.
9. Keep the body ≤5k tokens (o200k). Keep references one level deep, each with a read trigger.
10. Model policy: a model-invoked skill sets `model` + `effort` (haiku/low, sonnet/medium, opus/high); a user-only skill omits both and inherits the session model.
11. Put the trigger in the first sentence of `description`, in third person, with a "Do NOT use for" clause.

## Sidecar

```yaml
# skills/<name>/agents/openai.yaml
interface:
  display_name: "<Name>"
  short_description: "<one line>"
policy:
  allow_implicit_invocation: false
```

## Template (`add`)

```markdown
---
name: <name>
description: >
  <Verb> <object> <outcome>. Use when the user says "<phrase 1>", "<phrase 2>",
  "<phrase 3>", or invokes /<name>. Do NOT use for <adjacent task> (/<other>).
# user-only skills:
# disable-model-invocation: true
# argument-hint: "<args>"
# model-invoked skills:
# model: sonnet
# effort: medium
license: MIT
metadata:
  author: <handle>
---

# <name>

<One sentence: what this produces.>

## Inputs

<argument grammar; "the text after the skill name">

## Flow

1. <step> — done when <check>.

## Output

<format block>

## What this skill never does

- <boundary>

## References

- `references/<file>.md` — read when <trigger>.
```

## Evals (`add`)

`add` writes `evals/evals.json` in the agentskills.io shape.
Anthropic skill-creator and NVIDIA SkillEvaluator read the same shape.
The repository validator also requires `name` on each entry.

- `expected_skill`: the skill name, or `null` for a near-miss case.
- `assertions`: checkable statements about the output. A trigger case has none.
- `files`: fixture paths relative to the skill directory. Keep each fixture under `evals/files/`.

`autoimprove` seeds its case draft from this file.

```json
{
  "skill_name": "<name>",
  "evals": [
    {"id": 1, "name": "trigger-<slug>", "prompt": "<trigger phrase>", "expected_output": "The <name> skill loads.",
     "files": [], "expected_skill": "<name>", "assertions": []},
    {"id": 2, "name": "near-miss-<slug>", "prompt": "<anti-trigger request>",
     "expected_output": "The <name> skill stays unloaded.", "files": [], "expected_skill": null, "assertions": []},
    {"id": 3, "name": "task-<slug>", "prompt": "<realistic request>", "expected_output": "<what a correct run produces>",
     "files": ["evals/files/<fixture>"], "expected_skill": "<name>", "assertions": ["<a statement a grader can verify>"]}
  ]
}
```

## Sources

The repository-local `skillz-self-update` skill queries each source for changes since its `Checked` date.
The top-level `Checked:` date equals the oldest date in this table.

| Host | Source | Re-check | Checked |
|---|---|---|---|
| Claude Code | <https://code.claude.com/docs/en/skills> | frontmatter fields, listing truncation, line guidance | 2026-10-05 |
| Agent Skills spec | <https://agentskills.io/specification> | field set and limits | 2026-10-05 |
| Anthropic skills repo | <https://github.com/anthropics/skills> | reference layouts, skill-creator | 2026-10-05 |
| Codex | <https://learn.chatgpt.com/docs/build-skills> | scan paths, `$skill` invocation, `agents/openai.yaml` | 2026-10-05 |
| OMP | <https://github.com/can1357/oh-my-pi/blob/main/docs/skills.md> | provider priority, honored fields, `/skill:` | 2026-10-05 |
| Pi | <https://pi.dev/docs/latest/skills> | paths, validation, `/skill:` | 2026-10-05 |
| Zed | <https://zed.dev/docs/ai/skills> and <https://zed.dev/docs/ai/external-agents> | native vs ACP scope | 2026-10-05 |
| skills CLI | <https://github.com/vercel-labs/skills> | per-agent path table, compatibility matrix | 2026-10-05 |

A cell marked "unverified" in the matrix means the current primary source does not state the fact.

## Rejected

- `${CLAUDE_SKILL_DIR}` as a portable helper path — Codex passes the literal string (easy-cheese `harness-portability.md`).

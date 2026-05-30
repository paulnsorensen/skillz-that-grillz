---
name: serena-config
model: haiku
description: >
  Configure the Serena MCP server — its global defaults and a repo's
  per-project settings. Use when the user says "configure serena", "set up
  serena for this repo", "serena project config", "serena global config",
  "tune serena for this codebase", "edit serena_config.yml", "edit
  project.yml", "monorepo serena", "serena picked the wrong language",
  "serena is indexing vendor/node_modules", "make serena read-only here",
  "serena contexts and modes", "what context for claude-code", "disable
  serena memory/onboarding", "point serena at my installed language server",
  "ls_specific_settings", "ls_path", "SERENA_HOME", or "move the .serena
  folder". Routes per-repo `.serena/project.yml` questions to
  `references/project-config.md` and machine-wide `~/.serena/serena_config.yml`
  (settings, contexts, modes, language-server tuning) to
  `references/global-config.md`. Do NOT use for general "use serena to find /
  edit X" routing — that is an MCP-routing concern, not configuration.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(serena:*), Bash(git:*), mcp__context7__resolve-library-id, mcp__context7__query-docs, mcp__tavily__tavily_extract
context: fork
license: MIT
---

# serena-config

Configure [Serena](https://oraios.github.io/serena/) across its two layers: the
machine-wide global config and a repo's per-project config.

Serena is configured in layers, each overriding or extending the one above:

1. **Global** — `~/.serena/serena_config.yml`. Defaults for every project: language
   backend, default tools/modes, global ignore rules, language-server tuning.
2. **Project** — `<repo>/.serena/project.yml`. Per-repo overrides: languages,
   ignore rules, write access, monorepo workspace folders, added modes.
3. **Contexts & modes** — composable toolset/prompt presets, set globally,
   per-project, or at startup.
4. **CLI args** to `serena start-mcp-server` — override or extend everything above.

> `<certain>` Many `project.yml` keys *extend* or *override* `serena_config.yml`.
> Put machine-wide defaults in the global file; put only repo-specific aspects in
> the project file.

## Route to the right reference

| The user wants to… | Read |
| --- | --- |
| Tune a single repo: wrong language, monorepo cross-refs, ignore `vendor/`/generated dirs, read-only repo, per-repo LSP path | `references/project-config.md` |
| Create / index / activate a project; understand `project.local.yml` local overrides | `references/project-config.md` |
| Set machine-wide defaults: language backend, default tools/modes, global ignore, logging | `references/global-config.md` |
| Pick or change a **context** (`claude-code`, `ide`, `desktop-app`, …) | `references/global-config.md` |
| Add / override **modes** (`planning`, `editing`, `no-memories`, `query-projects`, …) | `references/global-config.md` |
| Point Serena at a self-installed language server (`ls_path`) or pin an LSP version | `references/global-config.md` |
| Move the data directory (`SERENA_HOME`) or the per-project `.serena` folder | `references/global-config.md` |

## When to act (and when not to)

Serena auto-creates both config files with sensible defaults — global on first run,
project on first activation of a repo. **Most repos need no edits.** Touch a config
only when one of these is true:

| Symptom | Layer | Fix lives in |
| --- | --- | --- |
| LSP can't resolve symbols across package boundaries (monorepo) | project | `additional_workspace_folders` |
| `find_symbol` returns wrong-language results or LSP errors at startup | project | `languages:` |
| Symbol search floods from `vendor/`, `node_modules/`, `dist/`, generated code | project | `ignored_paths:` |
| Repo is review-only and Serena should not write | project | `read_only: true` |
| Wrong toolset for the harness (e.g. Claude Code duplicating built-ins) | global | `--context` |
| Want planning-only or memory-free behavior | global | modes |
| Self-installed LSP, or need to pin an LSP/runtime version | either | `ls_specific_settings` |

If none apply, leave the config alone — the bootstrap is correct.

**The one proactive exception — `ignored_paths`.** Unlike the rows above, this is
worth setting *before* you feel pain in any repo that commits vendored or
generated code (`vendor/`, checked-in `dist/`, generated clients, snapshot
fixtures). `.gitignore` is honored by default, but committed noise pollutes
`find_symbol` / `get_symbols_overview` (and can cut the LSP's initial indexing). See
`references/project-config.md` § "`ignored_paths` — set this in most non-trivial
repos". `ls_path`, by contrast, stays an escape hatch — managed-first is the
recommended default (see `references/global-config.md`).

## Editing discipline

- **Never hand-create a config from scratch.** Let Serena bootstrap it
  (`serena project create` for a project, first run for the global file), then
  edit. The generated files carry inline documentation comments and fields added
  by newer versions — preserve them.
- **Verify after editing.** `serena print-system-prompt "$(pwd)"` renders the
  resolved config; confirm the project name, language list, active modes, and
  excluded tools are what you expect. Then probe the LSP with a `find_symbol`
  call against a known symbol — a clean record means the languages parsed; an LSP
  startup error means `languages:` or `ls_specific_settings:` is wrong.
- **On LSP weirdness after a change**, delete `.serena/cache/` to force a rebuild;
  some servers also need the MCP server restarted to fully reset.

## References

- Project config schema (template): <https://github.com/oraios/serena/blob/main/src/serena/resources/project.template.yml>
- Global config schema (template): <https://github.com/oraios/serena/blob/main/src/serena/resources/serena_config.template.yml>
- Language enum (valid `languages:` / `ls_specific_settings` keys): <https://github.com/oraios/serena/blob/main/src/solidlsp/ls_config.py>
- Workflow docs: <https://oraios.github.io/serena/02-usage/040_workflow.html>
- Configuration docs: <https://oraios.github.io/serena/02-usage/050_configuration.html>

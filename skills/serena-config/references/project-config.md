# Project configuration — `.serena/project.yml`

Per-repo Serena settings. Sourced from Serena's [Project Workflow](https://oraios.github.io/serena/02-usage/040_workflow.html#project-config) docs.

## The project lifecycle

1. **Creation** — define fundamental settings (and optionally index).
2. **Activation** — make Serena aware of the project you want to work with.
3. **Onboarding** — Serena builds memories about the project (skippable; see modes in `global-config.md`).
4. **Working** — symbol search and edits against the active project.

### Creating a project

Two ways:

- **Explicit** — run the creation command from the project directory:

  ```bash
  serena project create            # current dir; auto-detect languages
  serena project create --language python --language typescript   # empty project
  serena project create --name my-name --index
  ```

  - Defaults the project directory to the current directory.
  - For an existing project, languages are detected from source files; the dominant one is activated automatically. If several are found, you are prompted to enable them.
  - For an empty project, pass `--language` explicitly (repeatable).
  - `--index` pre-caches symbol information right after creation.

- **Implicit** — just activate a directory mid-conversation; Serena writes default settings and skips straight to activation.

> `<certain>` The subcommand is `serena project create` (verified against the Serena CLI; it accepts `--name`, `--language` (repeatable), and `--index`). Run `serena project --help` for the full, version-current flag list.

### Indexing

For larger projects, index once after creation to avoid a delay on the first
symbol-requiring tool call:

```bash
serena project index      # run from the project directory
```

Index only once; Serena updates the index automatically as files change.
(Not relevant under the JetBrains plugin — the IDE handles indexing.)

### Activation

- **Mid-conversation**: tell the agent to activate (`"Activate the project /path/to/repo"` for first-time auto-create, or `"Activate the project my_name"` for a known one). Requires the `activate_project` tool, which is **disabled** in single-project contexts (`ide`, `claude-code`) when a project is passed at startup.
- **At startup**: pass `--project <path-or-name>` to the MCP server (the normal path for single-project contexts like `claude-code`).

## `project.yml` — what you can configure

After creation, edit `.serena/project.yml`. The file configures:

- the **project name** (used when asking the LLM to activate the project dynamically)
- the **languages** for which language servers spawn (add/remove live via the Dashboard)
- the **language backend** for this project (overrides the global setting)
- source-file **encoding**
- **ignore rules**
- **write access** (`read_only`)
- **additional workspace folders** for cross-package references in monorepos
- an **initial prompt** passed to the LLM whenever the project activates
- the **tools and modes** to use for the project
- and other settings — see the [template file](https://github.com/oraios/serena/blob/main/src/serena/resources/project.template.yml).

### Key fields

```yaml
# project name shown in Serena's UI / logs and used for dynamic activation
project_name: "my-repo"

# LSP keys to start; choose from the language enum in Serena's docs.
# First entry is the default/fallback. Multi-language repos list each.
# For C use cpp; for JavaScript use typescript; Angular/Svelte/SCSS have
# their own subsuming keys (see the template's languages comment).
languages:
  - typescript
  - python

# whether to honor the repo's .gitignore files when ignoring (default true)
ignore_all_files_in_gitignore: true

# ADDITIVE on top of .gitignore AND the global ignored_paths — gitignore
# syntax (*, **). Set this in most non-trivial repos; see below.
ignored_paths:
  - "vendor/**"
  - "dist/**"
  - "**/*.generated.ts"

# cross-package symbol search in monorepos (TypeScript only — see below)
additional_workspace_folders:
  - ../shared-lib
  - packages/utils

# per-language LSP knobs; replaces (does not deep-merge) the global
# ls_specific_settings entry for the same language. Keys vary per server,
# and most languages expose NONE. ls_path is an escape hatch, NOT a default
# — see global-config.md § "ls_path: override or leave managed".
ls_specific_settings:
  python:
    ls_path: ".venv/bin/pyright-langserver"

# disables ALL edit tools for this repo
read_only: false

# encoding of source files (default utf-8) and line endings written by edits
# (unset = inherit global; else lf | crlf | native)
encoding: "utf-8"
line_ending: lf

# tool-set overrides for this repo. excluded_/included_ EXTEND the global
# lists; fixed_tools REPLACES the base set and can't combine with the other two.
excluded_tools: []
included_optional_tools: []

# mode overrides (commented out — copying `default_modes: []` verbatim would
# silently opt this repo out of the global default modes). Uncomment only when
# you mean to override. `default_modes: []` opts out; `added_modes` layers on top.
# See global-config.md § Modes for how base/default/added resolve.
# default_modes: []
# added_modes: [query-projects]

# override the global language backend for this repo only (LSP | JetBrains).
# Fixed at startup — activating a project with a different backend errors out.
language_backend: LSP

# per-call seconds budget for fetching docstrings/param info; overrides global
# (default 10). Lower it for repos on slow LSPs (e.g. clangd) that stall on hover.
symbol_info_budget: 10
```

### `ignored_paths` — set this in most non-trivial repos

This is the path setting that actually pays off broadly. Serena's symbol search
and overviews walk the project tree; vendored, generated, and build-output dirs
pollute `find_symbol` / `get_symbols_overview` results. `<certain>` `ignored_paths`
also reaches the language-server layer (it is a field on solidlsp's
`LanguageServerConfig`), so `<speculative>` excluding bulky generated dirs can cut
the server's initial indexing work too. `.gitignore` already covers most of it (honored by default via
`ignore_all_files_in_gitignore`), but anything *committed* yet noise — vendored
deps, generated clients, snapshot fixtures, `dist/` checked in for a GitHub
Pages build — won't be excluded unless you list it here.

> `<certain>` `ignored_paths` is **additive**: it merges with the repo's
> `.gitignore` and with the global `ignored_paths` from `serena_config.yml`. It
> never *un*-ignores anything.

A conventional starter set (`<speculative>` — opinionated, not Serena-blessed;
the docs prescribe no defaults). Trim to what's actually committed in your repo:

```yaml
ignored_paths:
  - "node_modules/**"      # if not already gitignored
  - "vendor/**"            # committed third-party code
  - "dist/**"
  - "build/**"
  - "**/__snapshots__/**"
  - "**/*.generated.*"     # generated clients / protobufs / GraphQL types
  - "**/*.min.js"
```

Don't list things `.gitignore` already excludes — that's redundant. The value of
`ignored_paths` is the *committed* noise gitignore can't reach.

### Additional workspace folders (cross-package references)

In monorepos, Serena's language server only sees symbols within the project root
by default. To let `find_referencing_symbols` discover usages in sibling packages,
list those packages:

```yaml
additional_workspace_folders:
  - ../shared-lib
  - ../api-client
  - /absolute/path/to/another-package
```

Paths are absolute or relative to the project root. Each is registered as an LSP
workspace folder.

> `<certain>` **TypeScript only** at time of writing. Other language servers raise
> an error if this setting is used. For non-TS monorepos, start Serena from the
> monorepo root and rely on the language server's own workspace detection.
> `<certain>` Each extra folder adds startup time (the LSP indexes it). List only
> the packages you actually need cross-references for.

### Local overrides — `project.local.yml`

`project.yml` is meant to be **committed with the repo**. For machine-specific
tweaks that should not be versioned, put them in `project.local.yml` in the same
directory — same schema, git-ignored by default, and any key there overrides the
matching key in `project.yml`.

## The `.serena/` directory

| File | Purpose | Default git status |
| --- | --- | --- |
| `project.yml` | Main config — intended to be versioned with the repo | committed |
| `project.local.yml` | Machine-local overrides; same schema | git-ignored (Serena's own `.serena/.gitignore`) |
| `cache/` | Symbol cache; rebuilt on demand | git-ignored |
| `memories/` | Memory store | per onboarding/memory config |
| `.serena/.gitignore` | Excludes `cache/` + `project.local.yml` | committed |

> To relocate this folder (e.g. keep repos clean), set `project_serena_folder_location`
> in the **global** config — see `global-config.md`.

## Reading from external projects (`query-projects`)

To let Serena read code/symbols from *another* project while working on this one
(e.g. a dependency), enable the `query-projects` mode (see `global-config.md`).
That exposes a `query_project` tool plus a project-listing tool. The queried
project must already be known to Serena (created as above). Under the LSP backend,
symbolic queries through this tool require Serena's **Project Server** running so
it can spawn the language servers for queried projects.

For a single agent editing several projects at once, the recommended layout is a
**monorepo folder** (real or via symlinks) containing each project as a subfolder,
opened as one Serena project — list every language used across them.

## Committing decision

| Repo shape | Recommendation |
| --- | --- |
| Single-author / exploratory | Leave `.serena/` git-ignored — bootstrap recreates it anywhere |
| Monorepo with non-trivial `additional_workspace_folders` | Commit `project.yml` — it encodes architectural intent contributors need |
| Shared team repo with custom `ignored_paths` / `ls_specific_settings` | Commit `project.yml` — saves every collaborator re-discovering the tuning |
| Review-only fork (`read_only: true`) | Commit it — the read-only stance is policy, not preference |

## Verification

1. **Render the resolved config** — fastest sanity check that Serena parsed the file:

   ```bash
   serena print-system-prompt "$(pwd)"
   ```

   Confirm the project name, language list, active modes, and excluded tools.
2. **Probe a symbol** — call `find_symbol` with a known name, `include_body=true`.
   A clean symbol record means the LSP started; a startup error means `languages:`
   or `ls_specific_settings:` is wrong.
3. **Check diagnostics** — `get_diagnostics_for_file` on any source file returns
   real diagnostics or an empty list; LSP setup errors mean misconfigured languages.

If verification fails, delete `.serena/cache/` (forces a rebuild) and retry. If it
still fails, your `languages:` entry probably isn't a valid key in the
[language enum](https://github.com/oraios/serena/blob/main/src/solidlsp/ls_config.py).

## Gotchas

- `<certain>` `additional_workspace_folders` is TypeScript-only.
- `<certain>` Project-level `ls_specific_settings` **replaces** the global entry for
  that language at the top level — it is not deep-merged. Re-state the keys you need.
- `<certain>` `read_only: true` disables *every* editing tool, including
  `replace_symbol_body` and `insert_*_symbol` — not just `replace_content`.
- `<speculative>` Some LSP servers (intelephense, rust-analyzer) cache aggressively;
  after editing `ls_specific_settings`, deleting `.serena/cache/` is sometimes not
  enough — restart the MCP server to fully reset.
- The bootstrap picks `languages:` from the dominant file extension. A docs-heavy
  monorepo where `.md` outnumbers code can get `markdown` picked and leave you
  without a real LSP. Override explicitly.
- Keep `initial_prompt` small — it is prepended to every session in this repo and
  inflates context. Put stable instructions in `AGENTS.md` / `CLAUDE.md` / `.cursor/rules` or your harness's instructions file instead.

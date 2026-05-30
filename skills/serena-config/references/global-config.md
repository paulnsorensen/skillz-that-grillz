# Global configuration — `~/.serena/serena_config.yml`

Machine-wide Serena defaults that apply to every project unless overridden by a
project's `project.yml`, a context/mode, or CLI args. Sourced from Serena's
[Configuration](https://oraios.github.io/serena/02-usage/050_configuration.html) docs.

## The config file

Auto-created on first run. Location:

- Linux / macOS / Git-Bash: `~/.serena/serena_config.yml`
- Windows (CMD/PowerShell): `%USERPROFILE%\.serena\serena_config.yml`

Access it via the [Serena Dashboard](https://oraios.github.io/serena/02-usage/060_dashboard.html)
while running, directly in an editor, or via the `serena config edit` command.

Full schema: the [template file](https://github.com/oraios/serena/blob/main/src/serena/resources/serena_config.template.yml).

## What lives here

- the **language backend** default (JetBrains plugin vs language servers; overridable per project)
- **UI settings** for the Dashboard and GUI tool
- the **default tools** to enable/disable
- the **default modes** to use
- **tool execution parameters** (timeout, max answer length)
- **global ignore rules**
- **logging** settings
- **language-server-specific settings** (`ls_specific_settings`, see below)

Some of these are *extended* or *overridden* by project settings, contexts, and modes.

### Notable defaults (from `serena_config.template.yml`)

`<certain>` Values a fresh install ships with — change these, don't recreate them:

| Key | Default | Notes |
| --- | --- | --- |
| `language_backend` | `LSP` | vs `JetBrains`; overridable per project |
| `base_modes` | `[interactive, editing]` | always-active modes (see Modes) |
| `default_modes` | *(empty)* | overridable by project / `--mode` |
| `tool_timeout` | `240` | seconds before a tool execution is killed |
| `default_max_tool_answer_chars` | `150000` | default cap on a tool's answer length |
| `symbol_info_budget` | `10` | per-call seconds for docstring/param fetch; `0` disables. Lower it for slow LSPs (clangd is called out by name) that stall on `request_hover`. Overridable per project. |
| `line_ending` | `native` | `lf` / `crlf` / `native`; per-project overridable |
| `web_dashboard` | `True` | template comment: *"strongly recommend to always enable"* |
| `excluded_tools` / `included_optional_tools` / `fixed_tools` | *(empty)* | `fixed_tools` replaces the base set; can't combine with the other two |
| `project_serena_folder_location` | `$projectDir/.serena` | see below |

## Contexts

A **context** is the environment Serena runs in. It sets the initial system prompt
and the available toolset. It is fixed at startup (`--context <name>`) and cannot
change during a session.

| Context | Use for |
| --- | --- |
| `desktop-app` | Claude Desktop and similar — full toolset (default; assumes no built-in coding tools) |
| `claude-code` | Claude Code — disables tools that duplicate Claude Code's built-ins |
| `codex` | OpenAI Codex |
| `ide` | Generic IDE assistants (VSCode, Cursor, Cline) — augments existing file/shell capabilities |
| `agent` | Autonomous-agent scenarios (e.g. Agno) |
| `oaicompat-agent` | Like `agent`, for local servers needing OpenAI-compatible tool descriptions |

> `<certain>` `ide` and `claude-code` are **single-project contexts**
> (`single_project: true`). When a project is passed at startup, the toolset is
> trimmed to what that project's config requires, tools the project disables are
> dropped entirely, and the project-activation tool is disabled (switching projects
> is no longer meaningful).

Definitions: <https://github.com/oraios/serena/tree/main/src/serena/resources/config/contexts>.
Manage with the `serena context` command.

## Modes

**Modes** refine behavior for a kind of task; multiple can be active at once. They
influence the system prompt and can exclude tools.

| Mode | Effect |
| --- | --- |
| `planning` | Planning and analysis focus |
| `editing` | Optimized for direct code modification |
| `interactive` | Conversational, back-and-forth style |
| `one-shot` | Complete in a single response (often paired with `planning` for reports) |
| `onboarding` | Focus on the project onboarding process |
| `no-onboarding` | Skip onboarding but keep memory tools |
| `no-memories` | Disable all memory tools (and tools built on them, e.g. onboarding) |
| `query-projects` | Enable tools to query other Serena projects without activating them |

### How active modes are resolved

The active set is the **union** of:

- `base_modes` — global config; always active. `<certain>` Ships defaulting to
  `[interactive, editing]` in `serena_config.template.yml` — that pair is what a
  fresh install runs with unless you change it. Empty/undefined ⇒ no base modes.
- `default_modes` — global config; overridable by project (`default_modes`) or CLI (`--mode`). Empty by default.
- `added_modes` — project config (`added_modes`) or CLI (`--add-mode`); added on top.

So the out-of-the-box active set is just `interactive + editing`. To make a mode
universal, add it to `base_modes`; to make it a usually-on-but-overridable
default, use `default_modes`; for one repo/session, use `added_modes`.

Guidance:

- Modes you *always* want → `base_modes`.
- Modes you *usually* want but sometimes override → `default_modes`.
- Modes for specific projects/sessions → `added_modes`.

> `<certain>` Some combinations are semantically incompatible (e.g. `interactive` +
> `one-shot`). Serena does not prevent this — choose sensible combinations.
> Mode definitions: <https://github.com/oraios/serena/tree/main/src/serena/resources/config/modes>.
> Manage with the `serena mode` command.

## Advanced settings

### Data directory — `SERENA_HOME`

The user data directory (config, language-server files, logs) defaults to `~/.serena`.
Override the location by setting the `SERENA_HOME` environment variable.

### Per-project `.serena` folder location

By default each project keeps its Serena data in `<project>/.serena`. Relocate it
globally with `project_serena_folder_location` in `serena_config.yml`, using two
placeholders:

| Placeholder | Meaning |
| --- | --- |
| `$projectDir` | absolute path to the project root |
| `$projectFolderName` | name of the project folder |

```yaml
# Default: data inside the project directory
project_serena_folder_location: "$projectDir/.serena"

# Central: all project data under one shared directory
project_serena_folder_location: "/projects-metadata/$projectFolderName/.serena"
```

Load-time fallback: (1) the configured path, (2) a `.serena` in the project root
(legacy/default), (3) create at the configured path. Existing in-root folders keep
working after you change the setting.

### Language-server-specific settings — `ls_specific_settings`

> **Advanced users only.** Most setups never touch these.

Under `ls_specific_settings` in `serena_config.yml`, pass per-language LSP config.
The same key works in `project.yml` / `project.local.yml` to override or extend the
global value for one project — settings merge at the top level, so a project entry
for a language **replaces** the global entry for that language.

```yaml
ls_specific_settings:
  <language>:
    # language-server-specific keys
```

#### `ls_path`: override or leave managed (default = leave managed)

> `<certain>` Serena is **managed-first by design.** The configuration docs
> introduce `ls_path` only as: *"if you have installed the language server
> yourself and want to use your installation instead of Serena's managed
> installation, you can set the `ls_path` setting."* There is **no** official
> guidance to set it in the common case, and most languages expose no
> per-language options at all ("No documentation on options means no options are
> available"). Leaving it unset is the recommended path.

| Leave `ls_path` unset (the default — do this) | Set `ls_path` (rare, deliberate) |
| --- | --- |
| You want Serena to download + version-manage the server | You already installed the server and want *that* binary |
| Reproducibility comes from Serena's pinned managed version | Air-gapped / offline box where the managed download can't run |
| You don't care which exact LSP binary runs | You must match a specific local toolchain the managed one won't |

> `<certain>` Setting `ls_path` **bypasses Serena's managed download** for that
> server; any server-specific version/registry settings then apply only while
> `ls_path` is unset. So `ls_path` and version-pin knobs are mutually exclusive
> for the same language.

```yaml
ls_specific_settings:
  <language>:
    ls_path: "/path/to/language-server"
```

> `<certain>` Supported by servers whose dependency provider derives from
> `LanguageServerDependencyProviderSinglePath`, plus some wrappers that expose
> `ls_path`. Documented examples: `ansible`, `bash`, `bsl`, `clojure`, `cpp`,
> `cpp_ccls`, `hlsl`, `html`, `kotlin`, `lean4`, `luau`, `markdown`, `php`,
> `php_phpactor`, `python`, `rust`, `scss`, `solidity`, `systemverilog`, `toml`,
> `typescript`, `yaml`. Setting `ls_path` bypasses Serena's managed download; any
> server-specific version/registry settings then apply only when `ls_path` is unset.
> `<certain>` `angular` does **not** support `ls_path` — it is a multi-process
> orchestration; pin versions via its dedicated settings instead.

#### Per-language version / behavior knobs

Each managed language server exposes its own keys (version pins, registry overrides,
runtime paths, lint toggles). A few representative ones:

| Language key | Notable settings |
| --- | --- |
| `python` | `ls_path`, version pins |
| `typescript` | `ls_path`, `typescript_version`, `typescript_language_server_version`, `npm_registry` |
| `angular` | `angular_language_server_version`, `angular_language_service_version`, `typescript_version`, `npm_registry` (no `ls_path`; must be listed explicitly in `project.yml`; don't also list `typescript`/`html`) |
| `bash` | `ls_path`, `bash_language_server_version`, `npm_registry` |
| `ansible` | `ls_path`, `ansible_language_server_version`, `lint_enabled`, `python_interpreter_path`, … |
| `clojure` | `ls_path`, `clojure_lsp_version`, `source_paths`, `config_edn_path` (for multi-module monorepos) |
| `cpp` (clangd) | `ls_path`, `compile_commands_dir`, `clangd_version` |
| `cpp_ccls` | `ls_path` (ccls from PATH; not managed) |
| `bsl` | `ls_path`, `bsl_ls_version` (needs Java 21+) |
| `csharp` (Roslyn) | needs .NET 10+; server auto-downloaded from NuGet |

> The full per-language catalog (every supported key, defaults, runtime requirements)
> lives in the [Configuration docs](https://oraios.github.io/serena/02-usage/050_configuration.html#language-server-specific-settings)
> and the [config template](https://github.com/oraios/serena/blob/main/src/serena/resources/serena_config.template.yml).
> Check there before guessing keys — most languages expose few or none ("No
> documentation on options means no options are available"); only a minority,
> like those above, carry notable settings.

## Verification

After editing the global config, render the resolved system prompt for any project
and confirm the defaults took effect:

```bash
serena print-system-prompt "$(pwd)"
```

Look for the expected default modes, context-trimmed toolset, and any globally
excluded tools.

---
name: chezmoi
model: haiku
description: >
  Manage dotfiles with chezmoi — the source-state dotfile manager that uses
  filename attributes (`dot_`, `private_`, `encrypted_`, `run_once_`), Go
  templates, and password-manager integration to keep one set of dotfiles
  working across many machines. Use when the user says "set up dotfiles",
  "manage my dotfiles", "bootstrap a new machine", "chezmoi", "encrypt my SSH
  key in dotfiles", "template dotfiles per OS", "add secrets to dotfiles",
  "what should be in my .chezmoi.toml.tmpl", or asks how to share configs
  across macOS / Linux / servers. Also trigger when the user is staring at a
  fresh machine and wants the one-liner that gets them home, or when they're
  about to commit something sensitive into a dotfiles repo. Do NOT use for
  stow / yadm / rcm or other dotfile managers, generic git repo setup, or
  password-manager setup unrelated to dotfiles.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(chezmoi:*), Bash(git:*), Bash(age:*), Bash(gpg:*), mcp__context7__resolve-library-id, mcp__context7__query-docs
context: fork
license: MIT
---

# chezmoi

Wrap [chezmoi](https://chezmoi.io/) — manage dotfiles across machines using
source-state semantics, Go templates, and at-rest encryption.

## When to use

- Bootstrapping dotfiles on a new machine
- Adding a new file to existing chezmoi-managed dotfiles
- Templating a dotfile per-OS, per-host, or per-role (work vs. personal)
- Adding secrets via 1Password / Bitwarden / age / gpg
- Writing a `run_once_` install script
- Diagnosing "chezmoi apply changed something I didn't expect"
- Designing the `.chezmoi.toml.tmpl` config template for a fleet of machines

## Mental model

chezmoi maps a **source directory** (`~/.local/share/chezmoi`, a git repo) to
a **target directory** (the user's home). Source filenames carry attribute
prefixes that determine target name and behavior. Source files with a `.tmpl` suffix are rendered as Go `text/template` and
written to their target paths. Files under `.chezmoitemplates/` are reusable
template fragments included by other templates — they do not produce target
files on their own. A
config file at `~/.config/chezmoi/chezmoi.toml` (typically generated from
`.chezmoi.toml.tmpl` on first init) holds per-machine variables.

The two reasons people get into trouble:

1. They edit files directly under `~/.local/share/chezmoi/` instead of using
   `chezmoi edit $TARGET`. This breaks templates and decrypt round-trips.
2. They commit secrets in plaintext "because the repo is private". Repos
   leak, forks leak, history is forever. Use a secret backend.

## Protocol

### 1. Detect what the user wants

| User says | Go to |
|---|---|
| "set up chezmoi from scratch" / "new machine" | §2 Bootstrap |
| "add this file to my dotfiles" | §3 Add a file |
| "make this dotfile depend on OS / host" | `references/templating.md` |
| "encrypt this" / "store this token" | `references/secrets.md` |
| "run a script on first apply" | `references/scripts.md` |
| "chezmoi did something weird" | `references/pitfalls.md` |
| "design my full setup" | `references/bootstrap.md` |

### 2. Bootstrap (new machine)

The full one-liner that clones, renders templates, and applies in one shot:

```bash
sh -c "$(curl -fsLS get.chezmoi.io)" -- init --apply $GITHUB_USERNAME
```

For a fully fleshed `.chezmoi.toml.tmpl` recipe with `promptBoolOnce` for
work-vs-personal-vs-headless, see `references/bootstrap.md`.

### 3. Add a file

```bash
chezmoi add ~/.zshrc                  # plain copy
chezmoi add --template ~/.gitconfig   # add as a template (.tmpl suffix added)
chezmoi add --encrypt ~/.ssh/config   # encrypted at rest
chezmoi chattr +template ~/.zshrc     # turn an existing managed file into a template
chezmoi edit ~/.zshrc                 # edit the SOURCE — handles decrypt + .tmpl correctly
```

Then the safe-apply ritual (§5).

### 4. File-naming reference

The table users get wrong most often. Prefixes are parsed in order and
combined; `literal_` halts parsing.

| Prefix | Effect on target |
|---|---|
| `dot_` | Add leading `.` (`dot_zshrc` → `~/.zshrc`) |
| `private_` | Strip group + world permissions (mode 0600 / 0700) |
| `readonly_` | Strip all write permission bits |
| `executable_` | Add executable bit |
| `empty_` | Allow empty file (default would remove it) |
| `encrypted_` | Decrypt source via age/gpg before writing target |
| `symlink_` | Create a symlink instead of a regular file |
| `exact_` (dirs) | Delete anything in target dir not present in source |
| `create_` | Create target only if missing — never overwrite |
| `modify_` | Treat source as a script that produces target contents |
| `run_` | Treat source as a script to execute (not a target file) |
| `before_` / `after_` | Order scripts relative to applying files |
| `once_` (scripts) | Run only when contents have not been run successfully before |
| `onchange_` (scripts) | Run only when contents change for THIS filename |
| `remove_` | Remove the target if it exists |
| `external_` | Ignore attributes in child entries |
| `literal_` (anywhere) | Stop parsing further attributes from this point |

Suffix: `.tmpl` makes the file a template. `.age` / `.asc` are stripped
automatically when the corresponding encryption is configured.

### 5. Safe-apply ritual

Before any apply that touches important files, run the inspection trio:

```bash
chezmoi status              # one line per changed file
chezmoi diff                # unified diff of pending changes
chezmoi apply --dry-run -v  # full plan, including scripts that would run
chezmoi apply               # only after the above looks right
```

For routine pulls on a machine you trust:

```bash
chezmoi update                                          # = git pull --autostash --rebase + apply
chezmoi git pull -- --autostash --rebase && chezmoi diff # preview only — no apply
```

### 6. Edit safely

```bash
chezmoi edit ~/.zshrc       # opens source in $EDITOR, handles .tmpl + encryption
chezmoi cd                  # subshell in the source directory (for git operations)
chezmoi re-add              # refresh source from current target after editing target
chezmoi merge ~/.zshrc      # 3-way merge target ↔ source ↔ destination
```

Never open `~/.local/share/chezmoi/encrypted_dot_ssh/...` or `*.tmpl` files
directly in your editor — the file you see isn't the file chezmoi sees.

## Hard rules

These are non-negotiable. Each one corresponds to a real foot-gun:

1. **Never commit plaintext secrets**, even to a private repo. Repos leak,
   get forked, get logged, get backed up. Pick a backend from
   `references/secrets.md`.
2. **Never edit encrypted or templated source files directly.** Use
   `chezmoi edit $TARGET` so the decrypt and template round-trips happen.
3. **Always inspect before applying** on shared/important files —
   `chezmoi diff` or `chezmoi apply --dry-run -v`. `exact_` on a directory
   deletes unmanaged entries; you want to see that coming.
4. **`prompt*` template functions belong only in the config-file template**
   (`.chezmoi.toml.tmpl`). Using them in regular dotfile templates causes a
   prompt on every apply / diff / status, which defeats automation.

## Secrets decision tree (summary)

Full details in `references/secrets.md`. Pick exactly one per repo.

- Public repo + cloud password manager → 1Password CLI / Bitwarden template
  functions. Secrets fetched at apply time, never on disk.
- Private repo + offline-friendly → age (`encrypted_` prefix). Identity at
  `~/.config/chezmoi/key.txt`, lives outside the repo.
- Sharing across a team → SOPS-encrypted `.chezmoidata/secrets.yaml`.
- Air-gapped / first-bootstrap → gitignored plaintext `.chezmoidata/local.yaml`
  (transitional, not a long-term answer).

Mixing backends multiplies moving parts without making anything more secure.

## Templating quick reference

Full details in `references/templating.md`.

```go-template
{{- if eq .chezmoi.os "darwin" }}
export HOMEBREW_PREFIX="/opt/homebrew"
{{- else if eq .chezmoi.os "linux" }}
export PATH="/usr/local/bin:$PATH"
{{- end }}

{{- if and (eq .chezmoi.os "darwin") (lookPath "brew") }}
# brew is installed
{{- end }}

email = {{ .email | quote }}
```

Built-in template variables include `.chezmoi.os`, `.chezmoi.arch`,
`.chezmoi.hostname`, `.chezmoi.username`, `.chezmoi.homeDir`,
`.chezmoi.sourceDir`. User-defined data lives under `[data]` in
`chezmoi.toml`, or any `.chezmoidata/*.{toml,yaml,json}` file.

## Special directories

| Path | Role |
|---|---|
| `.chezmoidata/` | TOML/YAML/JSON files merged into the template `.` namespace |
| `.chezmoitemplates/` | Reusable template fragments (`{{ template "name" . }}`) |
| `.chezmoiscripts/` | Scripts that don't correspond to any target file |
| `.chezmoiexternal.{toml,yaml}` | Pull files/archives from URLs at apply time |
| `.chezmoiignore` | Per-target ignore list — itself templatable |
| `.chezmoiroot` | Single-line file pointing at a sub-directory as the source root |
| `.chezmoi.toml.tmpl` | Config-file template used on first `chezmoi init` |

`.chezmoiignore` matches **source-relative paths** (e.g. `dot_zshrc`), not
target paths. People get this wrong constantly.

## What you don't do

- Write a competing dotfile manager or shim around `chezmoi apply`
- Edit files under `~/.local/share/chezmoi/` directly when they are templates
  or encrypted
- Commit a `.env` / `secrets.yaml` to a "private" repo
- Suggest `chezmoi apply` without first running `chezmoi diff` on a shared
  machine
- Use `prompt*` template functions outside of `.chezmoi.toml.tmpl`
- Mix multiple secret backends (age + 1Password + SOPS) in the same repo
  without a clear, written reason

## Gotchas

- `fork/exec ...: exec format error` running a templated script means a
  newline before `#!`. Add `-` inside the closing `}}` on the first
  template line to suppress it.
- `fork/exec ...: permission denied` for scripts means `$TMPDIR` is
  mounted `noexec`. Set `scriptTempDir` in `chezmoi.toml`.
- Windows line endings (CRLF) in templates produce broken shell scripts on
  Linux/macOS. Add `* text eol=lf` to a `.gitattributes` in the dotfiles
  repo, or set `core.autocrlf = input`.
- `chezmoi init` on a new machine before `.chezmoi.toml.tmpl` exists
  silently skips per-machine prompts. Land that template before adding
  host-specific files.
- `chezmoi doctor` is the first command to run when something is off — it
  reports missing dependencies (`op`, `age`, etc.), broken paths, and
  shell integration issues in one pass.
- Context7 may be unavailable. The chezmoi CLI is self-documenting via
  `chezmoi help` and `chezmoi help <command>`; fall back to that.

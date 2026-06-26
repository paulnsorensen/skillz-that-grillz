# Stacked-PR setup & team onboarding

One-time setup for each tool, and — the part that trips people up — **which
config is shared through the repo vs which every teammate and every machine
must redo**. Companion to `../SKILL.md` (detection + tool selection) and the
per-tool references (`gt.md`, `git-town.md`, `gh-stack.md`).

Read when the user asks "do we run this once or on every machine", "how does
my teammate get set up", "set up stacked PRs for the team / for this repo",
or is onboarding a second machine.

## The config-scope model — read this first

Every stacking tool stores config in one of three scopes. Only one of them
travels:

| Scope | Lives in | Shared with teammates? | On your *other* machine? |
| --- | --- | --- | --- |
| **per-user / per-machine** | home dir (`~/…`) | No | No — redo per machine |
| **per-clone (local)** | `.git/` (never committed) | No | No — redo per clone |
| **committed-and-shared** | a tracked file in the repo | Yes | Yes |

"Do I run `init` once or on every machine?" reduces to: *which scope does the
tool put its repo config in?* If it lands in `.git/`, it's per-clone and there
is no sharing — every teammate, every machine, every fresh clone repeats it.
If it lands in a committed file (or server-side), the team inherits it.

## Cross-tool scope matrix

| | `gt` (Graphite) | `git town` | `gh stack` |
| --- | --- | --- | --- |
| Third-party account per CLI user | **Yes** (free Hobby OK for personal repos; org repos need a plan — see OSS note) | No | No (GitHub only) |
| Per-machine auth | `gt auth --token` → `~/.graphite_user_config` | forge auth: `gh auth login` **or** `git config --global git-town.github-token <PAT>` | `gh auth login` |
| Repo config written by | `gt init` → `.git/.graphite_repo_config` | `git town init` → `git-town.toml` | GitHub server-side (waitlist enablement) |
| …and that config is | **local, never committed** | **committed & shared** | **server-side & shared** |
| Per-clone init required? | **Yes — every clone & machine** | **No** — teammates inherit the committed TOML¹ | **No** — server-side |
| Reviewers need any tooling? | No² | No² | No (native GitHub UI) |

¹ git-town's trunk also has a per-clone form (`git config --local
git-town.main-branch`), but a committed `git-town.toml` overrides it, so a
teammate cloning a repo that already has the file skips the wizard entirely.
² Stacked PRs are ordinary GitHub PRs — reviewers review and merge on
github.com with no CLI and no account on the stacking tool.

## `gt` (Graphite)

`.git/.graphite_repo_config` lives **inside `.git/`**, which git never
commits or pushes. So `gt init` is **per clone — once per machine × repo**.
You cannot run it once for the team; there is nothing to share.

**Repo admin / first user:** nothing special — there's no shared artifact to
commit.

**Every teammate (once ever):** create a Graphite account at
<https://app.graphite.com> (free Hobby tier issues the CLI token). Every CLI
user needs their own account; reviewers do not.

**Every machine (once, covers all repos):**

```bash
brew install withgraphite/tap/graphite      # or: npm i -g @withgraphite/graphite-cli@stable
gt auth --token <token-from-app.graphite.com/activate>   # writes ~/.graphite_user_config
```

CI / ephemeral hosts: set `GRAPHITE_AUTH_TOKEN` (v1.8.3+) instead of writing
the config file.

**Every clone (once each):**

```bash
gt init      # prompts for trunk; writes .git/.graphite_repo_config (NOT committed)
```

Skipping it is harmless — any `gt` command auto-prompts the same flow.

**Teammate collaboration:** push with `gt submit --stack`; a teammate (or your
second machine) pulls the stack with `gt get` (branches arrive **frozen** by
default) or `gt get --unfrozen` to edit immediately. `gt freeze` /
`gt unfreeze` toggle later. Under worktrees (v1.8.4+) `gt` refuses to mutate a
branch checked out in another worktree; trunk can still update; `gt undo`
history is per-worktree.

## `git town`

Free, MIT-licensed, no account, no server, forge-agnostic. The repo config is
a **committed file**, so onboarding is the lightest of the three.

**Repo admin (once for the project):**

```bash
git town init                       # wizard: trunk, perennial branches, sync/ship strategies
git add git-town.toml && git commit # commit the shared config
```

The config file (`git-town.toml`, also accepted as `.git-town.toml` or
`.git-branches.toml`) holds the main branch, perennial branches, and sync /
ship strategies — **all shared**. Forge **tokens never go in it** (they're
confidential). Optionally also commit `.github/workflows/git-town.yml` (the
`git-town/action`) to render a stack-navigation diagram into PR descriptions
so reviewers see the stack without any local tool.

**Every teammate / every machine:**

```bash
brew install git-town               # or choco / scoop (Windows); apt/.deb/.rpm/pacman; go install …@latest
gh auth login                       # …then reuse it explicitly:
git config --local git-town.github-connector gh   # reuse gh's auth (cleanest if gh is set up)
# …or, PAT route (per-user, not committed):
git config --global git-town.github-token <PAT>
# CI / ephemeral hosts: export GIT_TOWN_GITHUB_TOKEN=<PAT> instead of a stored token
```

Cloning the repo picks up the committed `git-town.toml` automatically — **no
wizard, no per-clone init.** That's the key contrast with `gt`.

**Reviewers:** nothing.

**Gotchas:** don't hardcode `git-town.github-connector` in the committed file
— it forces `gh`-or-not on the whole team; leave connector choice per-user.
Squash-merging the bottom of a stack outside git-town can produce phantom
conflicts on the next sync; the `fast-forward` ship strategy avoids them. For
fork-originated PRs, the Action needs "Allow edits by maintainers" or a PAT
repo secret (or use its `location: comment` mode). Forges: GitHub, GitLab,
Gitea, Forgejo/Codeberg, Bitbucket.

## `gh stack`

First-party GitHub extension. **Still private preview as of June 2026** — no
GA date and no announced pricing. The repo is enabled **server-side**, so
there's no per-clone init and the stack state is shared by construction.

**Repo / org enablement (once, server-side):** submit the org handle at
[gh.io/stacksbeta](https://gh.io/stacksbeta) and wait for GitHub to enable
it. This is **not** a self-service repo-admin toggle — GitHub flips it after
waitlist approval, and once enabled **all collaborators** get it with no
individual signup.

**Every teammate / every machine:**

```bash
gh extension install github/gh-stack    # needs gh >= 2.0
gh auth login                           # standard GitHub auth; no third-party account
```

**Reviewers:** nothing — the stack navigator is native GitHub PR UI.

**Where state lives:** authoritative state is **server-side on GitHub** (it
cascades the rebase server-side when a PR merges); local `.git/gh-stack` is
just a cache, not committed. Adopting existing branches is done at init time
via positional args to `gh stack init` (there is no `--adopt` flag). Stacks
**cannot** be driven from a fork.

**Enablement check:** there's no preflight command. A remote op (`submit` /
`sync` / `link`) **exits `9`** — "Stacked PRs not enabled for this
repository" — when the repo isn't enabled. (Exit `4` is a generic GitHub API
failure, a different condition.) If you hit `9`, point the user at the
waitlist; if `gt` is also installed, offer to fall back to it for this repo.

## Free for an open-source team?

All three can produce stacked PRs that teammates review for free on GitHub.
The cost question is about the *driver*, and for a multi-contributor OSS repo:

- **`git town` — recommended.** Fully free, MIT, no account, no server,
  works from forks. Committed config means lightest onboarding.
- **`gt` (Graphite)** — the free Hobby tier covers **personal-account repos
  only**; on an **org repo** the CLI hard-errors without a plan. The
  **"Graphite for Open Source"** program can grant the full plan free to OSS
  projects, but eligibility is a **contact-us check** — email
  `billing@graphite.com` to confirm (the current pricing FAQ documents no fixed
  member-count threshold). Every CLI user still needs a Graphite account.
  *(Eligibility terms change — verify with Graphite before relying on this.)*
- **`gh stack`** — free and native, but **not usable yet**: private preview
  behind a waitlist with no GA date. Revisit once it's GA.

## Sources

- Graphite install / auth / init scope: <https://graphite.dev/docs/install-the-cli>, <https://graphite.dev/docs/cli-quick-start>, <https://graphite.dev/docs/configure-cli>, <https://graphite.dev/docs/collaborate-on-a-stack>
- Graphite OSS program: <https://graphite.dev/docs/pricing-faq>
- git-town config file + tokens + connector: <https://www.git-town.com/configuration-file.html>, <https://www.git-town.com/preferences/github-token.html>, <https://www.git-town.com/preferences/github-connector.html>, <https://www.git-town.com/install.html>
- git-town Action: <https://github.com/git-town/action>
- gh stack quick start + CLI reference (exit codes) + waitlist: <https://github.github.com/gh-stack/getting-started/quick-start/>, <https://github.github.com/gh-stack/reference/cli/>, <https://github.com/github/gh-stack>, <https://gh.io/stacksbeta>

# Auth and first-time setup

First-time setup for `gt` in a fresh shell or fresh repo. Read once when the
machine or the repo isn't yet wired up; afterwards the day-to-day workflow in
`../SKILL.md` is enough.

## Install

```bash
brew install withgraphite/tap/graphite       # macOS / Linux via Homebrew tap
npm install -g @withgraphite/graphite-cli    # Node.js
```

Verify:

```bash
gt --version
```

If `which gt` lands on `/opt/homebrew/bin/gt` (macOS) or `/usr/local/bin/gt`
(Linux), you're set.

## Auth

`gt auth` needs a token from the Graphite dashboard:

1. Open <https://app.graphite.com/settings/cli> in a browser.
2. Create a new auth token. The page generates a ready-to-paste
   `gt auth --token <token>` command.
3. Paste and run it in the terminal.

```bash
gt auth --token <token-from-app.graphite.com>
```

The token is stored under `~/.graphite_user_config`. Re-auth if `gt submit`
fails with a 401 — the token has likely expired or been revoked.

## Repo init

Inside the repo, once per clone:

```bash
gt init
```

`gt init` asks for the trunk branch (default `main`). Accept unless the repo
calls trunk something else (`master`, `trunk`, `develop`).

Skip `gt init` if a teammate already ran it and committed `.graphite_repo_config`
— the config is checked into the repo and shared.

## Trunk-detection gotcha

If the repo has multiple "trunk-like" branches (e.g. `main` plus a long-lived
`release/*` branch), `gt` may misidentify trunk on first run. Re-run
`gt init` and pick the right trunk, or open `gt config` and adjust.

Verify with:

```bash
gt log short
```

The bottom node should be your real trunk.

## Useful config knobs

`gt config` is an interactive menu — there's no `gt config --foo bar` form.
Run it and walk the prompts:

```bash
gt config
```

The menu lets you change:

- **Trunk branch** — the bottom of every stack.
- **Submit settings** — whether new PRs default to draft, whether `gt submit`
  prompts for title/description, etc.
- **Branch naming** — auto-generated branch prefix when `gt create` infers a
  name from the commit message.

Settings persist in `~/.graphite_user_config` (per-user) and
`.graphite_repo_config` (per-repo, committed). Per-repo config wins for the
team-shared knobs (trunk name, target trunk for cross-fork PRs).

## Shell completion

`gt completion` prints a yargs-style completion script to stdout. Append it
to your shell rc:

```bash
gt completion >> ~/.zshrc       # zsh
gt completion >> ~/.bashrc      # bash
```

Reload the shell. `gt log <Tab>`, `gt create <Tab>`, etc. all work afterwards.

## CI considerations

- CI usually doesn't need `gt` at all — keep `gt submit` to local dev.
- If a CI pipeline does call `gt`, populate `~/.graphite_user_config` with a
  token from <https://app.graphite.com/settings/cli> in a CI-only secret so
  no interactive auth step is needed.

## Uninstalling and starting over

```bash
brew uninstall graphite                      # or npm uninstall -g
rm -rf ~/.graphite_user_config
rm .graphite_repo_config                     # in any repo where you ran gt init
```

Re-install from the top of this doc to start clean.

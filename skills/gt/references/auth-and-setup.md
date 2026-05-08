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

```bash
gt auth
```

The first run opens a browser tab and exchanges an OAuth-style token. The
token is stored under `~/.graphite_user_config`. If the browser handoff
fails (headless box, sandbox), use the printed CLI token form:

```bash
gt auth --token <token-from-graphite-dashboard>
```

Re-auth if `gt submit` fails with a 401 — the token has likely expired.

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
`release/*` branch), `gt` may misidentify trunk on first run. Override:

```bash
gt config --trunk main
```

Verify with:

```bash
gt log short
```

The bottom node should be your real trunk.

## Useful config knobs

```bash
# Default to draft PRs on every submit
gt config --submit-draft true

# Disable the auto-comment on each submitted PR
gt config --submit-update false

# Default branch-name template (kebab-case from the commit message)
gt config --branch-prefix "$(whoami)/"
```

These persist in `.graphite_user_config` (per-user) or
`.graphite_repo_config` (per-repo, committed). Repo-level config wins for
the team-shared knobs (trunk name, submit-update default).

## Shell completion

```bash
gt completion zsh  > ~/.zsh/completions/_gt    # zsh
gt completion bash > ~/.bash_completion.d/gt   # bash
gt completion fish > ~/.config/fish/completions/gt.fish
```

Reload the shell after installing. `gt log <Tab>`, `gt create <Tab>`, etc.
all work afterwards.

## Sandbox / CI considerations

- `gt submit` posts PRs by hitting `api.github.com` — same auth scope as the
  `gh` CLI. If `gh auth status` is healthy, `gt` should also work.
- In a sandboxed shell that blocks browser handoff, prefer
  `gt auth --token` over `gt auth`.
- CI usually doesn't need `gt` at all — keep `gt submit` to local dev. If a
  CI pipeline does call it, set `GRAPHITE_TOKEN` in the env and skip
  `gt auth` interactively.

## Uninstalling and starting over

```bash
brew uninstall graphite                      # or npm uninstall -g
rm -rf ~/.graphite_user_config
rm .graphite_repo_config                     # in any repo where you ran gt init
```

Re-install from the top of this doc to start clean.

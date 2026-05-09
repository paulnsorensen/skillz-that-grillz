---
name: safe-settings
model: haiku
description: >
  Set up GitHub's `safe-settings` (a Probot-based GitHub App from the
  github org) for declarative, org-wide repository policy as code. Use when
  the user says "settings as code", "safe-settings", "manage repos
  declaratively", "centralize repo settings", "org-level branch protection",
  "scaffold an admin repo", "github settings app", or invokes
  /safe-settings on an org that wants to manage repo settings, branch
  protection, rulesets, labels, teams, environments, and collaborators
  from one admin repo. Scaffolds the admin-repo layout (`settings.yml` +
  `suborgs/` + `repos/`), the GitHub App installation steps, and a
  scheduled `full-sync` GitHub Actions workflow. Distinct from /gh
  (per-task ops) and /gh-bootstrap (one-shot `gh api` config of a single
  repo) — this skill is for **org-scale settings as code** that
  reconciles continuously.
allowed-tools: Read, Write, Edit, Glob, Bash(gh:*), Bash(git:*)
license: MIT
---

# safe-settings

Onboard [`github/safe-settings`](https://github.com/github/safe-settings) — a Probot GitHub App that reconciles repo settings, branch protection, rulesets, labels, teams, environments, and collaborators from YAML files in a single admin repo.

Use this skill when an org wants to manage repository policy declaratively at scale, instead of clicking through each repo's settings page or running `gh api` one-shot scripts.

## When to reach for safe-settings vs alternatives

| Situation | Tool |
|---|---|
| Single repo, one-time policy lockdown | `/gh-bootstrap` (gh CLI, imperative) |
| Many repos, central policy that must stay in sync, drift detection, dry-run on PR | safe-settings (this skill) |
| Per-task GitHub ops (PRs, issues, CI status) | `/gh` |

safe-settings is **the right answer when policy is shared across repos** and you want PRs + dry-run gating on policy changes themselves. It's overkill for a single repo.

## What gets configured (by safe-settings, after this skill runs)

- Repo metadata (description, homepage, topics, visibility, default branch, feature toggles)
- Merge button settings (squash / merge / rebase, delete-on-merge, auto-merge, update-branch)
- Security toggles (vulnerability alerts, automated security fixes)
- Branch protection (classic) and rulesets (modern)
- Labels (with rename via `oldname`, exclusion patterns)
- Milestones
- Collaborators (per-user, with `include` / `exclude` repo lists)
- Teams (with permissions, visibility)
- Environments and deployment rules
- Custom properties
- Autolinks (Jira-style references)

The same YAML schema is layered org → suborg → repo, with repo-level files taking precedence.

## Protocol

### 1. Pick the admin repo and confirm scope

safe-settings reads its config from a single **admin repo** in the org. Two common layouts:

| Admin repo | When |
|---|---|
| `<org>/.github` | Quickest — the org already has this repo for community health files. Set `ADMIN_REPO=.github`. |
| `<org>/admin` (dedicated) | Cleaner for large orgs where a dedicated policy team owns the repo. Set `ADMIN_REPO=admin`. |

Confirm with the user:

- Org name (`gh api user --jq '.login'` if it's a user, `gh api orgs/<org>` to verify org)
- Admin repo choice (`.github` is the common default)
- Sub-org grouping needed? (e.g. group repos by team for different policy)
- Will deployment be **GitHub Actions cron** (simplest) or **hosted Probot app** (real-time webhooks)?

### 2. Scaffold the admin repo layout

The standard layout under the admin repo:

```
<admin-repo>/
└── .github/
    ├── settings.yml              # org-level baseline — applies to all repos
    ├── suborgs/                  # optional — overrides for groups of repos
    │   └── <suborg-name>.yml
    └── repos/                    # optional — per-repo overrides
        └── <repo-name>.yml
```

Files to copy from `assets/`:

- `assets/settings.yml` → `.github/settings.yml` — minimal, opinionated org baseline (see below)
- `assets/suborg.yml` → `.github/suborgs/example.yml` — commented sub-org template
- `assets/repo.yml` → `.github/repos/example.yml` — commented per-repo override template

The org baseline in `assets/settings.yml` mirrors the gh-bootstrap defaults: squash-only merging, PR-title commit subjects, delete-on-merge, plus a default-branch ruleset with merge queue and required reviews. Read `references/schema.md` for the full key catalog and how the layering works.

### 3. Choose the deployment path

#### GitHub Actions cron (recommended starting point)

- Lowest setup cost — no hosting, no webhook tunnel
- Reconciles every N hours via `npm run full-sync`
- Drift gets fixed within the cron interval, not in real time
- Copy `assets/full-sync.yml` → `<admin-repo>/.github/workflows/safe-settings.yml`

#### Hosted Probot app

- Real-time response to webhook events (push to `.github/settings.yml`, repo created, etc.)
- Needs hosting (AWS Lambda, Docker, Heroku, k8s) and a public URL for webhooks
- Use this once the cron path is proven and the org wants instant reconciliation
- See `references/deploy.md` for the deployment matrix and env-var reference

The skill defaults to the GHA cron path. Only switch to hosted if the user asks.

### 4. Create the GitHub App

Both deployment paths need a GitHub App installation. Walk the user through the manifest flow:

1. Clone safe-settings locally: `git clone https://github.com/github/safe-settings`
2. `cp .env.example .env`, set `GH_ORG=<org>`
3. `npm install && npm run dev`
4. Open the printed URL → "Register a GitHub App" → install on the org
5. Save these values:
   - **App ID** → `vars.SAFE_SETTINGS_APP_ID` (repo or org variable)
   - **Private key** (downloaded `.pem`) → `secrets.SAFE_SETTINGS_PRIVATE_KEY` (paste the file contents)
   - **Client ID** → `vars.SAFE_SETTINGS_GITHUB_CLIENT_ID`
   - **Client secret** → `secrets.SAFE_SETTINGS_GITHUB_CLIENT_SECRET`
   - **Webhook secret** → `secrets.SAFE_SETTINGS_WEBHOOK_SECRET` (only needed for hosted Probot)

The GitHub App needs admin access to the org's repos to manage settings. The manifest flow requests the right permissions automatically — don't hand-roll the permission list.

### 5. Wire and test

For the GHA path:

```bash
# Set the variables and secrets on the admin repo (or org-wide).
gh variable set SAFE_SETTINGS_GH_ORG --body "<org>" --repo "<org>/<admin-repo>"
gh variable set SAFE_SETTINGS_APP_ID --body "<app-id>" --repo "<org>/<admin-repo>"
gh variable set SAFE_SETTINGS_GITHUB_CLIENT_ID --body "<client-id>" --repo "<org>/<admin-repo>"
gh secret set SAFE_SETTINGS_PRIVATE_KEY --body "$(cat private-key.pem)" --repo "<org>/<admin-repo>"
gh secret set SAFE_SETTINGS_GITHUB_CLIENT_SECRET --body "<secret>" --repo "<org>/<admin-repo>"
```

Trigger the first sync manually via `workflow_dispatch`:

```bash
gh workflow run safe-settings.yml --repo "<org>/<admin-repo>"
gh run watch --repo "<org>/<admin-repo>"
```

Check the run output. The first sync logs every change it would apply; subsequent runs are quiet unless something has drifted.

### 6. Validate guardrails

safe-settings comes with two safety nets — make sure both are wired:

- **Dry-run on PRs** — when someone opens a PR against the admin repo modifying settings, the app posts a status check showing what *would* change. Don't merge if the check fails.
- **Validators** — `assets/settings.yml` includes a `validator.pattern` for repo names. Customize it for the org's naming convention so safe-settings refuses to apply settings to mis-named repos.

For a more rigorous setup, add `enforcement: evaluate` on new rulesets to test them in shadow mode before flipping to `active`. See `references/schema.md`.

## Idempotency

safe-settings is reconciliation-shaped — running `full-sync` on an unchanged config is a no-op. The skill itself is also idempotent:

- File scaffolding: diff against `assets/` templates and ask before overwriting any existing file
- Variables/secrets: `gh variable set` and `gh secret set` are idempotent (last write wins); the skill should ask before overwriting an existing value
- The GHA workflow file: same diff-and-ask treatment

Re-running the skill on a partially-set-up admin repo should fill in only the missing pieces.

## What this skill is NOT for

- Setting policy on a single one-off repo with no org behind it — use `/gh-bootstrap`
- Running PR / issue / CI ops — use `/gh`
- Local git work — use `/commit`
- Authoring new safe-settings rules upstream — this skill consumes the released app, doesn't develop it
- Migrating from the older `github/settings` app — safe-settings is the supported successor; the skill assumes you're starting fresh or already off `github/settings`

## Gotchas

- The admin repo path matters. By default safe-settings looks for config under `.github/` in `ADMIN_REPO`; if you put it elsewhere, set `CONFIG_PATH` to the directory.
- The GitHub App needs **admin** access to manage settings on member repos. A user-installed token won't cut it — it has to be the App, installed on the org.
- `full-sync` via GHA touches every repo in the org on every run. For huge orgs (>1000 repos) the run can take an hour and burn Actions minutes — use the hosted Probot path or scope by suborg.
- The same YAML key can mean different things at different layers. `branches` at org level applies to every repo; at repo level it overrides only that repo. The merge order is deep — read `references/schema.md` before writing complex overrides.
- When you change the org-level `settings.yml`, every repo gets the change on next sync. There is no "stage to a few repos first" toggle short of using `enforcement: evaluate` on rulesets.
- `force_create: true` in a `repos/<name>.yml` will create the repo if it doesn't exist. This is powerful and dangerous — use it deliberately.
- safe-settings does not unset every API field by default. To remove a label, listing it under `labels.exclude` is required; to remove a collaborator, you typically delete the entry and run sync. Read the schema before assuming "remove from YAML" means "remove from GitHub".
- The merge queue is a `merge_queue` rule inside a `rulesets` entry — not a top-level toggle. Same shape as `/gh-bootstrap`'s ruleset payload, just expressed in YAML and applied org-wide.

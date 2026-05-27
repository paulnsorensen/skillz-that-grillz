# Dependabot reference

Dependabot has three distinct features. Don't confuse them.

| Feature | Configured by | Purpose |
|---|---|---|
| **Dependabot alerts** | Repo Settings → Code security | Surfaces known-vulnerable dependencies in the repo's dep graph. Free for public repos. |
| **Dependabot security updates** | Repo Settings → Code security | Automatic PRs to fix the alerts above. Free for public repos. |
| **Dependabot version updates** | `.github/dependabot.yml` | Routine "bump everything to the latest minor/patch" PRs on a schedule. The skill scaffolds this. |

This reference covers `.github/dependabot.yml`. The first two are
enabled via `gh api` calls in the skill's protocol step 4.

Schema source of truth:
<https://docs.github.com/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file>.

## Minimum viable file

```yaml
version: 2
updates:
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
```

`github-actions` is the one ecosystem every repo with workflows
should enable. It bumps versions like `actions/checkout@v4` when
new minor/patch releases ship and protects against `actions/*`
references rotting.

## Per-ecosystem keys

| Ecosystem | Key | Detected by |
|---|---|---|
| Bundler (Ruby) | `bundler` | `Gemfile` |
| Cargo (Rust) | `cargo` | `Cargo.toml` |
| Composer (PHP) | `composer` | `composer.json` |
| Docker | `docker` | `Dockerfile` |
| Git submodule | `gitsubmodule` | `.gitmodules` |
| GitHub Actions | `github-actions` | `.github/workflows/*.yml` |
| Go modules | `gomod` | `go.mod` |
| Gradle | `gradle` | `build.gradle` |
| Maven | `maven` | `pom.xml` |
| npm | `npm` | `package.json` (also yarn, pnpm) |
| pip | `pip` | `requirements.txt`, `pyproject.toml` (with PEP 621), `setup.py` |
| pub | `pub` | `pubspec.yaml` |
| Terraform | `terraform` | `*.tf` |
| uv | `uv` | `pyproject.toml` + `uv.lock` |

## Useful options

### Cap open PR volume

```yaml
open-pull-requests-limit: 5
```

Default is 5; raise to 10 for active repos, lower for sleepy ones.

### Group updates to reduce review noise

```yaml
groups:
  actions-minor-and-patch:
    update-types: [minor, patch]
```

Groups bundle multiple updates into one PR. The example above pulls
all minor and patch GitHub Actions updates into a single PR — you
review one PR per week instead of N.

### Limit to security updates only

```yaml
open-pull-requests-limit: 0
```

Setting `open-pull-requests-limit: 0` disables routine version
updates while keeping security updates on. Useful for projects that
don't want noise but still want CVE fixes.

### Allow / ignore

```yaml
allow:
  - dependency-name: "@types/*"
ignore:
  - dependency-name: "lodash"
    versions: ["3.x"]
```

Use sparingly. `ignore` rules tend to outlive their original reason.

### Reviewers + labels

```yaml
reviewers:
  - "octocat"
labels:
  - "dependencies"
```

`reviewers` adds people to every PR; `labels` is mostly for filtering.

### Schedule timing

```yaml
schedule:
  interval: weekly
  day: monday
  time: "09:00"
  timezone: "Europe/London"
```

Without `day` and `time`, Dependabot picks a default. Setting them
gives you predictable PR arrivals.

## Operational notes

- Dependabot opens PRs against the **default branch**. If your
  default isn't `main`, no extra config is needed.
- Dependabot PRs run the same required CI as any other PR, so if
  CI is green, the PR is mergeable. Pair with auto-merge on
  Dependabot label or use [`@dependabot squash and merge`][automerge].
- Dependabot doesn't mass-rebase open PRs; it rebases the most
  recent one when its base moves. Stale PRs get auto-closed and
  reopened on the next schedule.
- The auto-generated PR descriptions include a compatibility score
  and changelog summary — useful for quick review.

[automerge]: https://docs.github.com/code-security/dependabot/working-with-dependabot/managing-pull-requests-for-dependency-updates

## When `version: 2` matters

Always use `version: 2`. `version: 1` is the legacy `.dependabot/`
format and is no longer supported for new repos.

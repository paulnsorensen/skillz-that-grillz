# safe-settings YAML schema

Reference for the keys consumed by `github/safe-settings`. This skill only ships a minimal baseline in `assets/settings.yml`; everything below is what's available when the user wants to extend it.

Authoritative source: <https://github.com/github/safe-settings/blob/main/docs/sample-settings/settings.yml>

## Layering

Three layers, deepest wins:

```
admin/
└── .github/
    ├── settings.yml          # org level — applies to every repo
    ├── suborgs/<name>.yml    # suborg level — overrides for a group of repos
    └── repos/<name>.yml      # repo level — overrides for one repo
```

A key set at repo level overrides the same key at suborg level, which overrides org level. Lists merge by replacement, not concatenation — if you want to add one label at the suborg level, you have to repeat the org-level labels too.

## Top-level keys

| Key | What it manages | Notes |
|---|---|---|
| `repository` | Repo metadata + merge button + security toggles | Maps 1:1 to `PATCH /repos/{owner}/{repo}` |
| `branches` | Classic branch protection | Use `name: default` to target the default branch |
| `rulesets` | Modern rulesets (preferred) | Includes `merge_queue` rule |
| `labels` | Issue/PR labels with `include` / `exclude` | `oldname:` renames an existing label |
| `milestones` | Issue/PR milestones | Rarely needed at org level |
| `collaborators` | Per-user repo access | Use `include` / `exclude` to scope to specific repos |
| `teams` | Team access + permission | Team must already exist in the org |
| `custom_properties` | GitHub custom properties | Used by sub-org property selectors |
| `autolinks` | URL autolinks (e.g. JIRA-123) | Per-repo, but commonly templated org-wide |
| `validator` | Repo-name regex | safe-settings refuses to apply to repos that don't match |
| `environments` | Deployment environments + reviewers | See safe-settings docs for the full shape |

## `repository`

The most common keys. See [the GitHub REST docs](https://docs.github.com/en/rest/repos/repos) for the exhaustive list — every field on `PATCH /repos/{owner}/{repo}` is settable here.

| Key | Type | Notes |
|---|---|---|
| `private` / `visibility` | bool / string | `visibility` supports `internal` on Enterprise |
| `description`, `homepage`, `topics` | string / list | Public-facing metadata |
| `default_branch` | string | Renames the default branch — use carefully |
| `has_issues`, `has_projects`, `has_wiki` | bool | Feature toggles |
| `allow_squash_merge`, `allow_merge_commit`, `allow_rebase_merge` | bool | Merge button gating |
| `allow_auto_merge`, `allow_update_branch`, `delete_branch_on_merge` | bool | Merge ergonomics |
| `squash_merge_commit_title` | enum | `PR_TITLE` or `COMMIT_OR_PR_TITLE` |
| `squash_merge_commit_message` | enum | `PR_BODY`, `COMMIT_MESSAGES`, or `BLANK` |
| `security.enableVulnerabilityAlerts` | bool | Dependabot alerts |
| `security.enableAutomatedSecurityFixes` | bool | Dependabot security PRs |
| `archived` | bool | `false` here will unarchive a previously-archived repo |
| `force_create` | bool | (repo-level only) creates the repo if missing |
| `template` | string | (repo-level only) template repo to seed from |

## `branches` (classic protection)

Use `rulesets` for new setups; `branches` is here for back-compat with orgs already on classic protection.

```yaml
branches:
  - name: default               # 'default' is a special token = the default branch
    protection:
      required_pull_request_reviews:
        required_approving_review_count: 1
        dismiss_stale_reviews: true
        require_code_owner_reviews: false
      required_status_checks:
        strict: false
        contexts: ["ci"]
      enforce_admins: false
      restrictions: null         # null = no push restrictions
```

## `rulesets`

Preferred over `branches` for new setups. Same payload shape as the GitHub REST endpoint, with merge queue as a first-class rule.

Conditions:

```yaml
conditions:
  ref_name:
    include: ["~DEFAULT_BRANCH"]   # special tokens: ~DEFAULT_BRANCH, ~ALL
    exclude: []
  # Org-level only — drop this at suborg/repo level:
  repository_name:
    include: ["~ALL"]
    exclude: ["legacy-*"]
    protected: true                # block renaming target repos
```

Enforcement levels:

| Value | Effect |
|---|---|
| `disabled` | Defined but inert. Use during initial authoring. |
| `evaluate` | "Shadow mode" — failures are reported but don't block. **Use this for new rulesets** before flipping to `active`. |
| `active` | Enforced. |

Rule types (the common ones):

| Type | Parameters |
|---|---|
| `deletion` | (none) — blocks branch deletion |
| `non_fast_forward` | (none) — blocks force-push |
| `creation` | (none) — blocks branch creation matching the condition |
| `update` | `update_allows_fetch_and_merge` |
| `required_linear_history` | (none) |
| `required_signatures` | (none) — requires signed commits |
| `required_deployments` | `required_deployment_environments: [list]` |
| `pull_request` | review count, dismiss-stale, code-owners, last-push approval, thread resolution |
| `required_status_checks` | `strict_required_status_checks_policy`, list of `{context, integration_id?}` |
| `merge_queue` | `merge_method: SQUASH`, grouping strategy, queue sizing |
| `workflows` | List of `{path, repository_id, ref}` — required GHA workflows |
| `commit_message_pattern`, `commit_author_email_pattern`, `committer_email_pattern`, `branch_name_pattern`, `tag_name_pattern` | `name`, `negate`, `operator` (one of `starts_with`/`ends_with`/`contains`/`regex`), `pattern` |

Bypass actors:

```yaml
bypass_actors:
  - actor_id: 1
    actor_type: OrganizationAdmin   # or RepositoryRole | Team | Integration
    bypass_mode: pull_request       # or always
```

## `labels`

```yaml
labels:
  include:
    - name: bug
      color: "d73a4a"
      description: Something isn't working
    - name: feature
      color: "#336699"           # leading # is optional; quote either way
      description: New functionality
    - name: enhancement
      oldname: Help Wanted        # renames an existing label
      color: "326699"
  exclude:
    - name: ^release             # regex — don't touch labels matching this
```

## `collaborators` and `teams`

```yaml
collaborators:
  - username: alice
    permission: push             # pull | push | admin
    include: ["api-*"]           # only these repos
  - username: bob
    permission: pull
    exclude: ["actions-demo"]    # all repos except these

teams:
  - name: core
    permission: admin
  - name: platform
    permission: push
    visibility: closed           # only honored on team creation
```

## `validator`

```yaml
validator:
  pattern: "[a-z0-9][a-z0-9-]*"   # lowercase, kebab-case repo names only
```

If a repo's name doesn't match, safe-settings refuses to apply settings and logs an error. Useful for catching typos in `.github/repos/<name>.yml`.

## `custom_properties` and sub-org property selectors

`custom_properties` sets values on a repo:

```yaml
custom_properties:
  - name: tier
    value: critical
```

Sub-org files can then select repos by property:

```yaml
suborgproperties:
  - tier: critical
```

This is the cleanest way to apply policy to a logical group of repos without enumerating them by name.

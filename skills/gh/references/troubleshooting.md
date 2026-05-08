# `gh` troubleshooting

Common errors, diagnostics, and recovery paths.

## Authentication

| Error | Fix |
|---|---|
| `gh: To get started... gh auth login` | `gh auth login` (or `gh auth login --with-token < token.txt`) |
| `HTTP 401: Bad credentials` | `gh auth refresh`, then re-run the command |
| `HTTP 403: Resource not accessible by personal access token` | Missing scope. `gh auth refresh -h github.com -s repo,workflow,admin:org` |
| Authenticated as wrong user | `gh auth status`, then `gh auth login --hostname github.com` to switch |

Common scopes:

- `repo` — read/write on private repositories
- `workflow` — modify GitHub Actions workflows
- `admin:org` — manage org settings, teams
- `write:packages` — publish to GitHub Packages

## Permissions

```bash
# Confirm what you can do on a repo
gh api repos/owner/repo --jq '.permissions'

# Re-bind git to use gh credentials (when push fails)
gh auth setup-git
git remote -v
```

If `gh repo view owner/repo` returns `HTTP 404` despite the repo
existing, you are either signed in as the wrong user or you do not have
access. `gh auth status` shows the current login.

## Rate limiting

```bash
gh api rate_limit                                # check headroom
gh api rate_limit --jq '.rate.reset' \
  | xargs -I {} date -r {}                       # human-readable reset time
```

- Authenticated requests: 5000/hour
- Unauthenticated: 60/hour
- Secondary rate-limit (`exceeded a secondary rate limit`): slow down,
  add small delays between calls, avoid `--paginate` over huge result sets

## Common command errors

### "PR for branch already exists"

```bash
gh pr list --head feature                        # find the existing PR
gh pr edit <number> --title "..." --body-file body.md
```

### "Pull Request is not mergeable"

```bash
gh pr checks <number>                            # CI failing?
gh pr view <number> --json reviewDecision        # review missing?
gh api repos/owner/repo/branches/main/protection # protection rules?

# If conflicts:
gh pr checkout <number>
git merge main                                   # resolve conflicts
git push
```

### "could not resolve to a Workflow"

```bash
gh workflow list                                 # find exact file name
gh workflow run ci.yml                           # use file name, not display name
```

### "could not resolve to a PullRequest/Issue"

```bash
gh repo view                                     # confirm current repo
gh repo set-default                              # if multiple remotes
gh pr view <number> --repo owner/repo            # be explicit
```

### "no default repository has been set"

```bash
gh repo set-default                              # interactive
gh repo set-default owner/repo                   # explicit
```

## Diagnostics

```bash
gh --version
gh auth status
gh config list
gh repo view                                     # shows current repo

# Verbose HTTP trace
GH_DEBUG=api gh pr list

# OAuth flow trace
GH_DEBUG=oauth gh auth login

# Combined
GH_DEBUG=api,oauth gh <command>
```

## Network

| Error | Fix |
|---|---|
| `dial tcp: i/o timeout` | Check `ping github.com` and proxy/VPN |
| `x509: certificate signed by unknown authority` | Update CA certs (`brew install ca-certificates`); set `GH_CA_BUNDLE=/path/to/ca-bundle.crt` if behind a corporate proxy |

## Error codes at a glance

- **401** — authentication failed (refresh or re-login)
- **403** — forbidden / missing scopes
- **404** — not found, wrong repo, or no access
- **422** — validation failed; the request body or flags are wrong
- **500** — GitHub-side; retry later

## Last resort

```bash
gh auth logout
gh auth login
```

Then close and reopen your terminal so any cached state in environment
variables (`GITHUB_TOKEN`, `GH_TOKEN`) is dropped.

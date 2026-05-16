# `gh` extras

> **When to read:** When the user asks about labels, codespaces, gists,
> Actions secrets/variables, Projects, aliases, the `gh status` page,
> repo rulesets, or attestations — anything beyond the SKILL.md core
> domains. Also consult here before reaching for `gh api`.

Less common but useful `gh` subcommands. SKILL.md links here on demand.

## Labels

```bash
gh label list                                    # repo labels

gh label create "priority: high" --color FF0000 --description "Block on this"
gh label edit "bug" --color FFAA00 --description "Something isn't working"

gh label clone owner/source-repo                 # copy labels from another repo
```

## Codespaces

```bash
gh codespace list
gh codespace create --repo owner/repo
gh codespace ssh                                 # SSH into the active codespace
gh codespace code                                # open in VS Code
gh codespace cp local.txt remote:~/path/         # copy file in
gh codespace cp remote:~/path/file.txt .         # copy file out
gh codespace logs
```

## Gists

```bash
gh gist create file.txt
echo "content" | gh gist create -                # from stdin
gh gist list
gh gist view <gist-id>
gh gist edit <gist-id>
```

## Secrets and variables (Actions)

```bash
gh secret list                                   # repo secrets (admin only)
gh secret set SECRET_NAME --body "value"
gh secret set SECRET_NAME < secret.txt
gh secret list --org my-org                      # org-level

gh variable list
gh variable set VAR_NAME --body "value"
```

## Projects

```bash
gh project list --owner my-org
gh project view <number>
gh project create --owner my-org --title "Q3 roadmap"
gh project item-list <number> --query "status:Done"
```

## Aliases

```bash
gh alias set pv "pr view"
gh alias set bugs "issue list --label bug"
gh alias list
gh pv 123                                        # uses the alias
```

## API access (last resort)

When a named subcommand exists, prefer it. `gh api` is only for endpoints
that have no subcommand wrapper:

```bash
gh api rate_limit                                # rate limit status
gh api repos/:owner/:repo/topics                 # topics aren't covered by subcommands
gh api --paginate repos/:owner/:repo/issues      # auto-paginate
gh api repos/:owner/:repo/issues -f title=Bug -f body=Detail  # POST with form fields
```

`GH_DEBUG=api gh ...` prints every underlying HTTP call — useful when
debugging an unexpected response.

## Extensions

```bash
gh extension list
gh extension install owner/gh-extension
gh extension upgrade --all
```

Extensions are user-side `gh` plugins. The skill itself does not require
any extensions.

## Status, browse, config

```bash
gh status                                        # mentions / review requests
gh browse                                        # open current repo
gh browse src/main.go:42                         # open file at line
gh browse --actions                              # open Actions tab

gh config list
gh config set editor vim
gh config set git_protocol ssh
```

## Repo rulesets

```bash
gh ruleset list
gh ruleset view <id>
```

## Attestations / supply-chain verification

```bash
gh release verify v1.0.0                         # verify release attestation
gh release verify-asset dist/cli --repo owner/repo
```

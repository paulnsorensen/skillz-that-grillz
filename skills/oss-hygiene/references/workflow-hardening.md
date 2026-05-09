# GitHub Actions hardening reference

Two Scorecard checks specifically inspect workflow files:

- `Token-Permissions` — flags workflows that grant the
  `GITHUB_TOKEN` more than they need.
- `Dangerous-Workflow` — flags patterns that let an attacker
  execute code with elevated privileges, primarily by abusing the
  `pull_request_target` event.

The skill performs **detection only** for both. Tightening is per-
workflow, often per-step, and the changes can break unrelated
behaviour if applied blindly. This reference is the playbook for
acting on the findings the skill prints.

## `Token-Permissions`

`GITHUB_TOKEN` is automatically minted for every workflow run. By
default it inherits the **permissive** scopes the org or repo allows
(historically `write` on contents, packages, etc.). Best practice is
to explicitly declare `permissions:` and grant only what each job
needs.

### The safe default

At the top of every workflow file:

```yaml
permissions:
  contents: read
```

Then per job, opt up only when needed:

```yaml
jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write   # softprops/action-gh-release
      id-token: write   # OIDC-based publishers (npm, PyPI, sigstore)
    steps:
      - ...
```

Job-level `permissions:` *replaces* the top-level block — it does
not merge. List every scope the job needs.

### Common scopes and when to grant `write`

| Scope | Grant `write` for |
|---|---|
| `contents` | Creating releases, pushing tags, committing back to the repo |
| `pull-requests` | Commenting on PRs, requesting reviewers, applying labels |
| `issues` | Opening or commenting on issues from a workflow |
| `id-token` | OIDC publishing (npm provenance, PyPI trusted publishing, sigstore) |
| `pages` | Deploying to GitHub Pages |
| `packages` | Publishing to GHCR or GitHub Packages |
| `security-events` | Uploading SARIF (CodeQL, scorecard) |
| `actions` | Cancelling other runs, updating action caches |

Scope reference: <https://docs.github.com/actions/security-guides/automatic-token-authentication#permissions-for-the-github_token>.

### Anti-patterns to flag

- `permissions: write-all` — the worst case; grants everything.
- No `permissions:` block at all — inherits whatever the org allows,
  often more than needed.
- `permissions: { contents: write }` at the top level when only one
  release job needs it — push it down to the job.

## `Dangerous-Workflow`

The headline pattern: **`pull_request_target` + `actions/checkout`
of the head commit + arbitrary code execution**.

`pull_request_target` runs the workflow with the **base** repo's
secrets and write tokens, but the diff and code being tested
typically come from the PR's head — which is attacker-controlled.
Checking out the head SHA and running its scripts gives an attacker
code execution with the base repo's secrets. This is how several
real-world supply-chain compromises started.

### The pattern to never write

```yaml
# DANGEROUS — do not copy this.
on: pull_request_target
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}   # PR head
      - run: npm install && npm test                      # PR scripts
```

The PR can change `package.json`'s `postinstall` to exfiltrate
secrets the moment `npm install` runs.

### Safe alternatives

| If you need... | Use this instead |
|---|---|
| To run CI on PRs from forks without secrets | Plain `pull_request` event (no secrets, no write token) |
| To label or comment on PRs from workflows | `pull_request_target` with **no checkout of head** — use `github.event.pull_request.*` data only |
| To run CI on PRs *with* secrets, gated on an approval | `workflow_run` triggered by the no-secrets PR workflow, gated on a maintainer label |
| To deploy preview environments for PRs | A separate `deployment` workflow gated on a label |

### Other dangerous patterns

- **Interpolating `github.event.*` strings into a `run:` block.**
  Issue titles, PR titles, branch names, and comment bodies are
  attacker-controlled. Quoting them into shell is a command-injection
  primitive. Pass them via env vars instead:

  ```yaml
  - run: echo "$TITLE"
    env:
      TITLE: ${{ github.event.pull_request.title }}
  ```

- **Unpinned third-party actions.** `uses: some-org/action@main`
  re-resolves on every run; if upstream gets compromised, you
  inherit the compromise. Pin to a SHA or a release tag.
- **Self-hosted runners on public repos** without isolation. PRs
  from forks run code on your runner.

## Acting on the skill's audit findings

The skill prints a list. For each entry:

1. **Skip** if the workflow only touches things that genuinely
   need the broader scope (releases, deploy keys, etc.).
2. **Tighten** by adding `permissions: { contents: read }` at the
   top and bumping individual jobs that need more.
3. **Refactor** dangerous-workflow patterns. This is real work; do
   it in a focused PR with reviewers who understand the repo.

Don't bulk-apply a tightening commit across many workflows — review
each one. False positives are common: a release workflow that
genuinely needs `contents: write` is fine.

## Further reading

- GitHub: [Hardening for GitHub Actions][harden]
- OpenSSF: [Scorecard checks doc][checks]
- Step Security: [Secure-repo guidance][stepsec]

[harden]: https://docs.github.com/actions/security-guides/security-hardening-for-github-actions
[checks]: https://github.com/ossf/scorecard/blob/main/docs/checks.md
[stepsec]: https://github.com/step-security/secure-repo

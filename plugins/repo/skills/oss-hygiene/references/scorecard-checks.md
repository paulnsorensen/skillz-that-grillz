# OpenSSF Scorecard checks reference

Scorecard runs ~18 automated checks against a public GitHub repo.
Each check returns a score from 0 to 10. The aggregate score is the
weighted average. Source of truth:
<https://github.com/ossf/scorecard/blob/main/docs/checks.md>.

This table records what the `oss-hygiene` skill scaffolds for each
check, what it leaves to the user, and the rationale. "Risk" is the
risk weighting Scorecard itself uses.

| Check | Risk | Skill action | Rationale |
|---|---|---|---|
| `Binary-Artifacts` | High | Scan tree for `.so .dll .jar .exe .zip .tgz`; warn on findings | Pinning binaries in source is a supply-chain footgun; surface, don't auto-delete |
| `Branch-Protection` | High | None (handled by `/gh-bootstrap`) | Already covered by the ruleset |
| `CI-Tests` | Low | None | Repo's existing CI already provides this signal |
| `CII-Best-Practices` | Low | Print badge registration link | Self-attested; only the maintainer can answer the questions |
| `Code-Review` | High | None (handled by `/gh-bootstrap`) | PR rule with required approving review is in the ruleset |
| `Contributors` | Low | None | Passive; emerges from real activity over time |
| `Dangerous-Workflow` | Critical | Audit existing workflows for `pull_request_target` + checkout-of-head | Refactoring is repo-specific; warn rather than auto-fix |
| `Dependency-Update-Tool` | High | Scaffold `dependabot.yml` | Always useful; per-ecosystem entries commented out for the user to enable |
| `Fuzzing` | Medium | Report only | Wiring is language-specific (cargo-fuzz, OSS-Fuzz, jazzer, etc.) |
| `License` | Low | Surface missing `LICENSE` | License choice is a decision; don't pick one for the user |
| `Maintained` | High | None | Passive; needs 90 days of activity to score |
| `Packaging` | Medium | Report only | Depends on the project's release pipeline |
| `Pinned-Dependencies` | Medium | Report only | Per-ecosystem and contentious (SHA pinning vs. tags vs. `~`) |
| `SAST` | Medium | Scaffold `codeql.yml` when applicable | Only CodeQL-supported languages; otherwise skipped |
| `Security-Policy` | Medium | Scaffold `SECURITY.md` | Surface 1 of the skill |
| `Signed-Releases` | High | Report only | Sigstore / cosign wiring is project-specific |
| `Token-Permissions` | High | Audit workflows for missing or `write-all` `permissions:` blocks | Tightening is per-workflow; warn rather than auto-edit |
| `Vulnerabilities` | High | None | Dependabot alerts handle this passively |
| `Webhooks` | Critical | None | Org-level concern; not relevant to a single public repo |

## Reading a Scorecard report

After the workflow runs, results are visible at:

```
https://scorecard.dev/viewer/?uri=github.com/<owner>/<repo>
```

The badge URL (use in README):

```
https://api.scorecard.dev/projects/github.com/<owner>/<repo>/badge
```

A score of `7+` is considered solid; `8+` is strong; `9+` is rare and
usually indicates active investment in supply-chain hardening.

## What pulls the score down on small or new repos

- `Maintained` requires 90 days of commit activity; new repos score 0
  here until they age in.
- `Code-Review` looks for at least one approving review on recent PRs.
  Solo repos with `0` required reviews will score lower here even
  though the ruleset is active.
- `Contributors` rewards multi-org diversity; small projects score 0.
- `Dependency-Update-Tool` flips to passing as soon as Dependabot
  opens its first PR — installing the YAML is necessary but not
  sufficient until it has a chance to run.

These are features, not bugs. Don't try to game them.

## How the skill maps to the score

After running `/oss-hygiene` on a public repo with `/gh-bootstrap`
already applied, you should expect to see green check marks on:

- `Branch-Protection`
- `Code-Review` (gated on the review-count > 0; solo repos with 0
  required reviews will still see this go yellow)
- `Dependency-Update-Tool`
- `Security-Policy`
- `License` (assuming the user added one)
- `Token-Permissions` (only after the user acts on the audit)
- `SAST` (when CodeQL is wired)

Everything else is either out of scope, language-specific, or accrues
naturally over time.

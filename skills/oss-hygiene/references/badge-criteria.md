# OpenSSF Best Practices Badge — criteria reference

The Best Practices Badge (formerly CII Best Practices) is a
self-attestation programme run by the OpenSSF. Maintainers answer a
questionnaire about how the project is run; meeting the criteria
unlocks the badge at one of three levels: **Passing**, **Silver**,
or **Gold**.

Source of truth:
<https://www.bestpractices.dev/en/criteria>.

Registration link (pre-filled with the repo URL):

```
https://www.bestpractices.dev/en/projects/new
```

This skill does **not** fill out the questionnaire — registration is
a credential and judgment step that requires the maintainer's
attestation. What the skill does is scaffold the artifacts each
question asks about, so the answers are easy to give truthfully.

## Passing — the level worth aiming for

The Passing level has roughly 70 questions. The ones that map
directly to repo artifacts the skill scaffolds:

| Criterion | Question (paraphrased) | Repo artifact |
|---|---|---|
| `documentation_basics` | Is there a clear description of what the project does? | `README.md` |
| `documentation_interface` | Is there documentation of the interface(s)? | `README.md` / docs |
| `license_location` | Is there a `LICENSE` file? | `LICENSE` |
| `floss_license` | Is the licence FLOSS? | `LICENSE` content |
| `discussion` | Is there a place for discussion? | GitHub Discussions or Issues |
| `english` | Is project documentation in English? | `README.md` etc. |
| `build` | Is the build process documented? | `CONTRIBUTING.md` |
| `vulnerability_report_process` | Is the vulnerability reporting process documented? | `SECURITY.md` |
| `vulnerability_report_response` | Do you respond to reports promptly? | Process commitment in `SECURITY.md` |
| `coding_standards` | Are coding standards documented? | `CONTRIBUTING.md` / linters |
| `test_policy` | Is there a documented test policy? | `CONTRIBUTING.md` / CI |
| `release_notes` | Are release notes published? | `.github/release.yml` (covered by `/gh-bootstrap`) |
| `static_analysis` | Is static analysis used? | `.github/workflows/codeql.yml` |
| `dynamic_analysis` | Is dynamic analysis used? | Optional; many small projects skip |
| `crypto_practices` | Are cryptography best practices followed? | Project-specific |
| `signed_releases` | Are releases cryptographically signed? | Out of scope for the skill |

## Silver and Gold

Silver and Gold add stricter requirements: documented governance,
security-trained reviewers, two-factor for committers, signed
releases, fuzz testing, etc. These are rarely worth chasing for a
small project; aim for Passing first and accumulate the rest if and
when the project warrants it.

## How the badge improves Scorecard

Once the project has a Best Practices Badge ID (even at the
*in-progress* level), the Scorecard `CII-Best-Practices` check picks
it up and scores accordingly:

| Badge level | Scorecard score |
|---|---|
| None | 0 |
| In progress | 2 |
| Passing | 5 |
| Silver | 7 |
| Gold | 10 |

Registering at the in-progress level immediately bumps the score
from 0 to 2 — the lowest-effort win on the supply-chain checklist.

## Adding the badge to README

Once registered, copy the markdown snippet from
`assets/README-badge-snippet.md` and replace `<id>` with your
project ID.

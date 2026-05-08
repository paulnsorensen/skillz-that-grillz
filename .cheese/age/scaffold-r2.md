# Age Report — scaffold-r2

## Orientation

Re-review of the cured working diff (six fixes applied after `.cheese/age/scaffold.md`).
Touched files since the previous report: `.github/workflows/validate.yml`,
`.markdownlint.jsonc`, `README.md`, `skills/commit/SKILL.md`,
`skills/gt/SKILL.md`, plus the new `tests/bash/test_install.bats` (347
lines, 45 tests). `scripts/install.sh` itself was not modified.

## High-stake findings

_None._

## Medium-stake findings

- **[deslop]** `README.md:76`, `README.md:244`, `skills/gt/SKILL.md:40`
  — Three stragglers still describe `/gt` as a "placeholder" while the
  prominent callout in `skills/gt/SKILL.md:13-19` and the README skills
  table now say "reserved slot — not yet implemented". The two terms
  mean the same thing, but the inconsistency dilutes the very
  visibility-bump the cure round was meant to deliver. Pick one term
  (recommend "reserved slot") and apply it to all three remaining call
  sites; or alternatively settle on "placeholder" and revert the
  recently-renamed banner. Either is fine — just be consistent.

- **[deslop]** `.github/workflows/validate.yml:38` — Job is still named
  `lint install.sh (macOS)` even though the job now also runs the
  `bats` unit suite and the dry-run smoke step. Stale label that was
  accurate when the job only ran shellcheck. Rename to
  `test install.sh (macOS)` or `install.sh checks (macOS)` so the
  CI status page accurately describes what failed.

- **[assertions]** `tests/bash/test_install.bats` — Suite covers
  `sg_brew_install_if_missing` for individual tools but does not
  exercise the `sg_install_tools` dispatcher loop that drives them.
  `easy-cheese` has equivalent `ec_install_tools` tests that confirm
  the loop visits each comma-separated entry. Add 1-2 cases:
  `sg_install_tools "gh,just"` should produce the right brew
  invocations in `$STUB_LOG`; `sg_install_tools "graphite"` should
  emit the tap-spec form. Small gap, easy fill.

- **[correctness]** `tests/bash/test_install.bats:30-37` — `make_stub`
  uses an unquoted heredoc (`<<STUB ... STUB`) so `$name` and
  `$exit_code` interpolate at write time. That is the intent and works
  fine for the current call sites, but if a future test ever passes a
  stub name containing `$` or `\``, the heredoc will silently
  interpolate that too. Easy-cheese has the same pattern; not a bug
  today. Optionally tighten by moving the runtime portion to a
  single-quoted heredoc and `printf`-ing the parameterised parts, or
  add an inline comment warning future authors not to pass `$`-bearing
  names.

- **[deslop]** `skills/gt/SKILL.md:14` — The `> [!IMPORTANT]` GFM alert
  is great on github.com but renders as a plain blockquote on most
  other markdown viewers (including the harness's skill preview).
  Consider following it with a plain bold line so the message survives
  rendering downgrades — e.g. `> **🚧 Reserved slot — not yet
  implemented.**` repeated outside the alert. Marginal — only worth
  fixing if you expect the SKILL.md to be previewed outside GitHub.

## Confidence

high — every check ran clean locally (14/14 validator unit tests, 5/5
SKILL.md valid, shellcheck clean, 45/45 bats tests, yamllint clean,
markdownlint 0 errors across 13 files, install.sh dry-run produces the
expected output). The graphite npm + brew tap paths were externally
verified during the cure round. No evidence sources unavailable.

## Next step

```text
/cure scaffold-r2   — pick findings to fix
```

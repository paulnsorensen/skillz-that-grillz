# Age Report — scaffold

## Orientation

Scaffolds the `skillz-that-grillz` repo from scratch on top of an empty
initial commit: 4 dotfiles-sourced skills (`commit`, `gh`, `justfile`,
`prek`), 1 reserved-slot skill (`gt`), a macOS `install.sh` that wraps the
four CLI tools (gh, just, prek, graphite), validator scripts and tests
copied from `easy-cheese`, two GitHub Actions workflows, lint configs, and
a README. ~2.6k lines across 22 files.

## High-stake findings

_None._

## Medium-stake findings

- **[correctness]** `.github/workflows/validate.yml:50-53` — Step is named
  `Real install smoke test (--skip-mcp, --dry-run)` and the comment above
  it claims "Real (un-stubbed) smoke test on a fresh macos-latest runner",
  but the command is `bash scripts/install.sh --skip-mcp --dry-run` — the
  `--dry-run` flag short-circuits every `brew install` and `gh skill
  install`, so this is a parse-and-stub-path test, not a real install.
  In `easy-cheese`, the equivalent step ran without `--dry-run` and
  caught real "phantom formula" / broken-tap regressions. Either rename
  the step (e.g. "install.sh dry-run") and accept the weaker coverage,
  or drop `--dry-run` and let CI exercise the actual `brew install
  withgraphite/tap/graphite` + `gh skill install` paths.

- **[correctness]** `README.md:240-244` and `scripts/install.sh:35` —
  Graphite is installed via the `withgraphite/tap/graphite` Homebrew tap
  and the README also offers `npm install -g @withgraphite/graphite-cli`
  as an alternative. The brew tap path is verifiable from upstream docs;
  the npm package name was not directly verified during scaffold. Confirm
  both before tagging a release — a typo here means `gt` never installs
  on a fresh machine.

- **[assertions]** `scripts/install.sh` (entire file) — The 436-line
  installer ships with no unit tests. `easy-cheese`'s equivalent had a
  ~770-line `tests/bash/test_install.bats` suite that exercised every
  argv-parsing branch, MCP routing path, and selection-validation rule
  with stubbed binaries. Here, the only CI evidence is `shellcheck` and
  the `--dry-run --skip-mcp` smoke step (see finding above). Argv-parsing
  edge cases (`--tools=`, empty `--harness`, unknown selection) are not
  exercised. Either port a trimmed bats suite or extend the in-CI smoke
  step to call `sg_parse_args` / `sg_validate_selection` against a
  representative table of inputs.

- **[deslop]** `.markdownlint.jsonc:14-26` — All 8 rule disables were
  carried over from `easy-cheese` (`MD013`, `MD022`, `MD031`, `MD032`,
  `MD033`, `MD037`, `MD040`, `MD041`, `MD060`). The current 13 markdown
  files in this repo lint clean even with stricter defaults; several of
  these disables (`MD022`/`MD031`/`MD032` for blank-line spacing,
  `MD060` for table column alignment) may be unnecessary for the
  narrower content here. Optional cleanup — re-enable each rule
  individually and keep only the disables that actually fire on this
  repo's files.

- **[spec]** `skills/gt/SKILL.md` — The reserved-slot skill is explicitly
  requested ("doesn't exist but put a placeholder for now") so spec
  adherence is satisfied, but the directory now exists and the installer
  happily ships it to users. Anyone invoking `/gt` will land on the
  reserved-slot body. Track this somewhere visible (issue tag, repo
  project board) so it does not sit indefinitely; consider gating its
  inclusion in the default `gh skill install` loop until the real
  protocol is written.

- **[deslop]** `skills/commit/SKILL.md:42, 56, 74` — Example commit
  messages hard-code `Co-Authored-By: Claude Sonnet 4.6` and the gotcha
  note acknowledges "Co-Authored-By model name drifts as models change".
  Not a bug — the skill itself flags the drift — but the example will
  get more out-of-date as time passes. Consider replacing with a generic
  `Co-Authored-By: Claude <noreply@anthropic.com>` form, or parameterize
  via `{model}`.

## Confidence

medium-high — every file was read, every local lint/validator/shellcheck
ran clean, the install.sh dry-run produces the expected output, and the
removed easy-cheese references were swept for. Two evidence sources were
not exercised: (1) a real (non-dry-run) `bash scripts/install.sh
--skip-mcp` on a fresh macOS, and (2) external verification of the
`@withgraphite/graphite-cli` npm package name.

## Next step

```text
/cure scaffold   — pick findings to fix
```

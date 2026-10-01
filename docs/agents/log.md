# Ingest Log

## Log


- 2026-09-23 · a1d28331d1b8715c · skipped-near-duplicate · agent-friendly-cli-design.md · cyclopts "Did you mean" correction already present verbatim in the Libraries table
- 2026-09-23 · a1d28331d1b8715c · merged · agent-friendly-cli-design.md · TOON output line superseded ("No independent measurement was found") by independent TOON benchmarks
- 2026-09-23 · a1d28331d1b8715c · new-page · toon-versus-json-for-llms.md · TOON versus JSON model accuracy, generation reliability, and vendor support
- 2026-09-23 · a1d28331d1b8715c · new-page · sources/improvingagents-toon-benchmarks.md · Improving Agents "TOON Benchmarks" source page
- 2026-09-23 · a1d28331d1b8715c · new-page · sources/notation-matters-token-optimized-formats.md · arXiv:2605.29676 "Notation Matters" source page
- 2026-09-23 · a1d28331d1b8715c · new-page · sources/toon-format-spec.md · toon-format README and vendor benchmark source page


- 2026-09-23 · pr86-coderabbit · merged · toon-versus-json-for-llms.md, sources/improvingagents-toon-benchmarks.md, agent-friendly-cli-design.md · review corrections: scope benchmark claims, remove unsupported training-data claim, attribute vendor claims to the secondary summary, toon-python is an encoder and decoder


- 2026-09-23 · bad68b8175321818 · merged · decisions/fromargs-cli-library.md · Cure preserved suggestions for leading flags and resolved nested async backends


- 2026-09-24 · fromargs-app-redesign · merged · decisions/fromargs-cli-library.md · ADR-001 and ADR-003 amended for the App redesign; ADR-005 added for decorators and forced JSON output


- 2026-09-24 · wedge-skill-packaging · new-page · decisions/wedge-skill-packaging.md · content key, release-asset publishing, reproducible builds, launcher trust model, and known limits for the wedge packaging tool

- 2026-09-25 · fdf83ace7cfe13b7 · merged · decisions/wedge-skill-packaging.md · Strip host-bound installed scripts and bump the asset format to 2 after hosted CI exposed cross-host hash drift

- 2026-09-25 · cdb0090a33349460 · merged · decisions/wedge-skill-packaging.md · Disable ZIP compression and bump the asset format to 3 after cross-host hash drift remained

- 2026-09-25 · 314e588 · merged · decisions/wedge-skill-packaging.md · Sort ZIP entries after shiv, bump asset format to 4, and verify macOS lock hash on hosted Linux CI

- 2026-09-24 · fromargs-publish-prep · merged · decisions/fromargs-cli-library.md · ADR-006 added for the tag-driven PyPI publish workflow

- 2026-09-26 · release-lanes · merged · decisions/fromargs-cli-library.md · Kept `v[0-9]*` for skills and future Wedge; secured both tag namespaces and registered the pending `fromargs` PyPI publisher

- 2026-09-26 · pr89-compatibility · merged · decisions/fromargs-cli-library.md, decisions/wedge-skill-packaging.md · Preserved independent fromargs and Wedge distributions after PR #88, and aligned Wedge build inputs with the fromargs release path

- 2026-09-26 · fromargs-v0.1.0 · merged · decisions/fromargs-cli-library.md · Recorded the approved first PyPI publication and the successful release workflow

- 2026-09-26 · pr89-trim · merged · decisions/wedge-skill-packaging.md · Export the skill closure from lib/fromargs/uv.lock so shiv's pip/setuptools/click stop shipping in every .pyz; publish only from the post-merge wedge workflow

- 2026-09-26 · pr89-cure · updated · decisions/wedge-skill-packaging.md · Recorded format 6 metadata normalization across umask 002/022, symlink and lock publication invariants, and no shared concurrency for pending-run safety

- 2026-09-26 · wedge-action · merged · decisions/wedge-skill-packaging.md · ADR-001 and ADR-003 amended for project-relative keys; ADR-007 added for portable wedge.toml projects, format 5, and the public actions/wedge composite action

- 2026-09-26 · pr93-integration · updated · decisions/wedge-skill-packaging.md · Integrated the portable project model with format 7 and the #89 validation and reproducibility safeguards
- 2026-09-26 · pr93-hardening · updated · decisions/wedge-skill-packaging.md · Recorded dependency overwrite protection, non-sudo gh installation, exported runner PATH, and failed-result preservation

- 2026-09-26 · wedge-content-digest · merged · decisions/wedge-skill-packaging.md · ADR-008: pin a digest over uncompressed contents, deflate assets, name them by that digest; bump the asset format to 8

- 2026-09-27 · wedge-mac-path · updated · decisions/wedge-skill-packaging.md · ADR-003 and ADR-008: build with `--no-cache`; publish rebuilds before it trusts an existing asset; record the hardlinked-cache drift evidence

- 2026-09-27 · wedge-fanout · updated · decisions/wedge-skill-packaging.md · ADR-009: every command discovers skills under `--root`; build, lock, and publish fan out with `--jobs` and share one site directory per project; `groups` in wedge.toml; check rejects committed `.pyz`; consumers pin wedge in a uv project of their own

- 2026-09-27 · ab3884d42165d951 · merged · decisions/wedge-skill-packaging.md · ADR-010 scopes direct committed bundles as an opt-in exception to legacy release-asset delivery; records source selection and corrupt-bundle checks.

- 2026-09-28 · skills-to-git-gouda · removed · README.md, .github/copilot-instructions.md, scripts/install.sh, skills/github-copilot-personal-instructions/SKILL.md · Moved prek, oss-hygiene, safe-settings, release, justfile, and github-copilot-repo-instructions to paulnsorensen/git-gouda; dropped them from the skill tables, install.sh known tools, and updated the remaining cross-reference to point at git-gouda
- 2026-09-28 · remove-respond · removed · README.md, .github/copilot-instructions.md, .github/workflows/validate.yml, justfile · Removed the respond skill and its post-reply tests; easy-cheese /affinage covers PR review-comment triage
- 2026-09-28 · retire-last-skills · removed · README.md, AGENTS.md, CONTRIBUTING.md, .github/copilot-instructions.md, .github/instructions/skills.instructions.md, .github/scripts/validate_skills.py, .github/workflows/validate.yml, .github/workflows/release.yml, justfile, .gitignore · Removed skills/gh, skills/file-handler, and skills/github-copilot-personal-instructions — the repo publishes no Agent Skills under skills/; deleted scripts/install.sh and its tests (no skills left to install); validate_skills.py now tolerates an absent skills/ and validates .agents/skills/ instead
- 2026-09-28 · merge-main-skillz · merged · README.md, AGENTS.md, CONTRIBUTING.md, .github/copilot-instructions.md, .github/instructions/skills.instructions.md, .github/workflows/release.yml · Kept skills/skillz from main (#102) as the one published skill; restored the README Skills table and the gh skill publish release steps



- 2026-09-28 · f7dd2d362d5fa3c5 · new-page · research/gepa-session-optimization.md · GEPA/session-analytics research ingest; exact identity and natural retrieval probes pass.
- 2026-09-28 · f7dd2d362d5fa3c5 · new-page · ideas/session-driven-skill-optimization.md · GEPA/session-analytics research ingest; exact identity and natural retrieval probes pass.
- 2026-09-28 · f7dd2d362d5fa3c5 · new-page · sources/skillz-session-analytics-pr-105.md · GEPA/session-analytics research ingest; exact identity and natural retrieval probes pass.
- 2026-09-28 · f7dd2d362d5fa3c5 · new-page · sources/gepa-gskill.md · GEPA/session-analytics research ingest; exact identity and natural retrieval probes pass.
- 2026-09-28 · f7dd2d362d5fa3c5 · new-page · sources/gepa-optimize-anything.md · GEPA/session-analytics research ingest; exact identity and natural retrieval probes pass.
- 2026-09-28 · f7dd2d362d5fa3c5 · new-page · sources/itsmostafa-gskill.md · GEPA/session-analytics research ingest; exact identity and natural retrieval probes pass.
- 2026-09-28 · f7dd2d362d5fa3c5 · new-page · sources/gepa-faq.md · GEPA/session-analytics research ingest; exact identity and natural retrieval probes pass.
- 2026-09-28 · f7dd2d362d5fa3c5 · new-page · sources/gepa-adapters.md · GEPA/session-analytics research ingest; exact identity and natural retrieval probes pass.
- 2026-09-28 · f7dd2d362d5fa3c5 · new-page · sources/gepa-releases.md · GEPA/session-analytics research ingest; exact identity and natural retrieval probes pass.
- 2026-09-28 · f7dd2d362d5fa3c5 · conflict-flagged · research/gepa-session-optimization.md · Resolved gskill identity, Pareto terminology, and prototype holdout claims with primary evidence; prior repositories remain unchanged.



- 2026-09-28 · 472a0c67d38bf2c8 · merged · research/gepa-session-optimization.md; ideas/session-driven-skill-optimization.md · Recorded approved bounded implementation, normalized case boundary, immutable evaluation, and private export. Six frozen retrieval probes passed after one lead repair.

- 2026-09-28 · 472a0c67d38bf2c8 · new-page · sources/codex-skill-discovery-isolation.md · Preserved pinned OpenAI skill-discovery evidence that ignore-user-config did not exclude host skill roots.



- 2026-09-28 · skillz-live-20260928 · merged · research/gepa-session-optimization.md; sources/codex-skill-discovery-isolation.md · Recorded authenticated self-test: 19 charged invocations, 16/16 tasks and 6/6 holdout runs passed; both search modes retained the seed. Preserved Codex exec argument and independent-validation corrections. All six frozen retrieval probes passed.

- 2026-09-28 · fec4e1e9156e1f05 · merged · decisions/wedge-skill-packaging.md · Add the /wedge teaching workflow, required build layout, fixed dependency closure, output-limit boundary, and relocated-launcher verification. All three frozen retrieval probes return the page at rank 1.

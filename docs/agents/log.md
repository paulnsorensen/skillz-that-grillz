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
- 2026-10-01 · pr106-cure · updated · decisions/wedge-skill-packaging.md · Rebased the /wedge teaching section on ADR-004, ADR-005, ADR-007, ADR-009, and ADR-010 after the merge with main; dropped the superseded lib/fromargs layout and fixed-closure claims; cited files without line ranges.
- 2026-10-01 · skillz-wedge · updated · decisions/wedge-skill-packaging.md · Recorded the /skillz wedge handoff: skillz writes the behavior contract, /wedge packages it, and skillz never invokes /wedge.
- 2026-10-01 · skillz-wedge-bundle · updated · decisions/wedge-skill-packaging.md · /wedge ships its builder as a direct bundle; guard accepts compressed py2.py3 pure wheels; publish skips direct bundles.


- 2026-10-04 · 5f96f7f0ac775c0b · merged · decisions/skillz-autoimprove.md · Record the deferred Claude live-network verification gap from PR #118. All three frozen retrieval probes return the page at rank 1.
- 2026-10-05 · skillz-wedge-offload · updated · decisions/wedge-skill-packaging.md · #151: /wedge moved into /skillz wedge; one offload procedure for wedge and improve; fromargs by default, PEP 723 `uv run --script` when not wedged; builder at skills/skillz/wedge/; ADR-009 nested-in-skill defaults rule. Reverses the 2026-10-01 skillz-wedge split.


- 2026-10-10 · d18eff71e7d8b81a · new-page · research/behavioral-bdd-gherkin.md · Curated BDD altitude, candidate comparison, and regression evidence; no existing topic owner.
- 2026-10-10 · d18eff71e7d8b81a · new-page · research/skill-behavior-specifications-for-evals.md · Curated upstream intent documents, evaluation methods, and schema differences; existing runner decisions remain unchanged.
- 2026-10-10 · skillz-journey-evals · updated · decisions/skillz-autoimprove.md · ADR-008 to ADR-010: journey case kind, declared task tool surface, and a separate journey budget of 1000 calls and 28800 seconds.



- 2026-10-10 · ed696930d458e9fa · new-page · parallel-quality-gate.md · Record bounded gate lanes, dev-only pytest workers, short isolated temporary roots, and incomplete Mac verification. No measured speedup.



- 2026-10-10 · rebase-43a9dbe-parallel-gate · merged · parallel-quality-gate.md · Replace separate lib environments with one shared packages workspace preparation. Record the user-selected Linux CI verification route.



- 2026-10-10 · ci-38102417849-bounded-wait · merged · parallel-quality-gate.md · Record the Linux descendant-state race and approved bounded test observation. Preserve the runtime timeout and final assertion.
- 2026-10-11 · issue-211-nono-removal · updated · decisions/skillz-autoimprove.md, sources/codex-skill-discovery-isolation.md, ideas/session-driven-skill-optimization.md · Mark ADR-007 superseded after the removal of the nono backend and the `--isolation` option. Note that the Codex adapter requires 0.154.0 or newer.


- 2026-10-15 · 1406d6d87eb17d68 · updated · decisions/skillz-autoimprove.md · ADR-017 records the three autoimprove claims, the replay phase, and the claims.apply rule (spec skillz-autoimprove-claims).
- 2026-10-15 · 1406d6d87eb17d68 · merged · ideas/session-driven-skill-optimization.md · Export section now states the replay on a clean copy and the three-claim report.

- 2026-10-18 · pr-363-journey-kits · updated · decisions/skillz-autoimprove.md · Record the kit location `packages/skillz/journey-kits/` in an ADR-008 note and the ADR-019 context, and why PR #363 renamed it from `experiments/`.


- 2026-10-19 · 82aa7d68c88aff27 · new-page · research/llm-judge-scoring-and-binary-criteria.md · Current judge scoring (0-100, pillar mean, one call per case) and judge-scale, binary-checklist, generated-criteria, and EARS research. Research only; the /mold judge design is open. No page owned the topic; ADR and criteria files left unchanged. All five frozen probes return the page at rank 1.

- 2026-10-22 · c1182c6ea19c3c0d · merged · decisions/wedge-skill-packaging.md · Record consumer merge-group validation and separate publication policies. All three frozen probes return the decision page at rank 1; corpus reconciliation remains pending.

- 2026-10-24 · wedge-site-layer-cache · updated · decisions/wedge-skill-packaging.md · ADR-003 and ADR-011 amendments: `build` reuses a manifest-verified site layer from wedge's own cache, and `build_from_layer` links clean layer entries instead of copying them

- 2026-10-25 · wiki-harvest · updated · criteria/index.md · Add the 41 criteria files that the index list lacked, so the list matches the directory.

- 2026-10-27 · gh-324-live-proof · new-page · skillz-live-proof-lessons.md · Lessons from the #324 live proof: split and plain preflight prompts after a live model refusal, the native Read setting, split floors that move suites and caps together, caps that equal the dry estimate, the scoring-label cohort break, CPU-time test margins, and process gotchas. No page owned these gotchas; ADR-006 and the criteria files hold the design.


- 2026-10-30 · 6070cd54e776b3bf · merged · decisions/skillz-autoimprove.md · Record approved trusted activation and native shell/cwd parsing constraints from PR #579.

- 2026-11-01 · 1b8434010d4146fb · merged · criteria/gh-580.md · Stream approval hashing without repeated fixture allocations. Preserve legacy digests and byte limits.


- 2026-11-01 · bc17b82fd0aaa813 · merged · criteria/gh-580.md · Measure the written seed bytes and keep legacy draft schema compatibility. Exact probe ranks first; natural probe ranks second.


- 2026-11-01 · f12ebe3cf4352fbb · merged · decisions/skillz-pragmatic-autoimprove.md · Record PR #577 resume diagnostic precedence without changing rejection eligibility. Frozen exact and natural retrieval probes pass at ranks 1, 1, and 2.


- 2026-11-01 · 01d8d6d36c719c55 · merged · criteria/gh-580.md · Share matching fixture paths despite inline conflicts. Preserve legacy formats, approval hashes, and byte bounds. Frozen exact and natural probes pass at ranks 1, 1, and 2.

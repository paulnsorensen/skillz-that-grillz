# wedge skill packaging decisions

These records explain the design of `wedge`, a packaging tool at `packages/wedge/src/wedge/`, inside the `skillz-that-grillz` package at `packages/wedge/`. `wedge` packages a `fromargs` CLI as a content-addressed ZIP executable. Archives run directly from the ZIP without extraction or runtime cache writes (ADR-013). It supports legacy release assets and opt-in direct archives (ADR-010). Other repositories use it through the `actions/wedge` GitHub Action (ADR-007). The design brief was a session spec, not a durable spec; these records are the durable source of the design.

## Records

### ADR-001: Key the build on content, not the git commit  [status: accepted]

- **Context:** Two merges can land on `main` seconds apart and each trigger a post-merge publish job. A key derived from `HEAD`'s commit sha would make each run publish a new asset for the same bytes, even when neither commit touched the skill's inputs. A build must stay safe to run more than once, from more than one commit, without changing or deleting an asset another run already published.
- **Decision:** The key is a sha256 hash over a canonical JSON document: a `FORMAT_VERSION` constant, the target Python version, the sha256 of the skill's `wedge.toml`, the sha256 of the project's `uv.lock`, and a sorted list of (`project-relative posix path`, `sha256(file bytes)`) pairs for every file in `source` and each `include` entry.[^1] `__pycache__` and `*.pyc` are excluded. Paths are relative to the configured project directory, not the checkout, so the key is portable (ADR-007).
- **Alternatives:** Key on the git commit sha of `HEAD`. Rejected: it changes on every commit, even a commit that does not touch the skill, and it gives two racing runs no way to converge on one asset.
- **Consequences:** Two runs that race after a merge each compute the same key from the same bytes and publish the same asset name; the later run finds the asset already there and skips its upload.[^2] Git's linear history still decides which key the committed lock on `main` points at. No mutable "latest" pointer exists. Every asset is immutable and append-only; nothing ever deletes or overwrites a published asset.

### ADR-002: Release assets, never a committed .pyz  [status: accepted]

- **Context:** A `.pyz` bundles third-party wheels and can reach megabytes. Committing it would bloat every clone and every diff, and two builds of the same commit could still disagree byte-for-byte without a reproducibility guarantee.
- **Decision:** The `.pyz` is a GitHub release asset only. The skill directory commits only a small launcher script and a lock file (`name`, `key`, `content_sha256`, `release`, `asset`, `repo`, `format`).[^3] `wedge publish` builds the asset once and uploads it to one rolling prerelease tagged `wedge`, with `--latest=false` so the repository's "Latest release" badge stays on real version tags. Asset names embed the content digest (`<name>-<content12>.pyz`, ADR-008), so publishing is append-only.
- **Alternatives:** Commit the `.pyz` directly. Rejected: it bloats the git history with binary blobs that never compress well and turns every skill change into a large diff.
- **Consequences:** A skill needs network access on first run to fetch its `.pyz`; the launcher caches it after that. The repository stays small. CI must run `wedge publish` after every merge that changes a wedged skill's inputs, or the committed lock can reference an asset that does not exist yet.
- **Scoped supersession:** ADR-010 permits an opt-in committed archive for direct execution. This ADR still governs the lock, launcher, and release-asset workflow.

### ADR-003: Reproducible builds, verified by hash  [status: accepted]

- **Context:** The content key promises that any machine building the same inputs gets the same asset. That promise only holds if the build itself is deterministic: file timestamps, wheel resolution order, and installed metadata can otherwise vary between machines and even between runs on the same machine.
- **Decision:** `wedge build` fixes `SOURCE_DATE_EPOCH`, uses shiv's `--reproducible` and `--uncompressed` flags (the builder deflates afterwards, ADR-008), writes a fixed `/usr/bin/env python3` shebang, and evaluates markers against a fixed target Python (the `requires-python` floor, 3.11). It strips volatile install files (`RECORD`, `INSTALLER`, `REQUESTED`, `direct_url.json`, `__pycache__`) and installed `bin/` scripts before zipping. `uv pip install --target` writes console scripts with the builder's absolute Python path. Shiv's reproducible mode does not normalize those bytes. Removing host-bound scripts and avoiding compressor-dependent ZIP bytes changes the asset format. Shiv leaves its bundled bootstrap entries in filesystem enumeration order, so `wedge build` sorts all ZIP entries after shiv runs. The builder normalizes ZIP timestamps, creator system, and file/directory modes, so umask 002 and 022 produce identical bytes. Hosted Linux CI and local macOS then produced the same archive contents. `FORMAT_VERSION = 8` prevents an earlier key from naming different bytes.[^4] Third-party dependencies resolve from the configured project's `uv.lock` with hashes and install with `--no-cache --require-hashes --no-deps --target`, so the exact wheel bytes are pinned. `--no-cache` makes uv download and hash-check every wheel on every build: uv checks a hash only at download time and hardlinks cached unpacked wheels into each install, so an edit to any hardlinked copy would otherwise reach every later build on that host. Source and include trees copy into the site directory directly, not through a wheel build.
- **Alternatives:** Accept whatever the local `uv`/`pip` resolver picks at build time. Rejected: it does not guarantee the same wheel on every machine, which breaks the content key's core promise.
- **Consequences:** To reproduce a published asset locally: check out the commit the lock's key was computed against, run `wedge build <skill-dir> --out <dir>`, and compare `.<name>.content_sha256` from the printed manifest to the lock's `content_sha256` field. Any mismatch means the local toolchain, `uv.lock`, or inputs drifted from what produced the asset. Build trees reject symlinks before hashing or copying, and lock validation rejects malformed names, keys, digests, assets, formats, releases, and repositories before publication.
- **Amendment 2026-10-24 (wedge-site-layer-cache):** `build` and `build_many` no longer install the closure on every build. They reuse a site layer from wedge's own cache at `${WEDGE_CACHE:-${XDG_CACHE_HOME:-~/.cache}/wedge}/site-layers/<key>`. The key is a sha256 over `FORMAT_VERSION`, the target Python, the host platform, the `uv --version` text, and every non-comment line of the `uv export` output, with its `--hash` lines. A new entry still comes from a `--no-cache --require-hashes` install. The entry holds `site/` and `manifest.json`, which records the sha256 of every file and the name of every directory. Each reuse hashes the whole layer again. An edited, added, or removed file, a symlink, a special file, or a changed manifest moves the entry aside and installs a fresh one. A layer can still change after that check, so the build also compares the archive members below the layer's top-level names with the manifest; any difference fails the build, and no archive lands. An entry appears by one atomic rename, so concurrent builders share it, and a new entry deletes entries unused for 30 days. No other tool links into this cache, so the hardlink drift of the maintenance note below cannot reach it unnoticed. Limit: a writer that changes a layer file and its manifest together is not detected; such a writer can also replace `uv` itself. `populate_site_layer` for skillz runs does not use the cache. Reason: a `wedge build` reinstalled and deleted thousands of files, and the skillz e2e suite saturated a host disk with that metadata churn.[^18]

- **Amendment 2026-11-01 (gh-581):** Format 9 replaces Shiv with a standard-library ZIP writer. The writer sorts members and fixes timestamps, creator system, modes, and shebang. It deflates files and stores directory entries. Exact interpreter patches compile unchecked-hash legacy bytecode with normalized `site-packages/...` filenames. The pin table and configured bytecode subset enter the content key. Format 10 then separates re-execution changes from intermediate format-9 artifacts. Format-8 and format-9 locks become stale. The normalized-content digest remains unchanged.[^zip-build]

### ADR-004: The launcher trusts only the sha256 in its committed lock  [status: accepted]

- **Context:** The launcher downloads a binary over the network before it runs it. A compromised release, a stale cache entry, or a truncated download must never lead to code execution the lock did not authorize.
- **Decision:** The committed launcher is a stdlib-only Python script that reads the `.wedge.json` lock beside it, resolves a cache path under `${WEDGE_CACHE:-${XDG_CACHE_HOME:-~/.cache}/wedge}/<asset>`, and downloads the asset only when the cached file is missing or its content digest does not match the lock. It writes the download to a temp file in the cache directory, hashes it, and only then renames it into place atomically. Any hash mismatch, including a `WEDGE_PYZ` override pointing at a local file, refuses to run: it prints one JSON line on stderr and exits 3, and never calls `execv`. `WEDGE_BASE_URL` must be `https://…` unless the host is `127.0.0.1` or `localhost`, which keeps a plain-`http` override usable in tests without opening the default path to a downgrade.
- **Alternatives:** Trust the downloaded bytes once the HTTPS connection succeeds. Rejected: TLS authenticates the server, not the asset; a compromised or mismatched release asset would still run.
- **Consequences:** The lock's content digest is the entire trust boundary. Regenerating the lock after any source change is required, and `wedge check` fails the build when the lock's key goes stale or the launcher text drifts from the template, so a stale lock cannot silently ship. Publication rejects duplicate configured skill names and CLI/config/lock repository disagreement before side effects. The post-merge workflow has no shared concurrency group because cancelling pending runs can lose append-only assets.

### ADR-005: Known limits  [status: accepted]

- **Context:** The design above trades some capability for simplicity and safety in the first version.
- **Decision:** Ship without these, and revisit only if a real need appears.
- **Consequences:**
  - No asset garbage collection. The rolling `wedge` release keeps every published asset forever; nothing deletes an old key's `.pyz`.
  - Archive execution requires Python 3.11 or newer. On POSIX, an older interpreter searches pinned `python3.N` names on `PATH`, newest first. It probes each candidate's actual minor before re-execution. Broken executables and misnamed wrappers fall through to the next candidate. It re-execs with unchanged archive arguments. It preserves active `-E`, `-s`, `-S`, and `-I` restrictions, including `-I` with `-S`. No match produces exit 3 and a JSON version error on stderr, with empty stdout. Windows reports that error without searching. The build requires Python 3.11 or newer.[^zip-runtime]
  - Only a pure-Python dependency closure is supported. `wedge build` accepts a resolved wheel only when its PEP 425 Python tag set includes `py3`, its ABI tag is `none`, and its platform tag is `any`. It rejects any other wheel, and any resolved dependency that carries a platform marker (`sys_platform`, `platform_system`, …), with a clear error naming the offending package. A compressed universal tag such as `py2.py3-none-any` also passes.

- **Amendment 2026-11-01 (gh-581):** Cold bytecode builds can download the exact managed interpreter patches. Warm builds resolve installed exact pins without writing into the interpreter installation root. Runtime execution needs no interpreter download.[^zip-build]

### ADR-006: Keep Wedge and fromargs as separate distributions  [status: accepted]

- **Context:** PR #88 publishes `fromargs` from its own project. Wedge packages only its own distribution and uses configured project dependencies.
- **Decision:** Keep `fromargs` independent. A skill vendors local source through `include` and exports only the configured project's dependency closure, avoiding shiv's own transitive dependencies (`pip`, `setuptools`, `click`) in each `.pyz`.
- **Consequences:** The fromargs release lane remains independent. Only the post-merge Wedge workflow publishes assets; a tag points at a commit on `main` that workflow already published.

### ADR-007: Portable projects and a public composite action  [status: accepted]

- **Context:** Other repositories want to ship wedged skills. The first version assumed this repository's `packages/fromargs` layout, so it could not run in a consumer repository.
- **Decision:** `wedge.toml` names its layout. `project` (default `.`) contains `pyproject.toml` and `uv.lock`; `include` lists local package trees to vendor; `repo` is required and must be `owner/name`. `source` and each `include` entry must resolve inside `project`, reject symlinks, and have unique top-level names. Configured local inputs cannot overwrite installed dependencies. `uv export` omits local path packages, so consumers vendor them through `include`; unsupported URL or path requirements fail with guidance. Publication validates lock field types, names, digests, format, repository agreement, and duplicate skill names before side effects. `FORMAT_VERSION = 8` marks this integrated key layout and normalized-content digest.[^8] The public `actions/wedge/action.yml` composite action supports `command: check|publish`, runs Wedge with `uv run --locked` against its own `lib/` project, verifies the pinned gh digest, installs gh under runner temp without sudo, and exports its path.
- **Alternatives:** A separate Wedge repository with a root `action.yml`, or publishing Wedge and fromargs to PyPI. Deferred: the action needs no index because it runs from its own checkout.
- **Consequences:** Consumers reference `paulnsorensen/skillz-that-grillz/actions/wedge@<sha>`. Validation covers a standalone consumer project with its own `uv.lock`. The former exact Shiv pin becomes unnecessary under ADR-013; Wedge no longer depends on Shiv. The Action preserves a failed Wedge result and exits nonzero so callers can diagnose publication failures.

### ADR-008: Pin the uncompressed contents, ship compressed assets  [status: accepted]

- **Context:** ADR-003 disabled ZIP compression because macOS and Linux zlib builds emitted different deflate bytes for identical contents, and the lock pinned the file's sha256. Uncompressed assets were about 3.7 times larger (8.5 MB against 2.3 MB for `cheese-cave`).
- **Decision:** The lock pins `content_sha256`: a sha256 over a domain tag and each member's length-prefixed name, size, and uncompressed bytes, in archive order.[^9] The builder sorts and normalizes members, computes that digest, then deflates them. The asset name embeds the first 12 hex characters of the digest. The launcher recomputes the digest before `execv` and refuses any archive that fails to parse or expands past 512 MiB. `wedge publish` builds on every run and treats an existing asset as published only when both its own build and the downloaded asset match the lock's content digest, so an asset uploaded from a developer machine is verified by the post-merge runner. The launcher template carries a copy of the digest function, and a test keeps the two equal.
- **Alternatives:** Record the runner's file sha256 in the lock through a bot pull request. Rejected: `main-protection` requires a PR with status checks, so it needs an App or a personal token and leaves locks pending until the bot PR merges. Hash the bytes of an uncompressed ZIP. Rejected: the launcher would need to re-serialize the archive, and `zipfile` output can change between Python versions.
- **Consequences:** A local `wedge lock` pins a digest that every host reproduces, so publication needs only `GITHUB_TOKEN` with `contents: write`. Bytes outside the members (the shebang line, the archive comment, trailing data) are not covered; Python does not execute them when the launcher runs the archive. Each launch decompresses and hashes the cached archive once.

- **Maintenance note:** On 2026-09-26 a lock built from a shared uv cache differed from CI's build in one member, `markdown_it/port.yaml`. The cached copy carried 366 hardlinks and a reflowed YAML block; a tool had edited a hardlinked install in place. `--require-hashes` did not catch it and `UV_REFRESH` did not clear it. `--no-cache` in the builder removes this class of drift; the tool that edited the file is not identified.

- **Amendment 2026-11-01 (gh-581):** The canonical writer deflates every file member directly and stores directory entries. Runtime import uses stock `zipimport`. Neither the digest algorithm nor the verified-download cache contract changes.[^zip-build]

### ADR-009: Wedge fans out across skills; consumers keep no build loop  [status: accepted]

- **Context:** `build` and `lock` took one skill directory while `check` and `publish` discovered skills under `--root`. A repository with 13 skills over one package wrote its own thread pool and `xargs -P` loop around `wedge`, renamed the outputs, and paid 13 `uv export` and 13 `--no-cache` wheel downloads per run. It also pinned the wedge commit in a wrapper script and in the workflow, with a test to keep the two equal.
- **Decision:** Every command resolves skills the same way: positional directories, else `*/wedge.toml` under each `--root`, and an empty discovery is an error. Positional directories and `--root` are exclusive, and every `--root` must be a directory. Resolved duplicate directories collapse to one, in first-seen order. `build`, `lock`, and `publish` run skills through one `fan_out` helper (`--jobs` threads, one outcome per skill, one skill's failure never stops the others). `fan_out` catches every exception per item on purpose, so one skill's crash never stops the rest. `build_many` groups skills by resolved project, source, includes, and groups. It populates one site directory per group and packages each skill from it; `lock` and `publish` build through it. `build` and `lock` print `{name: {...}}`. `wedge.toml` gains `groups`, a list of uv dependency groups exported into the closure. A project whose CLI dependencies must stay out of its published library uses this. `groups` may not name `dev`: uv drops a `--group dev` export, so the CLI ships without its dependencies. An unknown key in `wedge.toml` fails, naming the file that set it. Legacy `wedge check` rejects a `.pyz` committed beside a launcher (ADR-002) and reports duplicate skill names. Direct bundles use `wedge bundle --check` (ADR-010). A `wedge.toml` in a discovery root supplies defaults for every skill beside it (any key but `name` and `entry`; the skill's file wins). It joins the key only when present, so existing keys stand. A parent directory that holds a `SKILL.md` is a skill, not a discovery root, so a CLI directory nested in a skill reads no defaults (2026-10-05, #151). With `--branch`, `publish` refuses unless `--target` resolves to the checked-out HEAD of every skill directory. It then compares that commit against the branch through the compare API. A shallow checkout still passes, and a branch name alone no longer suffices. A uv-managed consumer pins wedge once, as the sole dependency of a small uv project of its own (`tools/wedge/`). It runs `uv run --locked --project tools/wedge wedge …` locally and in CI; the composite action remains for repositories without uv. The pin cannot live in the skill project's own lock: uv carries the `fromargs` path source from `packages/wedge/pyproject.toml` into any lock that depends on wedge. That replaces the `fromargs` wheel in the `.pyz` closure with a git checkout. The post-merge workflow skips `publish` when no `skills/**/*.wedge.json` lock exists, so a repository with no legacy wedged skills stays green. It does not key on `wedge.toml`: a direct-bundle skill has one, and `publish` rejects its `[[target]]` tables once `main` no longer tracks the archive (ADR-012).
- **Alternatives:** Per-skill files only. Rejected: 13 skills repeated the same five lines, and a skill directory is copied for use, not for building. A wedge dependency group in the consumer's own project. Rejected: uv carries wedge's `fromargs` path source into that lock. Skipping the publish build when the asset already matches the lock. Rejected: the rebuild is what proves a developer-machine lock reproducible (ADR-008). Verifying only the compressed bytes at launch. Rejected: the launcher's contract is the content digest (ADR-004).
- **Consequences:** A consumer's glue shrinks to one pinned tool project and one command per recipe; the manifest gives tests each archive's path. Site sharing makes a stale skill build before its key mismatch is reported, since keys come from the same pass that resolves the site. The action's `uses:` pin and the consumer's `uv.lock` pin remain two pins only for consumers that use both.

### ADR-010: Direct archives are an opt-in delivery mode  [status: accepted]

- **Context:** Some consumers copy a self-contained skill archive and cannot depend on a first-run download. They accept a committed binary in exchange for direct execution.
- **Decision:** `wedge bundle` writes an executable `scripts/<name>.pyz` without a lock or launcher. It builds each skill, writes through a temporary file, and atomically replaces the target. `wedge bundle --check` rebuilds and compares the uncompressed-content digest, the canonical shebang, and executable permission without changing the committed archive.[^10] A corrupt DEFLATE stream is an invalid-bundle outcome for its skill; checking continues for later skills. Optional `source_paths` selects paths below `source`, preserves their archive paths, and joins the selection to the content key. An absent selection retains the full source tree.[^11]
- **Alternatives:** Keep the release-asset launcher as the only delivery mode. Rejected for consumers that need a copied skill to run without a network fetch.
- **Consequences:** Direct consumers commit the larger `.pyz` and run `wedge bundle --check` in CI. The legacy lock, launcher, and release-asset workflow remain available. The `wedge check` restriction on a `.pyz` beside a launcher still applies only to that legacy mode. `wedge publish` reports a skill with `scripts/<name>.pyz` and no lock as `skipped`, because no release asset exists for it; a missing lock without a bundle still fails. The repository's own `skillz` skill ships four direct bundles: `scripts/skillz-experiment.pyz`, `scripts/inspect-skill.pyz`, `scripts/agent-session-analytics.pyz`, and the builder at `scripts/wedge.pyz`. Since ADR-012, `main` does not commit them: the gate builds them, and `dist` ships them.

_Source: PR #103, `packages/wedge/src/wedge/`, `packages/wedge/tests/wedge/test_cli.py`, and `packages/wedge/README.md` · Updated: 2026-09-27 · Supersedes: ADR-002's blanket ban on committed `.pyz` files for opt-in direct bundles_

[^10]: `packages/wedge/src/wedge/_bundle.py`; `packages/wedge/tests/wedge/test_cli.py`
[^11]: `packages/wedge/src/wedge/_config.py`; `packages/wedge/src/wedge/_key.py`; `packages/wedge/src/wedge/_build.py`; `packages/wedge/tests/wedge/test_key.py`

### ADR-011: [[target]] tables and a reusable site layer  [status: accepted]

- **Context:** One skill directory sometimes holds several CLIs, and each CLI needs its own archive. Separate builds install the same third-party closure again.
- **Decision:** `wedge.toml` accepts `[[target]]` tables with the keys `name`, `entry`, `source`, `source_paths`, `include`, and `groups`. Top-level keys other than `name` and `entry` are defaults for every table. A top-level `name` or `entry` beside a table is an error, and so is a duplicate target name. An error names its table by index and name. The single-target form keeps its key and its archive bytes. The cache key adds the target name only for `[[target]]` tables, so `FORMAT_VERSION` stays 8. `build` and `bundle` accept the tables. `bundle` writes `scripts/<name>.pyz` per target, and `bundle --check` checks each one. `lock`, `check`, and release-mode `publish` reject the tables. `publish` skips each target that is a direct bundle, meaning a `scripts/<name>.pyz` and no lock. A site layer is a third-party install directory. Its key covers the wedge format version, the `uv.lock` digest, the sorted groups, and the target Python. `populate_site_layer` fills the layer once, may use the network, and writes the key to a file beside the layer. It refuses a destination whose key file already exists. A caller removes both the layer and its key file. `open_site_layer` reopens a layer and refuses changed inputs. `resolve_target` resolves a target once against the checkout. `stage_sources` copies the first-party files, and an optional overlay replaces or adds files by project-relative path. An added file must lie under an include root or under the source, and inside the `source_paths` selectors when they exist. Any other overlay file is an error. `build_from_layer` checks the key, lays out the layer and the staged files in a temporary site directory, and runs shiv with no network. Every build path uses these steps. `build_many` shares one layer per project and group set.[^16]
- **Alternatives:** Keep one site directory per target set in `build_many`. Rejected because it leaves two build paths. Reject several targets per skill and use one directory per CLI. Rejected because it repeats the install.
- **Consequences:** The bytes of a single-target build do not change. The build rejects a first-party top-level `bin`, a symlink, and a name that shadows an installed package. It writes each archive through a temporary file and an atomic replace. A later change defines the lock shape for several targets.
- **Amendment 2026-10-22 (gh-391, gh-393):** `wedge/__init__.py` is the public API of the package. Besides `main`, it exports the names that `skillz_experiments` imports: `TARGET_KEYS`, `BuildPaths`, `ConfigError`, `SiteLayer`, `WedgeConfig`, `build_from_layer`, `bundle_path`, `defaults_path`, `first_party_files`, `layer_import_names`, `load_targets`, `open_site_layer`, `parse_targets`, `populate_site_layer`, `resolve_target`, and `stage_sources`. The `_build`, `_bundle`, `_config`, and `_key` modules stay private. Another workspace package imports these names with `from wedge import ...` and never reads a `wedge._*` module. `check_architecture.py` enforces this, and its `ALLOWED` set is empty. A new export needs a caller outside `wedge` and an amendment here. No new cross-package edge appears: `skillz` already depends on `wedge`.
- **Amendment 2026-10-24 (wedge-site-layer-cache):** `build_from_layer` no longer copies the layer. Each top-level layer entry that holds only regular files and directories, and nothing that the strip removes, becomes a symlink in the temporary site directory. shiv follows these links, so the archive gets the same paths and bytes. Any other entry is copied and stripped as before, so a symlink or special file in the layer still fails the build. The build never writes through a link. In the rebuilt `skillz` bundles, every third-party member kept its bytes; only `wedge/_build.py`, the new `wedge/_layer_cache.py`, and `environment.json` differ.

- **Amendment 2026-11-01 (gh-581):** `bytecode` is also a target key and a shared default. `build_from_layer` copies its site view into a private snapshot. It checks that snapshot against the cached-layer manifest before analysis and compilation. Compilation and archive writing use the same verified snapshot. The final archive-member check remains. The source layer stays unchanged. Cold compiler resolution can use the network; the earlier no-network promise no longer applies. The public facade remains the only cross-package import seam.[^zip-build]

_Source: `packages/wedge/src/wedge/` and its tests · Updated: 2026-11-01 · Supersedes: Shiv writer and network-free compilation claims · 2026-10-24_

### ADR-012: This repository publishes its direct archives on a `dist` branch  [status: accepted]

- **Context:** Mergify's merge queue tests each pull request on top of the pull requests ahead of it. When two queued pull requests change source that a `skills/skillz` archive includes, the later pull request carries an archive built against `main` only. `wedge bundle --check` then fails, and the queue removes the pull request. No queue can add a rebuild commit. Git cannot merge the binary archives either (#344).
- **Decision:** `main` holds source only. The `publish dist` workflow runs on each push to `main`, on each manual run, and every hour. It checks out the `main` tip, runs `wedge bundle skills/skillz`, and runs each archive with `--help`. `.github/scripts/publish_dist.py` then appends one commit to `dist`: the full `main` tree plus the built archives, with a `Source-Commit: <sha>` trailer.[^18] The publisher fast-forwards `dist` without force. It pushes with the job's `GITHUB_TOKEN`. The easy-cheese `dist` publisher does the same, and its runs push `dist` commits that change `.github/workflows/` files. The publisher refuses a source that is not on `main` or that does not descend from the last published source. An older source is a no-op. One `publish-dist` concurrency group runs one publish at a time, and the remaining run publishes the newest tip, so a replaced pending run loses no source change (Consumer CI guidance). `dist` is the default branch, so `npx skills add` and a bare clone get built archives. A `pull_request_target` workflow moves each pull request that targets `dist` to `main` and never checks out its code.
- **Alternatives:** A rebuild bot that pushes to each open pull request. Rejected: it needs a GitHub App token, and GitHub's server-side update still stops on the binary conflict. A post-merge rebuild commit on `main`. Rejected: it needs a ruleset bypass actor, and `main` is stale between the merge and the rebuild. The legacy lock and launcher. Rejected: a lock also changes in every pull request that touches shared source, and a first run needs the network. The model follows the easy-cheese `dist` design.
- **Consequences:** `dist` holds the full tree, because GitHub runs scheduled workflows, Dependabot, and Scorecard against the default branch. Each Dependabot version-update block sets `target-branch: main`, and Scorecard has no `push: main` trigger. Dependabot security updates still target the default branch; recreate those changes from `main` because `dist` has unrelated history.[^dependabot-target] CodeRabbit explicitly adds `^main$` through `reviews.auto_review.base_branches`. `AGENTS.md` records the bot and job branch policy. The `main-protection` and `copilot-auto-review` rulesets name `refs/heads/main`, not `~DEFAULT_BRANCH`. A `dist` ruleset blocks deletion and non-fast-forward pushes, with no pull-request rule, so `GITHUB_TOKEN` can append. `dist` grows by one archive set (about 15 MB) for each publish that changes an archive. A release tag on a `main` commit has no archives, so `gh skill install` at that tag gets none. Git ignores `skills/*/scripts/*.pyz`, and the gate builds the archives before any test reads them. `Candidate.capture` keeps a git-ignored `scripts/<name>.pyz` of a target that `wedge.toml` declares, so an experiment on a checkout still runs the archives.

_Source: `.github/workflows/publish-dist.yml`, `.github/scripts/publish_dist.py`, and `.github/workflows/retarget-dist-prs.yml` · Updated: 2026-10-24_

[^18]: `.github/scripts/publish_dist.py`; `.github/scripts/test_publish_dist.py`

[^dependabot-target]: [GitHub Dependabot options reference](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference#target-branch).

### ADR-013: Execute archives directly from the ZIP  [status: accepted]

- **Context:** Shiv extracts dependencies before execution. A read-only sandbox can reject that write even when it permits the archive command.
- **Decision:** Every Wedge archive runs in place. A standard-library launcher selects the matching cache-tag tree before the complete source closure. A finder before the stock `PathFinder` maps missing package submodules to source. It defers to concrete bytecode loaders and prevents data-only namespaces from masking regular source packages. Stock loaders continue to own imports. The launcher fixes the absolute archive path before application code runs. A `linecache` hook reads normalized source filenames with Python encoding declarations and reuses complete cached entries. The first ZIP open reads both environment metadata and matching-tree presence. Bundled distribution metadata precedes host metadata when archive paths come first. Explicit metadata paths and host-only distributions remain available. Package data needs no extraction. Bundled `.dist-info` and `.egg-info` metadata both support distribution lookup. Sorted top-level `.pth` files initialize existing bundled paths and trusted import lines before the entry point runs.[^zip-runtime]
- **Consequences:** Archive execution creates no cache or extraction directory. Command-requested output remains permitted. The legacy download launcher still caches verified release assets before execution. First-party resource reads compute the same engine digests and scaffold bytes as checkout execution. Harness probes target the real archive file, not a virtual member path.[^zip-safety]



- **Python 3.11 and 3.12 tracebacks:** Native uncaught exception display bypasses the Python `linecache` hook. Wedge installs lazy Python traceback formatting only when the default exception hook remains installed. Caller exception hooks remain unchanged.[^zip-traceback312]

[^zip-traceback312]: `packages/wedge/src/wedge/_main.py:62-90`; `packages/wedge/tests/wedge/test_bytecode.py:159-187`.

### ADR-014: Compile a static import set with exact Python patches  [status: accepted]

- **Context:** Source imports repeat compilation on each ZIP launch. Full third-party mirrors in five bytecode trees waste archive space.
- **Decision:** The default minor set is 3.11 through 3.15. `BYTECODE_PYTHONS` pins patches 3.11.18, 3.12.16, 3.13.18, 3.14.10, and 3.15.2. Each compiler writes legacy `.pyc` files with `compileall -b --invalidation-mode unchecked-hash` and normalized source paths. `modulefinder` starts at the entry point. Reached literal `importlib.import_module("...")` calls extend that closure to a fixed point. Literal `name` and `package` keywords also participate. Selected third-party packages retain data but no source in their bytecode trees. Every tree retains complete first-party source and bytecode. The separate source tree retains the complete dependency closure.[^zip-build]
- **Configuration:** `bytecode = ["3.11", "3.15"]` selects those trees. `bytecode = []` produces source only. An unpinned minor produces a configuration error. A missing runtime cache tag uses source. Lazy command imports stay lazy: provenance checks cover third-party modules that `--help` actually loads, while static-set checks cover the complete selected bytecode set.[^zip-tests]
- **Consequences:** Interpreter patches and the subset enter the key. A cold build can download compilers; a warm build can use a read-only installation root. Five trees make `skillz-experiment.pyz` exceed the former 16 MiB capture limit. The user approves 24 MiB per file, with the 64 MiB total unchanged. This limit change does not permit a full third-party mirror.[^zip-guard]
- **Selection isolation:** Wedge runs `ModuleFinder` and literal import traversal under managed CPython 3.11.18 with `-I -S`. Ambient builder packages cannot extend the selected closure. A nonempty subset uses this analyzer; an empty subset skips selection. Relative literal imports resolve their declared package before traversal. Private copied directories become owner-writable before compilation. Source and cached-layer directory modes remain unchanged.[^zip-selector]
- **Compiler catalog:** Archive producers pin uv 0.13.6, whose catalog resolves all five compiler patches. Cold-build tests use isolated installation roots and `UV_PYTHON_INSTALL_BIN=0`.[^zip-producers]

_Source: gh-581 implementation, stack 620 review regressions, real ZIP acceptance, and approved user decisions · Updated: 2026-11-01 · Supersedes: extraction runtime, ambient builder discovery, Shiv compiler path, and intermediate format-9 launcher keys_



- **Offline compiler lookup:** Without `uv` on PATH, Wedge first honors explicit `UV_PYTHON_INSTALL_DIR`. Otherwise it infers the managed installation root from the host interpreter. An invalid explicit root fails without searching another root. Exact patch validation still applies.[^zip-offline312]

[^zip-offline312]: `packages/wedge/src/wedge/_bytecode.py:91-123`; `packages/wedge/tests/wedge/test_bytecode.py`; PR #617 Python 3.12.16 full build, session 97767.

## Consumer CI guidance

The action guide is the canonical consumer guide for merge-group validation and post-merge publication.[^17]
The package guide lists the check command for each packaging mode and links to the action guide.
Append-only release publication must preserve each commit's run, as ADR-004 requires.
A complete distribution snapshot can replace pending work only when later runs cover missed source changes.
This guidance changes no runtime behavior, workflow, or repository guard.

_Source: consumer documentation · Updated: 2026-10-22_

[^17]: `actions/wedge/README.md` § Workflows; `packages/wedge/README.md` § Merge queues and publication races.

## Teaching the packaging workflow

`/skillz` teaches the offload and packaging workflow without expanding the runtime contract.[^12]
Its `wedge` mode and its `improve` mode share one offload procedure.
The procedure moves repeatable computation into a CLI and leaves interpretation in skill instructions.
Its bundled references and templates remain available after skill installation.

The skill states no runtime facts of its own.
The records above own them:

- Project layout, `include`, and the action: ADR-007 and ADR-009.
- Pure-Python wheels and Python 3.11 or later: ADR-005.
- Lock and launcher mode, and the opt-in direct mode: ADR-004 and ADR-010.
- Output limits and the reserved `--full` flag: the fromargs record, `fromargs-cli-library.md`.

The CLI uses fromargs unless the user declines it.
A declined fromargs CLI is a stdlib `argparse` script run as `python3 -I`, or a shell pipeline.
The procedure wedges the CLI when the target has a uv project, a committed `uv.lock`, and a pure-Python closure, and the user does not decline.
Otherwise it ships the CLI unpackaged and states the reason.
An unpackaged fromargs CLI carries PEP 723 metadata and runs through `uv run --script`, which resolves fromargs from PyPI.
The run never silently copies libraries or changes shared dependencies.
It verifies source behavior and a relocated launcher with a hash-checked local archive.
That local verification does not prove remote asset availability.[^13]

The skill ships `skills/skillz/scripts/wedge.pyz` as its direct builder bundle. It vendors `packages/wedge/src/wedge` and `packages/fromargs/src/fromargs` without a `wedge` dependency group.[^15]
The builder is one of four `[[target]]` in `skills/skillz/wedge.toml`, beside `skillz-experiment` and `inspect-skill`. It earlier sat in a nested `wedge/` directory, because wedge reads one `wedge.toml` per directory and predated `[[target]]` tables.
A user who installs only the skill can then build without a wedge checkout or tool project; `uv` stays a host requirement because it is a native binary.
Inside an archive, the builder writes a canonical ZIP and invokes pinned host compilers. Warm compiler lookup works with a read-only installation root.
CI consumers still pin wedge in a tool project (ADR-009).

On 2026-10-10, `wedge.toml` gained `[[target]]` tables and a reusable site layer (ADR-011).

On 2026-10-05 (#151), the separate `/wedge` skill moved into `/skillz wedge`.
This reverses the earlier split, in which `/skillz wedge` wrote only a brief and `/wedge` implemented it.
The goal is maximum CLI offload, so the offload decision and the packaging decision are one procedure.
`/skillz` is user-only (`disable-model-invocation: true`), unlike the old `/wedge` skill.
A prompt such as "wedge this" no longer invokes it on its own; the user types `/skillz wedge`. The invocation policy is unchanged.
`improve` now proposes each offload in its approval question instead of recording it as a residual.
The autoimprove wedge arm (`skillz-autoimprove.md`, ADR-005) is unchanged; its alignment is deferred.

_Source: the published teaching skill and the records above · Updated: 2026-11-01 · Supersedes: bundled Shiv builder and obsolete Wedge dependency-group instructions_

[^12]: `skills/skillz/SKILL.md`; `skills/skillz/references/offload.md`
[^13]: `skills/skillz/references/wedge-packaging.md`; `packages/wedge/tests/wedge/test_skill_template.py`
[^15]: `skills/skillz/wedge.toml`; `pyproject.toml`; `packages/wedge/src/wedge/_build.py`; `packages/wedge/tests/wedge/test_bundled_cli.py`
[^16]: `packages/wedge/src/wedge/_config.py`; `packages/wedge/src/wedge/_key.py`; `packages/wedge/src/wedge/_build.py`; `packages/wedge/src/wedge/_bundle.py`; `packages/wedge/src/wedge/_lock.py`; `packages/wedge/src/wedge/_publish.py`; `packages/wedge/tests/wedge/test_targets.py`; `packages/wedge/tests/wedge/test_press_targets.py`
[^18]: `packages/wedge/src/wedge/_layer_cache.py`; `packages/wedge/src/wedge/_build.py`; `packages/wedge/tests/wedge/test_layer_cache.py`; `docs/agents/criteria/wedge-site-layer-cache.md`

[^1]: `packages/wedge/src/wedge/_key.py`
[^2]: `packages/wedge/src/wedge/_publish.py`
[^3]: `packages/wedge/src/wedge/_cli.py`
[^4]: `packages/wedge/src/wedge/_build.py`; `packages/wedge/src/wedge/_key.py`; `packages/wedge/tests/wedge/test_build.py`
[^5]: `.github/workflows/publish-fromargs.yml`; `packages/fromargs/pyproject.toml`
[^6]: `packages/wedge/pyproject.toml`; `packages/wedge/src/wedge/_build.py`; `packages/wedge/src/wedge/_resolve.py`
[^7]: `justfile`; `.github/workflows/wedge.yml`
[^8]: `packages/wedge/src/wedge/_config.py`; `packages/wedge/src/wedge/_key.py`; `packages/wedge/src/wedge/_resolve.py`; `packages/wedge/src/wedge/_publish.py`; `actions/wedge/action.yml`; `packages/wedge/tests/wedge/test_consumer.py`
[^9]: `packages/wedge/src/wedge/_digest.py`; `packages/wedge/src/wedge/_launcher.py`; `packages/wedge/tests/wedge/test_launcher.py`

[^zip-build]: `packages/wedge/src/wedge/_zipapp.py`; `packages/wedge/src/wedge/_bytecode.py`; `packages/wedge/src/wedge/_config.py:23-30`; `packages/wedge/src/wedge/_key.py`; `packages/wedge/src/wedge/_build.py:341-385`
[^zip-runtime]: `packages/wedge/src/wedge/_main.py`; `packages/wedge/tests/wedge/test_reexec.py`
[^zip-safety]: `packages/skillz/src/skillz_experiments/run/_open.py`; `packages/skillz/src/skillz_experiments/harness/_isolation.py`; `packages/skillz/tests/skillz_experiments/test_entrypoints_and_engine_hash.py`
[^zip-tests]: `packages/wedge/tests/wedge/test_bytecode.py`; `packages/wedge/tests/wedge/test_build.py`; `docs/agents/criteria/gh-581.md`
[^zip-guard]: `packages/skillz/src/skillz_experiments/_candidate.py:20-21`; `packages/skillz/tests/skillz_experiments/test_press_wedge_targets.py:299`; user approval on 2026-11-01
[^zip-selector]: `packages/wedge/src/wedge/_bytecode.py:35-89,155-195`; `packages/wedge/tests/wedge/test_bytecode.py:251-296`
[^zip-producers]: `.github/workflows/validate.yml:57,132`; `.github/workflows/publish-dist.yml:66`; `.github/workflows/release.yml:76`; `actions/wedge/action.yml:71`; `packages/wedge/tests/wedge/test_bytecode.py`

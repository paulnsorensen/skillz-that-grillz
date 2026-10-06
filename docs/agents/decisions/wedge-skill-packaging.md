# wedge skill packaging decisions

These records explain the design of `wedge`, a packaging tool at `lib/src/wedge/`, inside the `skillz-that-grillz` package at `lib/`. `wedge` shivs a `fromargs` CLI into a skill as a content-addressed `.pyz`. It supports legacy release assets and opt-in direct archives (ADR-010). Other repositories use it through the `actions/wedge` GitHub Action (ADR-007). The design brief was a session spec, not a durable spec; these records are the durable source of the design.

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
  - The launcher and the build both require Python 3.11 or newer on the host; there is no fallback for older interpreters.
  - Only a pure-Python dependency closure is supported. `wedge build` accepts a resolved wheel only when its PEP 425 Python tag set includes `py3`, its ABI tag is `none`, and its platform tag is `any`. It rejects any other wheel, and any resolved dependency that carries a platform marker (`sys_platform`, `platform_system`, …), with a clear error naming the offending package. A compressed universal tag such as `py2.py3-none-any` (shiv's only wheel) therefore passes.

### ADR-006: Keep Wedge and fromargs as separate distributions  [status: accepted]

- **Context:** PR #88 publishes `fromargs` from its own project. Wedge packages only its own distribution and uses configured project dependencies.
- **Decision:** Keep `fromargs` independent. A skill vendors local source through `include` and exports only the configured project's dependency closure, avoiding shiv's own transitive dependencies (`pip`, `setuptools`, `click`) in each `.pyz`.
- **Consequences:** The fromargs release lane remains independent. Only the post-merge Wedge workflow publishes assets; a tag points at a commit on `main` that workflow already published.

### ADR-007: Portable projects and a public composite action  [status: accepted]

- **Context:** Other repositories want to ship wedged skills. The first version assumed this repository's `lib/fromargs` layout, so it could not run in a consumer repository.
- **Decision:** `wedge.toml` names its layout. `project` (default `.`) contains `pyproject.toml` and `uv.lock`; `include` lists local package trees to vendor; `repo` is required and must be `owner/name`. `source` and each `include` entry must resolve inside `project`, reject symlinks, and have unique top-level names. Configured local inputs cannot overwrite installed dependencies. `uv export` omits local path packages, so consumers vendor them through `include`; unsupported URL or path requirements fail with guidance. Publication validates lock field types, names, digests, format, repository agreement, and duplicate skill names before side effects. `FORMAT_VERSION = 8` marks this integrated key layout and normalized-content digest.[^8] The public `actions/wedge/action.yml` composite action supports `command: check|publish`, runs Wedge with `uv run --locked` against its own `lib/` project, verifies the pinned gh digest, installs gh under runner temp without sudo, and exports its path.
- **Alternatives:** A separate Wedge repository with a root `action.yml`, or publishing Wedge and fromargs to PyPI. Deferred: the action needs no index because it runs from its own checkout.
- **Consequences:** Consumers reference `paulnsorensen/skillz-that-grillz/actions/wedge@<sha>`. Validation covers a standalone consumer project with its own `uv.lock`. The pinned `shiv` version remains exact because shiv writes its version into each `.pyz`. The Action preserves a failed Wedge result and exits nonzero so callers can diagnose publication failures.

### ADR-008: Pin the uncompressed contents, ship compressed assets  [status: accepted]

- **Context:** ADR-003 disabled ZIP compression because macOS and Linux zlib builds emitted different deflate bytes for identical contents, and the lock pinned the file's sha256. Uncompressed assets were about 3.7 times larger (8.5 MB against 2.3 MB for `cheese-cave`).
- **Decision:** The lock pins `content_sha256`: a sha256 over a domain tag and each member's length-prefixed name, size, and uncompressed bytes, in archive order.[^9] The builder sorts and normalizes members, computes that digest, then deflates them. The asset name embeds the first 12 hex characters of the digest. The launcher recomputes the digest before `execv` and refuses any archive that fails to parse or expands past 512 MiB. `wedge publish` builds on every run and treats an existing asset as published only when both its own build and the downloaded asset match the lock's content digest, so an asset uploaded from a developer machine is verified by the post-merge runner. The launcher template carries a copy of the digest function, and a test keeps the two equal.
- **Alternatives:** Record the runner's file sha256 in the lock through a bot pull request. Rejected: `main-protection` requires a PR with status checks, so it needs an App or a personal token and leaves locks pending until the bot PR merges. Hash the bytes of an uncompressed ZIP. Rejected: the launcher would need to re-serialize the archive, and `zipfile` output can change between Python versions.
- **Consequences:** A local `wedge lock` pins a digest that every host reproduces, so publication needs only `GITHUB_TOKEN` with `contents: write`. Bytes outside the members (the shebang line, the archive comment, trailing data) are not covered; Python does not execute them when the launcher runs the archive. Each launch decompresses and hashes the cached archive once.

- **Maintenance note:** On 2026-09-26 a lock built from a shared uv cache differed from CI's build in one member, `markdown_it/port.yaml`. The cached copy carried 366 hardlinks and a reflowed YAML block; a tool had edited a hardlinked install in place. `--require-hashes` did not catch it and `UV_REFRESH` did not clear it. `--no-cache` in the builder removes this class of drift; the tool that edited the file is not identified.

### ADR-009: Wedge fans out across skills; consumers keep no build loop  [status: accepted]

- **Context:** `build` and `lock` took one skill directory while `check` and `publish` discovered skills under `--root`. A repository with 13 skills over one package wrote its own thread pool and `xargs -P` loop around `wedge`, renamed the outputs, and paid 13 `uv export` and 13 `--no-cache` wheel downloads per run. It also pinned the wedge commit in a wrapper script and in the workflow, with a test to keep the two equal.
- **Decision:** Every command resolves skills the same way: positional directories, else `*/wedge.toml` under each `--root`, and an empty discovery is an error. Positional directories and `--root` are exclusive, and every `--root` must be a directory. Resolved duplicate directories collapse to one, in first-seen order. `build`, `lock`, and `publish` run skills through one `fan_out` helper (`--jobs` threads, one outcome per skill, one skill's failure never stops the others). `fan_out` catches every exception per item on purpose, so one skill's crash never stops the rest. `build_many` groups skills by resolved project, source, includes, and groups. It populates one site directory per group and shivs each skill from it; `lock` and `publish` build through it. `build` and `lock` print `{name: {...}}`. `wedge.toml` gains `groups`, a list of uv dependency groups exported into the closure. A project whose CLI dependencies must stay out of its published library uses this. `groups` may not name `dev`: uv drops a `--group dev` export, so the CLI ships without its dependencies. An unknown key in `wedge.toml` fails, naming the file that set it. Legacy `wedge check` rejects a `.pyz` committed beside a launcher (ADR-002) and reports duplicate skill names. Direct bundles use `wedge bundle --check` (ADR-010). A `wedge.toml` in a discovery root supplies defaults for every skill beside it (any key but `name` and `entry`; the skill's file wins). It joins the key only when present, so existing keys stand. A parent directory that holds a `SKILL.md` is a skill, not a discovery root, so a CLI directory nested in a skill reads no defaults (2026-10-05, #151). With `--branch`, `publish` refuses unless `--target` resolves to the checked-out HEAD of every skill directory. It then compares that commit against the branch through the compare API. A shallow checkout still passes, and a branch name alone no longer suffices. A uv-managed consumer pins wedge once, as the sole dependency of a small uv project of its own (`tools/wedge/`). It runs `uv run --locked --project tools/wedge wedge …` locally and in CI; the composite action remains for repositories without uv. The pin cannot live in the skill project's own lock: uv carries the `fromargs` path source from `lib/pyproject.toml` into any lock that depends on wedge. That replaces the `fromargs` wheel in the `.pyz` closure with a git checkout. The post-merge workflow skips `publish` when no `skills/*/wedge.toml` exists, so a repository with no wedged skills yet stays green.
- **Alternatives:** Per-skill files only. Rejected: 13 skills repeated the same five lines, and a skill directory is copied for use, not for building. A wedge dependency group in the consumer's own project. Rejected: uv carries wedge's `fromargs` path source into that lock. Skipping the publish build when the asset already matches the lock. Rejected: the rebuild is what proves a developer-machine lock reproducible (ADR-008). Verifying only the compressed bytes at launch. Rejected: the launcher's contract is the content digest (ADR-004).
- **Consequences:** A consumer's glue shrinks to one pinned tool project and one command per recipe; the manifest gives tests each archive's path. Site sharing makes a stale skill build before its key mismatch is reported, since keys come from the same pass that resolves the site. The action's `uses:` pin and the consumer's `uv.lock` pin remain two pins only for consumers that use both.

### ADR-010: Direct archives are an opt-in delivery mode  [status: accepted]

- **Context:** Some consumers copy a self-contained skill archive and cannot depend on a first-run download. They accept a committed binary in exchange for direct execution.
- **Decision:** `wedge bundle` writes an executable `scripts/<name>.pyz` without a lock or launcher. It builds each skill, writes through a temporary file, and atomically replaces the target. `wedge bundle --check` rebuilds and compares the uncompressed-content digest, the canonical shebang, and executable permission without changing the committed archive.[^10] A corrupt DEFLATE stream is an invalid-bundle outcome for its skill; checking continues for later skills. Optional `source_paths` selects paths below `source`, preserves their archive paths, and joins the selection to the content key. An absent selection retains the full source tree.[^11]
- **Alternatives:** Keep the release-asset launcher as the only delivery mode. Rejected for consumers that need a copied skill to run without a network fetch.
- **Consequences:** Direct consumers commit the larger `.pyz` and run `wedge bundle --check` in CI. The legacy lock, launcher, and release-asset workflow remain available. The `wedge check` restriction on a `.pyz` beside a launcher still applies only to that legacy mode. `wedge publish` reports a skill with `scripts/<name>.pyz` and no lock as `skipped`, because no release asset exists for it; a missing lock without a bundle still fails. The repository's own `skillz` skill ships two direct bundles: `scripts/skillz-experiment.pyz` and the builder at `wedge/scripts/wedge.pyz`.

_Source: PR #103, `lib/src/wedge/`, `lib/tests/wedge/test_cli.py`, and `lib/README.md` · Updated: 2026-09-27 · Supersedes: ADR-002's blanket ban on committed `.pyz` files for opt-in direct bundles_

[^10]: `lib/src/wedge/_bundle.py`; `lib/tests/wedge/test_cli.py`
[^11]: `lib/src/wedge/_config.py`; `lib/src/wedge/_key.py`; `lib/src/wedge/_build.py`; `lib/tests/wedge/test_key.py`

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

The skill ships the builder as its own direct bundle, `skills/skillz/wedge/scripts/wedge.pyz`, built from `lib/src/wedge` with a `wedge` dependency group that pins shiv.[^15]
The builder sits in a subdirectory because wedge reads one `wedge.toml` per skill directory, and `skills/skillz/wedge.toml` builds `skillz-experiment`.
A user who installs only the skill can then build without a wedge checkout or tool project; `uv` stays a host requirement because it is a native binary.
Inside an archive, `sys.executable` is the host interpreter, so the builder runs `python -m shiv` with `PYTHONPATH` set to the directory that holds the imported shiv.
CI consumers still pin wedge in a tool project (ADR-009).

On 2026-10-05 (#151), the separate `/wedge` skill moved into `/skillz wedge`.
This reverses the earlier split, in which `/skillz wedge` wrote only a brief and `/wedge` implemented it.
The goal is maximum CLI offload, so the offload decision and the packaging decision are one procedure.
`/skillz` is user-only (`disable-model-invocation: true`), unlike the old `/wedge` skill.
A prompt such as "wedge this" no longer invokes it on its own; the user types `/skillz wedge`. The invocation policy is unchanged.
`improve` now proposes each offload in its approval question instead of recording it as a residual.
The autoimprove wedge arm (`skillz-autoimprove.md`, ADR-005) is unchanged; its alignment is deferred.

_Source: the published teaching skill and the records above · Updated: 2026-10-05_

[^12]: `skills/skillz/SKILL.md`; `skills/skillz/references/offload.md`
[^13]: `skills/skillz/references/wedge-packaging.md`; `lib/tests/wedge/test_skill_template.py`
[^15]: `skills/skillz/wedge/wedge.toml`; `pyproject.toml`; `lib/src/wedge/_build.py`; `lib/tests/wedge/test_bundled_cli.py`

[^1]: `lib/src/wedge/_key.py`
[^2]: `lib/src/wedge/_publish.py`
[^3]: `lib/src/wedge/_cli.py`
[^4]: `lib/src/wedge/_build.py`; `lib/src/wedge/_key.py`; `lib/tests/wedge/test_build.py`
[^5]: `.github/workflows/publish-fromargs.yml`; `lib/fromargs/pyproject.toml`
[^6]: `lib/pyproject.toml`; `lib/src/wedge/_build.py`; `lib/src/wedge/_resolve.py`
[^7]: `justfile`; `.github/workflows/wedge.yml`
[^8]: `lib/src/wedge/_config.py`; `lib/src/wedge/_key.py`; `lib/src/wedge/_resolve.py`; `lib/src/wedge/_publish.py`; `actions/wedge/action.yml`; `lib/tests/wedge/test_consumer.py`
[^9]: `lib/src/wedge/_digest.py`; `lib/src/wedge/_launcher.py`; `lib/tests/wedge/test_launcher.py`

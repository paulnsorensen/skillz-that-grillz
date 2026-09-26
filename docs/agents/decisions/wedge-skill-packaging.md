# wedge skill packaging decisions

These records explain the design of `wedge`, a packaging tool at `lib/src/wedge/`, inside the `skillz-that-grillz` package at `lib/`. `wedge` shivs a `fromargs` CLI into a skill as a content-addressed `.pyz` release asset. Other repositories use it through the `actions/wedge` GitHub Action (ADR-007). The design brief was a session spec, not a durable spec; these records are the durable source of the design.

## Records

### ADR-001: Key the build on content, not the git commit  [status: accepted]

- **Context:** Two merges can land on `main` seconds apart and each trigger a post-merge publish job. A key derived from `HEAD`'s commit sha would make each run publish a new asset for the same bytes, even when neither commit touched the skill's inputs. A build must stay safe to run more than once, from more than one commit, without changing or deleting an asset another run already published.
- **Decision:** The key is a sha256 hash over a canonical JSON document: a `FORMAT_VERSION` constant, the target Python version, the sha256 of the skill's `wedge.toml`, the sha256 of the project's `uv.lock`, and a sorted list of (`project-relative posix path`, `sha256(file bytes)`) pairs for every file in `source` and each `include` entry.[^1] `__pycache__` and `*.pyc` are excluded. Paths are relative to the configured project directory, not the checkout, so the key is portable (ADR-007).
- **Alternatives:** Key on the git commit sha of `HEAD`. Rejected: it changes on every commit, even a commit that does not touch the skill, and it gives two racing runs no way to converge on one asset.
- **Consequences:** Two runs that race after a merge each compute the same key from the same bytes and publish the same asset name; the later run finds the asset already there and skips its upload.[^2] Git's linear history still decides which key the committed lock on `main` points at. No mutable "latest" pointer exists. Every asset is immutable and append-only; nothing ever deletes or overwrites a published asset.

### ADR-002: Release assets, never a committed .pyz  [status: accepted]

- **Context:** A `.pyz` bundles third-party wheels and can reach megabytes. Committing it would bloat every clone and every diff, and two builds of the same commit could still disagree byte-for-byte without a reproducibility guarantee.
- **Decision:** The `.pyz` is a GitHub release asset only. The skill directory commits only a small launcher script and a lock file (`name`, `key`, `sha256`, `release`, `asset`, `repo`, `format`).[^3] `wedge publish` builds the asset once and uploads it to one rolling prerelease tagged `wedge`, with `--latest=false` so the repository's "Latest release" badge stays on real version tags. Asset names embed the key (`<name>-<key12>.pyz`), so publishing is append-only.
- **Alternatives:** Commit the `.pyz` directly. Rejected: it bloats the git history with binary blobs that never compress well and turns every skill change into a large diff.
- **Consequences:** A skill needs network access on first run to fetch its `.pyz`; the launcher caches it after that. The repository stays small. CI must run `wedge publish` after every merge that changes a wedged skill's inputs, or the committed lock can reference an asset that does not exist yet.

### ADR-003: Reproducible builds, verified by hash  [status: accepted]

- **Context:** The content key promises that any machine building the same inputs gets the same asset. That promise only holds if the build itself is deterministic: file timestamps, wheel resolution order, and installed metadata can otherwise vary between machines and even between runs on the same machine.
- **Decision:** `wedge build` fixes `SOURCE_DATE_EPOCH`, uses shiv's `--reproducible` and `--uncompressed` flags, writes a fixed `/usr/bin/env python3` shebang, and evaluates markers against a fixed target Python (the `requires-python` floor, 3.11). It strips volatile install files (`RECORD`, `INSTALLER`, `REQUESTED`, `direct_url.json`, `__pycache__`) and installed `bin/` scripts before zipping. `uv pip install --target` writes console scripts with the builder's absolute Python path. Shiv's reproducible mode does not normalize those bytes. Removing host-bound scripts and avoiding compressor-dependent ZIP bytes changes the asset format. Shiv leaves its bundled bootstrap entries in filesystem enumeration order, so `wedge build` sorts all ZIP entries after shiv runs. The builder normalizes ZIP timestamps, creator system, and file/directory modes, so umask 002 and 022 produce identical bytes. Hosted Linux CI and local macOS then produced the same archive hash. `FORMAT_VERSION = 7` prevents an earlier key from naming different bytes.[^4] Third-party dependencies resolve from the configured project's `uv.lock` with hashes and install with `--require-hashes --no-deps --target`, so the exact wheel bytes are pinned. Source and include trees copy into the site directory directly, not through a wheel build.
- **Alternatives:** Accept whatever the local `uv`/`pip` resolver picks at build time. Rejected: it does not guarantee the same wheel on every machine, which breaks the content key's core promise.
- **Consequences:** To reproduce a published asset locally: check out the commit the lock's key was computed against, run `wedge build <skill-dir> --out <dir>`, and compare the resulting sha256 to the lock's `sha256` field. Any mismatch means the local toolchain, `uv.lock`, or inputs drifted from what produced the asset. Build trees reject symlinks before hashing or copying, and lock validation rejects malformed names, keys, digests, assets, formats, releases, and repositories before publication.

### ADR-004: The launcher trusts only the sha256 in its committed lock  [status: accepted]

- **Context:** The launcher downloads a binary over the network before it runs it. A compromised release, a stale cache entry, or a truncated download must never lead to code execution the lock did not authorize.
- **Decision:** The committed launcher is a stdlib-only Python script that reads the `.wedge.json` lock beside it, resolves a cache path under `${WEDGE_CACHE:-${XDG_CACHE_HOME:-~/.cache}/wedge}/<asset>`, and downloads the asset only when the cached file is missing or its sha256 does not match the lock. It writes the download to a temp file in the cache directory, hashes it, and only then renames it into place atomically. Any hash mismatch, including a `WEDGE_PYZ` override pointing at a local file, refuses to run: it prints one JSON line on stderr and exits 3, and never calls `execv`. `WEDGE_BASE_URL` must be `https://…` unless the host is `127.0.0.1` or `localhost`, which keeps a plain-`http` override usable in tests without opening the default path to a downgrade.
- **Alternatives:** Trust the downloaded bytes once the HTTPS connection succeeds. Rejected: TLS authenticates the server, not the asset; a compromised or mismatched release asset would still run.
- **Consequences:** The lock's sha256 is the entire trust boundary. Regenerating the lock after any source change is required, and `wedge check` fails the build when the lock's key goes stale or the launcher text drifts from the template, so a stale lock cannot silently ship. Publication rejects duplicate configured skill names and CLI/config/lock repository disagreement before side effects. The post-merge workflow has no shared concurrency group because cancelling pending runs can lose append-only assets.

### ADR-005: Known limits  [status: accepted]

- **Context:** The design above trades some capability for simplicity and safety in the first version.
- **Decision:** Ship without these, and revisit only if a real need appears.
- **Consequences:**
  - No asset garbage collection. The rolling `wedge` release keeps every published asset forever; nothing deletes an old key's `.pyz`.
  - The launcher and the build both require Python 3.11 or newer on the host; there is no fallback for older interpreters.
  - Only a pure-Python dependency closure is supported. `wedge build` rejects any resolved wheel that is not `py3-none-any` or that carries a platform marker (`sys_platform`, `platform_system`, …), with a clear error naming the offending package.

### ADR-006: Keep Wedge and fromargs as separate distributions  [status: accepted]

- **Context:** PR #88 publishes `fromargs` from its own project. Wedge packages only its own distribution and uses configured project dependencies.
- **Decision:** Keep `fromargs` independent. A skill vendors local source through `include` and exports only the configured project's dependency closure, avoiding shiv's own transitive dependencies (`pip`, `setuptools`, `click`) in each `.pyz`.
- **Consequences:** The fromargs release lane remains independent. Only the post-merge Wedge workflow publishes assets; a tag points at a commit on `main` that workflow already published.

### ADR-007: Portable projects and a public composite action  [status: accepted]

- **Context:** Other repositories want to ship wedged skills. The first version assumed this repository's `lib/fromargs` layout, so it could not run in a consumer repository.
- **Decision:** `wedge.toml` names its layout. `project` (default `.`) contains `pyproject.toml` and `uv.lock`; `include` lists local package trees to vendor; `repo` is required and must be `owner/name`. `source` and each `include` entry must resolve inside `project`, reject symlinks, and have unique top-level names. `uv export` omits local path packages, so consumers vendor them through `include`; unsupported URL or path requirements fail with guidance. Publication validates lock field types, names, digests, format, repository agreement, and duplicate skill names before side effects. `FORMAT_VERSION = 7` marks this integrated key layout.[^8] The public `actions/wedge/action.yml` composite action supports `command: check|publish`, runs Wedge with `uv run --locked` against its own `lib/` project, and verifies the pinned gh digest. This repository's workflow dogfoods `./actions/wedge` without shared concurrency.
- **Alternatives:** A separate Wedge repository with a root `action.yml`, or publishing Wedge and fromargs to PyPI. Deferred: the action needs no index because it runs from its own checkout.
- **Consequences:** Consumers reference `paulnsorensen/skillz-that-grillz/actions/wedge@<sha>`. Validation covers a standalone consumer project with its own `uv.lock`. The pinned `shiv` version remains exact because shiv writes its version into each `.pyz`.

_Source: the wedge implementation at `lib/src/wedge/`, `actions/wedge/`, and hosted CI reproducibility failures · Updated: 2026-09-26_

[^1]: `lib/src/wedge/_key.py`
[^2]: `lib/src/wedge/_publish.py`
[^3]: `lib/src/wedge/_cli.py`
[^4]: `lib/src/wedge/_build.py`; `lib/src/wedge/_key.py`; `lib/tests/wedge/test_build.py`
[^5]: `.github/workflows/publish-fromargs.yml`; `lib/fromargs/pyproject.toml`
[^6]: `lib/pyproject.toml`; `lib/src/wedge/_build.py`; `lib/src/wedge/_resolve.py`
[^7]: `justfile`; `.github/workflows/wedge.yml`
[^8]: `lib/src/wedge/_config.py`; `lib/src/wedge/_key.py`; `lib/src/wedge/_resolve.py`; `lib/src/wedge/_publish.py`; `actions/wedge/action.yml`; `lib/tests/wedge/test_consumer.py`

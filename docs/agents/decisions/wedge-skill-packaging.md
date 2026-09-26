# wedge skill packaging decisions

These records explain the design of `wedge`, a packaging tool at `lib/src/wedge/`, inside the `skillz-that-grillz` package at `lib/`. `wedge` shivs a pure-Python skill CLI, such as a `fromargs` CLI, into a skill as a content-addressed `.pyz` release asset. Other repositories use it through the `actions/wedge` GitHub Action (ADR-006). The design brief was a session spec, not a durable spec; these records are the durable source of the design.

## Records

### ADR-001: Key the build on content, not the git commit  [status: accepted]

- **Context:** Two merges can land on `main` seconds apart and each trigger a post-merge publish job. A key derived from `HEAD`'s commit sha would make each run publish a new asset for the same bytes, even when neither commit touched the skill's inputs. A build must stay safe to run more than once, from more than one commit, without changing or deleting an asset another run already published.
- **Decision:** The key is a sha256 hash over a canonical JSON document: a `FORMAT_VERSION` constant, the target Python version, the sha256 of the skill's `wedge.toml`, the sha256 of the project's `uv.lock`, and a sorted list of `(project-relative posix path, sha256(file bytes))` pairs for every file in `source` and in each `include` entry.[^1] `__pycache__` and `*.pyc` are excluded. Paths are relative to the configured project directory, not to a repository root, so the key does not depend on the checkout path or on where the skill directory sits (ADR-007).
- **Alternatives:** Key on the git commit sha of `HEAD`. Rejected: it changes on every commit, even a commit that does not touch the skill, and it gives two racing runs no way to converge on one asset.
- **Consequences:** Two runs that race after a merge each compute the same key from the same bytes and publish the same asset name; the later run finds the asset already there and skips its upload.[^2] Git's linear history still decides which key the committed lock on `main` points at. No mutable "latest" pointer exists. Every asset is immutable and append-only; nothing ever deletes or overwrites a published asset.

### ADR-002: Release assets, never a committed .pyz  [status: accepted]

- **Context:** A `.pyz` bundles third-party wheels and can reach megabytes. Committing it would bloat every clone and every diff, and two builds of the same commit could still disagree byte-for-byte without a reproducibility guarantee.
- **Decision:** The `.pyz` is a GitHub release asset only. The skill directory commits only a small launcher script and a lock file (`name`, `key`, `sha256`, `release`, `asset`, `repo`, `format`).[^3] `wedge publish` builds the asset once and uploads it to one rolling prerelease tagged `wedge`, with `--latest=false` so the repository's "Latest release" badge stays on real version tags. Asset names embed the key (`<name>-<key12>.pyz`), so publishing is append-only.
- **Alternatives:** Commit the `.pyz` directly. Rejected: it bloats the git history with binary blobs that never compress well and turns every skill change into a large diff.
- **Consequences:** A skill needs network access on first run to fetch its `.pyz`; the launcher caches it after that. The repository stays small. CI must run `wedge publish` after every merge that changes a wedged skill's inputs, or the committed lock can reference an asset that does not exist yet.

### ADR-003: Reproducible builds, verified by hash  [status: accepted]

- **Context:** The content key promises that any machine building the same inputs gets the same asset. That promise only holds if the build itself is deterministic: file timestamps, wheel resolution order, and installed metadata can otherwise vary between machines and even between runs on the same machine.
- **Decision:** `wedge build` fixes `SOURCE_DATE_EPOCH`, uses shiv's `--reproducible` and `--uncompressed` flags, writes a fixed `/usr/bin/env python3` shebang, and evaluates markers against a fixed target Python (the `requires-python` floor, 3.11). It strips volatile install files (`RECORD`, `INSTALLER`, `REQUESTED`, `direct_url.json`, `__pycache__`) and installed `bin/` scripts before zipping. `uv pip install --target` writes console scripts with the builder's absolute Python path. Shiv's reproducible mode does not normalize those bytes. Removing host-bound scripts and avoiding compressor-dependent ZIP bytes changes the asset format. Shiv leaves its bundled bootstrap entries in filesystem enumeration order, so `wedge build` sorts all ZIP entries after shiv runs. Hosted Linux CI and local macOS then produced the same archive hash. `FORMAT_VERSION = 4` prevents an earlier key from naming different bytes.[^4] Third-party dependencies resolve from the project's `uv.lock` with hashes and install with `--require-hashes --no-deps --target`, so the exact wheel bytes are pinned. The `include` trees (for example `fromargs`) and the skill's CLI source copy into the site directory directly, not through a wheel build. `wedge build` runs shiv as `sys.executable -m shiv`, so a different `shiv` on `PATH` cannot change the bytes.
- **Alternatives:** Accept whatever the local `uv`/`pip` resolver picks at build time. Rejected: it does not guarantee the same wheel on every machine, which breaks the content key's core promise.
- **Consequences:** To reproduce a published asset locally: check out the commit the lock's key was computed against, run `wedge build <skill-dir> --out <dir>`, and compare the resulting sha256 to the lock's `sha256` field. Any mismatch means the local toolchain, `uv.lock`, or inputs drifted from what produced the lock.

### ADR-004: The launcher trusts only the sha256 in its committed lock  [status: accepted]

- **Context:** The launcher downloads a binary over the network before it runs it. A compromised release, a stale cache entry, or a truncated download must never lead to code execution the lock did not authorize.
- **Decision:** The committed launcher is a stdlib-only Python script that reads the `.wedge.json` lock beside it, resolves a cache path under `${WEDGE_CACHE:-${XDG_CACHE_HOME:-~/.cache}/wedge}/<asset>`, and downloads the asset only when the cached file is missing or its sha256 does not match the lock. It writes the download to a temp file in the cache directory, hashes it, and only then renames it into place atomically. Any hash mismatch, including a `WEDGE_PYZ` override pointing at a local file, refuses to run: it prints one JSON line on stderr and exits 3, and never calls `execv`. `WEDGE_BASE_URL` must be `https://…` unless the host is `127.0.0.1` or `localhost`, which keeps a plain-`http` override usable in tests without opening the default path to a downgrade.
- **Alternatives:** Trust the downloaded bytes once the HTTPS connection succeeds. Rejected: TLS authenticates the server, not the asset; a compromised or mismatched release asset would still run.
- **Consequences:** The lock's sha256 is the entire trust boundary. Regenerating the lock after any source change is required, and `wedge check` fails the build when the lock's key goes stale or the launcher text drifts from the template, so a stale lock cannot silently ship.

### ADR-005: Known limits  [status: accepted]

- **Context:** The design above trades some capability for simplicity and safety in the first version.
- **Decision:** Ship without these, and revisit only if a real need appears.
- **Consequences:**
  - No asset garbage collection. The rolling `wedge` release keeps every published asset forever; nothing deletes an old key's `.pyz`.
  - The launcher and the build both require Python 3.11 or newer on the host; there is no fallback for older interpreters.
  - Only a pure-Python dependency closure is supported. `wedge build` rejects any resolved wheel that is not `py3-none-any` or that carries a platform marker (`sys_platform`, `platform_system`, …), with a clear error naming the offending package.

### ADR-006: Keep Wedge and fromargs as separate distributions  [status: accepted]

- **Context:** PR #88 publishes `fromargs` from `lib/fromargs/`. PR #89 originally moved its code into the Wedge distribution at `lib/`, which would remove the release workflow's package path.[^5]
- **Decision:** Keep `fromargs` at `lib/fromargs/` and package only `wedge` in the `skillz-that-grillz` wheel and sdist. Wedge depends on `fromargs>=0.1.0,<0.2` for installation. Its local development lock uses an editable path dependency. A skill `.pyz` copies `fromargs` source directly and exports only its third-party dependencies. The export excludes the local package and the build-only `shiv` executable.[^6]
- **Consequences:** `fromargs-v*` remains the library's PyPI lane. The skills' `v[0-9]*` lane can add a separate Wedge PyPI publish step later. No Wedge PyPI upload runs now. `just build` tests both packages and checks the sample skill lock.[^7]

### ADR-007: Portable projects and a public composite action  [status: accepted]

- **Context:** Other repositories want to ship wedged skills. The first version found a repository root by looking for `lib/fromargs/src/fromargs` and `lib/uv.lock`, always vendored `lib/fromargs/src/fromargs`, and defaulted `repo` to this repository. A consumer repository has none of these files, so wedge could not run there.
- **Decision:** `wedge.toml` names its own layout. `project` (default `.`) is the directory with `pyproject.toml` and `uv.lock`. `include` lists local package trees to vendor. `repo` is required and must be `owner/name`. `source` and each `include` entry must resolve inside `project`, and their top-level names must be unique. `uv export` omits local path packages, such as `fromargs`, so a skill vendors them through `include`. Any other export line that is not `name==version`, such as a URL dependency, fails the build and points the user to `include`. `wedge publish` refuses a lock whose `repo` differs from `--repo`. `FORMAT_VERSION = 5` marks the new key layout.[^8] The public action is `actions/wedge/action.yml`, a composite action with `command: check|publish`. It runs wedge with `uv run --locked` against the `lib/` project in its own checkout, so pinning the action SHA pins wedge and its dependencies. The internal `.github/actions/wedge-publish` action is removed, and this repository's `wedge` workflow uses `./actions/wedge`.
- **Alternatives:** A separate `wedge` repository with a root `action.yml`, listed on GitHub Marketplace. Deferred: it needs a new repository and a copy of `fromargs`. Publish `wedge` and `fromargs` to PyPI. Deferred: the name `wedge` is taken on PyPI, and the action needs no index because it runs from its own checkout. Local use installs from git with `uvx --from 'skillz-that-grillz @ git+…#subdirectory=lib'`.
- **Consequences:** Consumers reference `paulnsorensen/skillz-that-grillz/actions/wedge@<sha>`. The action is not listed on Marketplace. The `validate` workflow runs the action with `command: check` against `lib/examples/consumer`, a project with its own `uv.lock` and no `lib/` layout, and `lib/tests/wedge/test_consumer.py` re-locks that example outside this checkout. `publish` needs a Linux X64 runner for the pinned `gh` binary, which the action verifies against its release sha256. A local `uvx --from git+…` install ignores `lib/uv.lock`, so the `shiv` dependency is pinned to `shiv==1.0.8` exactly: shiv writes its version into each `.pyz`, and a different shiv would give a lock that the action cannot reproduce.

_Source: the wedge implementation at lib/src/wedge/, actions/wedge/, and hosted CI reproducibility failure · Updated: 2026-09-26_

[^1]: lib/src/wedge/_key.py
[^2]: lib/src/wedge/_publish.py:97-128
[^3]: lib/src/wedge/_cli.py:44-47
[^4]: lib/src/wedge/_build.py:100-158; lib/src/wedge/_key.py:16; lib/tests/wedge/test_build.py:17-61; https://github.com/linkedin/shiv/blob/main/src/shiv/builder.py#L164-L177; https://github.com/paulnsorensen/skillz-that-grillz/actions/runs/36223738163
[^5]: .github/workflows/publish-fromargs.yml:33-58; lib/fromargs/pyproject.toml:1-3
[^6]: lib/pyproject.toml:20-48; lib/src/wedge/_build.py:43-62; lib/src/wedge/_resolve.py:17-35
[^7]: justfile:33-45; .github/workflows/release.yml
[^8]: lib/src/wedge/_config.py; lib/src/wedge/_key.py; lib/src/wedge/_resolve.py; lib/src/wedge/_publish.py; actions/wedge/action.yml; lib/tests/wedge/test_consumer.py

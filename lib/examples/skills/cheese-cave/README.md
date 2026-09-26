---
name: cheese-cave
description: Example wedge fixture. Tracks wheels of cheese as they ripen through a self-healing fromargs CLI, shivved into a single .pyz.
---

# cheese-cave

An example skill wedged with `wedge`. It ships a committed launcher
(`scripts/cheese-cave`) and a committed lock (`scripts/cheese-cave.wedge.json`).
The `.pyz` itself is not committed; a post-merge job builds it and uploads it
as a release asset.

## Use

Run the launcher directly. It downloads the matching `.pyz` on first use,
verifies its sha256 against the lock, caches it, and execs it.

```bash
scripts/cheese-cave wheels list
scripts/cheese-cave age brie --weeks "2 --dry-run"
```

Set `WEDGE_PYZ=<path>` to run a local build instead of downloading a release
asset. Set `WEDGE_CACHE` to change the cache directory.

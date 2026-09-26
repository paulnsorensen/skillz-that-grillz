# wedge consumer example

This directory has the layout that a repository outside `skillz-that-grillz`
uses with the [wedge GitHub Action](../../../actions/wedge/README.md).
It has its own `pyproject.toml` and `uv.lock` and does not use `lib/`.

- `skills/hello/wedge.toml` points `project` at this directory.
- `skills/hello/hello.py` is a stdlib-only CLI.
- `skills/hello/scripts/` holds the lock and launcher from `wedge lock`.

The lock names `example-owner/wedge-consumer`, a placeholder repository.
Nothing publishes this example; CI runs `wedge check` on it.

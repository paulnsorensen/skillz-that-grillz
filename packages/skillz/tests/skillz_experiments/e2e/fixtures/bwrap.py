#!/usr/bin/env python3
"""Synthetic OS boundary for orchestration tests; not isolation evidence."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    args = sys.argv[1:]
    workspace = Path(args[args.index("--chdir") + 1])
    capture: Path | None = None
    for index, item in enumerate(args):
        if item == "--bind" and args[index + 2] == "/run/skillz-capture":
            capture = Path(args[index + 1])
    start = max(index for index in range(len(args) - 1) if args[index:index + 2] == ["/usr/bin/env", "-i"])
    values = args[start + 2:]
    environment: dict[str, str] = {}
    while values and "=" in values[0] and not values[0].startswith("/"):
        name, value = values.pop(0).split("=", 1)
        environment[name] = value
    if (values[:2] == ["/usr/bin/python3", "-c"] and len(values) == 3
            and "read isolation failed" in values[2] and "network isolation failed" in values[2]):
        print("isolation-ok")
        return 0
    result = subprocess.run(values, cwd=workspace, env=environment or os.environ.copy(),
                            capture_output=True, text=True, check=False)
    _ = sys.stdout.write(result.stdout)
    if capture is not None:
        _ = (capture / "stderr").write_text(result.stderr)
    else:
        _ = sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

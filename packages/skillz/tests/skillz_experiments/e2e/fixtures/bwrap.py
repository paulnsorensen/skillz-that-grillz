#!/usr/bin/env python3
"""Synthetic OS boundary for orchestration tests; not isolation evidence."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from vendor_common import is_isolation_probe, parse_env_prefix


def main() -> int:
    args = sys.argv[1:]
    workspace = Path(args[args.index("--chdir") + 1])
    capture: Path | None = None
    for index, item in enumerate(args):
        if item == "--bind" and args[index + 2] == "/run/skillz-capture":
            capture = Path(args[index + 1])
    start = max(index for index in range(len(args) - 1) if args[index:index + 2] == ["/usr/bin/env", "-i"])
    environment, values = parse_env_prefix(args[start + 2:])
    if is_isolation_probe(values):
        print("isolation-ok")
        return 0
    result = subprocess.run(values, cwd=workspace, env=environment,
                            capture_output=True, text=True, check=False)
    _ = sys.stdout.write(result.stdout)
    if capture is not None:
        _ = (capture / "stderr").write_text(result.stderr)
    else:
        _ = sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

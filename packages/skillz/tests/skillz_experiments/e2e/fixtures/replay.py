#!/usr/bin/env python3
"""Replay an exported skill through the same task fixture on unseen input."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vendor_common import task


def main() -> None:
    workspace = Path(sys.argv[1])
    answer, _ = task(workspace, "transform-skill")
    print(json.dumps(answer))


if __name__ == "__main__":
    main()

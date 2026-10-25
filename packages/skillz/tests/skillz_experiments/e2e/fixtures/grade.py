#!/usr/bin/env python3
"""Grade actual task output against the visible input transformation."""
from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    source = Path("fixture.md").read_text()
    output = Path("output/output/result.txt").read_text()
    print(json.dumps({"score": float(output == source.upper())}))


if __name__ == "__main__":
    main()

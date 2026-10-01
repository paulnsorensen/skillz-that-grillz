"""Example wedged CLI: print one JSON greeting on stdout."""

from __future__ import annotations

import json
import sys


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "world"
    print(json.dumps({"greeting": f"hello, {name}"}))


if __name__ == "__main__":
    main()

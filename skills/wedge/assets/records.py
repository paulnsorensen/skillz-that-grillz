"""Count normalized text records with stable ordering."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import fromargs

app = fromargs.App("records", help="Count nonempty UTF-8 lines.")


@app.command(limit=2)
def counts(path: Path, *, minimum: int = 1) -> list[dict[str, str | int]]:
    """Count stripped lines, ordered by count and then value."""
    if minimum < 1:
        raise fromargs.CliError("--minimum must be at least 1")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise fromargs.contract_error(exc, context=f"cannot read {path}") from exc
    totals = Counter(value for line in lines if (value := line.strip()))
    return [
        {"value": value, "count": count}
        for value, count in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
        if count >= minimum
    ]


def main() -> int:
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())

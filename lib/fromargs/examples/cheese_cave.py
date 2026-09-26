"""cheese-cave: an example agent-friendly CLI built on fromargs.

The CLI tracks wheels of cheese as they ripen. Run it from the repository root:

    uv run --project lib/fromargs python lib/fromargs/examples/cheese_cave.py wheels list

Agents often send malformed argv. Each call below still runs the intended
command. ``run`` prints one ``note:`` line on stderr only for quote-split repairs:

    cheese_cave.py --json wheels list              # --json is a no-op, dropped
    cheese_cave.py age brie --weeks "2 --dry-run"  # splits the merged argument
    cheese_cave.py --json age brie --weeks "2 --dry-run"  # drops --json silently; notes the split

Cyclopts itself binds ``--dry_run`` to ``--dry-run``. It also answers a
misspelled command such as ``wheels lst`` with ``Did you mean "list"?``.
"""

import sys
from dataclasses import dataclass

import fromargs

LIST_LIMIT = 3


@dataclass
class Wheel:
    name: str
    style: str
    weeks: int


def starter_cave() -> dict[str, Wheel]:
    """Return a new cave with four wheels, keyed by name."""
    wheels = [
        Wheel("brie", "bloomy", 4),
        Wheel("comte", "alpine", 52),
        Wheel("gouda", "washed-curd", 26),
        Wheel("stilton", "blue", 12),
    ]
    return {wheel.name: wheel for wheel in wheels}


def build_app(cave: dict[str, Wheel]) -> fromargs.App:
    """Build the cheese-cave CLI over ``cave``; commands change it in place."""
    app = fromargs.App("cheese-cave", help="Track wheels of cheese as they ripen.")
    wheels = app.group("wheels", help="Inspect the wheels in the cave.")

    @wheels.command(name="list", limit=LIST_LIMIT)
    def list_wheels() -> list[Wheel]:
        """List the wheels, oldest first."""
        return sorted(cave.values(), key=lambda wheel: -wheel.weeks)

    @wheels.command
    def show(name: str) -> Wheel:
        """Show one wheel."""
        return _find(cave, name)

    @app.command
    def age(name: str, *, weeks: int, dry_run: bool = False) -> dict[str, object]:
        """Age one wheel for more weeks."""
        if weeks < 1:
            raise fromargs.CliError(f"--weeks must be at least 1, got {weeks}")
        wheel = _find(cave, name)
        total = wheel.weeks + weeks
        if not dry_run:
            wheel.weeks = total
        return {"name": wheel.name, "weeks": total, "dry_run": dry_run}

    @app.command
    def load(record: str) -> Wheel:
        """Add a wheel from a ``name:style:weeks`` record."""
        try:
            name, style, weeks = record.split(":")
            wheel = Wheel(name, style, int(weeks))
        except ValueError as exc:
            raise fromargs.contract_error(exc, context=f"record {record!r}") from exc
        cave[wheel.name] = wheel
        return wheel

    return app


def _find(cave: dict[str, Wheel], name: str) -> Wheel:
    """Return the named wheel, or fail with the names an agent can retry."""
    if name not in cave:
        known = ", ".join(sorted(cave))
        raise fromargs.CliError(f"unknown wheel {name!r}; known wheels: {known}")
    return cave[name]


def main(argv: list[str] | None = None) -> int:
    return build_app(starter_cave()).run(argv)


if __name__ == "__main__":
    sys.exit(main())
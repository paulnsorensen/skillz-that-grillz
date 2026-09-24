"""cheese-cave: an example agent-friendly CLI built on fromargs.

The CLI tracks wheels of cheese as they ripen. Run it from the repository root:

    uv run --project lib/fromargs python lib/fromargs/examples/cheese_cave.py wheels list

Agents often send malformed argv. Each call below still runs the intended
command, and ``run`` prints one ``note:`` line on stderr for each repair:

    cheese_cave.py --json wheels list              # moves --json after 'wheels list'
    cheese_cave.py age brie --weeks "2 --dry-run"  # splits the merged argument
    cheese_cave.py --json age brie --weeks "2 --dry-run"  # both repairs

Cyclopts itself binds ``--dry_run`` to ``--dry-run``. It also answers a
misspelled command such as ``wheels lst`` with ``Did you mean "list"?``.
"""

# No `from __future__ import annotations`: Cyclopts resolves the command hints
# at parse time.

import sys
from dataclasses import asdict, dataclass

from cyclopts import App

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


def build_app(cave: dict[str, Wheel]) -> App:
    """Build the cheese-cave CLI over ``cave``; commands change it in place."""
    app = App(name="cheese-cave", help="Track wheels of cheese as they ripen.")
    wheels = App(name="wheels", help="Inspect the wheels in the cave.")
    app.command(wheels)

    @wheels.command(name="list")
    def list_wheels(*, json: bool = False, full: bool = False) -> None:
        """List the wheels, oldest first."""
        ordered = sorted(cave.values(), key=lambda wheel: -wheel.weeks)
        if json:
            fromargs.emit([asdict(wheel) for wheel in ordered], json_mode=True)
            return
        rows = [f"{w.name:<8} {w.style:<12} {w.weeks:>3} weeks" for w in ordered]
        fromargs.emit(rows, limit=LIST_LIMIT, full=full)

    @wheels.command
    def show(name: str, *, json: bool = False) -> None:
        """Show one wheel."""
        fromargs.emit(asdict(_find(cave, name)), json_mode=json)

    @app.command
    def age(name: str, *, weeks: int, dry_run: bool = False, json: bool = False) -> None:
        """Age one wheel for more weeks."""
        if weeks < 1:
            raise fromargs.CliError(f"--weeks must be at least 1, got {weeks}")
        wheel = _find(cave, name)
        total = wheel.weeks + weeks
        if not dry_run:
            wheel.weeks = total
        result = {"name": wheel.name, "weeks": total, "dry_run": dry_run}
        if json:
            fromargs.emit(result, json_mode=True)
        else:
            suffix = " (dry run)" if dry_run else ""
            fromargs.emit(f"{wheel.name} is now {total} weeks old{suffix}")

    @app.command
    def load(record: str, *, json: bool = False) -> None:
        """Add a wheel from a ``name:style:weeks`` record."""
        try:
            name, style, weeks = record.split(":")
            wheel = Wheel(name, style, int(weeks))
        except ValueError as exc:
            raise fromargs.contract_error(exc, context=f"record {record!r}") from exc
        cave[wheel.name] = wheel
        fromargs.emit(asdict(wheel), json_mode=json)

    return app


def _find(cave: dict[str, Wheel], name: str) -> Wheel:
    """Return the named wheel, or fail with the names an agent can retry."""
    if name not in cave:
        known = ", ".join(sorted(cave))
        raise fromargs.CliError(f"unknown wheel {name!r}; known wheels: {known}")
    return cave[name]


def main() -> int:
    return fromargs.run(build_app(starter_cave()))


if __name__ == "__main__":
    sys.exit(main())

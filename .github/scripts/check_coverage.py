#!/usr/bin/env python3
"""Coverage gate: combine the pytest lane data, then check project coverage, dead code, and diff coverage.

    check_coverage.py DATA_DIR [--base REF|auto]

DATA_DIR holds one `*.cov` data file per pytest lane or shard. The gate combines them into DATA_DIR/combined and
writes DATA_DIR/coverage.xml. It then runs three checks and reports every failure:

- project: the combined total must reach `[tool.coverage.report].fail_under`.
- dead-code-coverage: no whole function may have zero hits (check_dead_code_coverage.py).
- diff: the source lines that this change adds must reach `[tool.skillz.coverage].diff_fail_under`.
  `--base auto` resolves the base as the slice guard does (check_slices.resolve_base).

Exit codes: 0 pass, 1 a check fails, 2 input error.
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast

import check_dead_code_coverage as dead_code
from check_slices import resolve_base
from coverage import Coverage
from coverage.exceptions import NoDataError
from slice_map import MapError

ROOT = Path(__file__).resolve().parents[2]
DATA_SUFFIX = ".cov"


def diff_threshold(root: Path) -> float:
    """Return `[tool.skillz.coverage].diff_fail_under`; raise `ValueError` when it is missing or out of range."""
    with (root / "pyproject.toml").open("rb") as stream:
        config = cast(dict[str, object], tomllib.load(stream))
    table = config
    for key in ("tool", "skillz", "coverage"):
        table = cast(dict[str, object], table.get(key, {}))
    value = table.get("diff_fail_under")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        raise ValueError("[tool.skillz.coverage].diff_fail_under must be a number from 0 to 100")
    return float(value)


def combine(data_dir: Path, root: Path) -> tuple[Coverage, list[Path]]:
    """Combine every lane data file in `data_dir` and return the loaded result; raise `ValueError` on no data.

    Each measured process writes `<lane>.cov.<host>.<pid>.<random>`; a combined lane file is `<lane>.cov`.
    """
    lanes = sorted({*data_dir.glob(f"*{DATA_SUFFIX}"), *data_dir.glob(f"*{DATA_SUFFIX}.*")})
    if not lanes:
        raise ValueError(f"{data_dir} holds no *{DATA_SUFFIX} lane data")
    combined = Coverage(data_file=str(data_dir / "combined"), config_file=str(root / "pyproject.toml"))
    combined.combine([str(path) for path in lanes], keep=True)
    combined.save()
    return combined, lanes


def project_check(combined: Coverage) -> tuple[bool, str]:
    """Return whether the total reaches `fail_under`, with the report text."""
    floor = cast(float, combined.get_option("report:fail_under"))
    buffer = io.StringIO()
    total = combined.report(file=buffer, skip_covered=True, skip_empty=True, show_missing=False)
    summary = f"project coverage {total:.2f}% (floor {floor:g}%)"
    if total < floor:
        return False, f"{buffer.getvalue()}{summary}: below the floor; add tests"
    if total >= floor + 1:
        summary += f"; raise [tool.coverage.report].fail_under to {int(total)}"
    return True, summary


def diff_check(coverage_xml: Path, base: str, threshold: float, root: Path) -> tuple[bool, str]:
    """Run diff-cover against `base` and return whether the changed lines reach `threshold`.

    The report names the measured line count, so a diff with no measured line never reads as a pass.
    `--include-untracked` makes a local run see a new file before `git add`; CI has no untracked file.
    """
    report = coverage_xml.with_name("diff-cover.json")
    result = subprocess.run(
        [sys.executable, "-m", "diff_cover.diff_cover_tool", str(coverage_xml), f"--compare-branch={base}",
         f"--fail-under={threshold:g}", "--include-untracked", f"--format=json:{report}", "--quiet"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    output = (result.stdout + result.stderr).strip()
    try:
        stats = cast(dict[str, object], json.loads(report.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return False, output or f"diff coverage: diff-cover wrote no report (exit {result.returncode})"
    lines = cast(int, stats.get("total_num_lines", 0))
    if not lines:
        return result.returncode == 0, f"diff coverage: no measured source line in this diff against {base[:12]}"
    percent = cast(float, stats.get("total_percent_covered", 0))
    report_lines = [f"diff coverage {percent:g}% of {lines} measured lines (floor {threshold:g}%) against {base[:12]}"]
    for path, file_stats in sorted(cast(dict[str, dict[str, object]], stats.get("src_stats", {})).items()):
        missing = cast(list[int], file_stats.get("violation_lines", []))
        if missing:
            report_lines.append(f"  {path}: missing lines {', '.join(map(str, missing))}")
    return result.returncode == 0, "\n".join(filter(None, [output, *report_lines]))


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(prog="check_coverage.py")
    _ = parser.add_argument("data_dir", type=Path)
    _ = parser.add_argument("--base", default="auto")
    args = parser.parse_args(argv)
    data_dir = cast(Path, args.data_dir).resolve()
    try:
        threshold = diff_threshold(root)
        base = resolve_base(root, cast(str, args.base))
        combined, lanes = combine(data_dir, root)
        coverage_xml = data_dir / "coverage.xml"
        _ = combined.xml_report(outfile=str(coverage_xml))
        dead = dead_code.find_whole_dead_symbols(coverage_xml, root, dead_code.load_exemptions(root))
        diff_passed, diff_report = diff_check(coverage_xml, base, threshold, root)
    except (MapError, NoDataError, OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"coverage: {error}", file=sys.stderr)
        return 2
    print(f"coverage: combined {len(lanes)} lane data files")
    passed, report = project_check(combined)
    print(report)
    for symbol in dead:
        print(symbol.report())
    print(diff_report)
    return 0 if passed and not dead and diff_passed else 1


if __name__ == "__main__":
    sys.exit(main())

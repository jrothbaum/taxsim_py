"""List year-keyed parameters with no entry for a target year.

usage: uv run python scripts/year_status.py 2026 [--states ny,ca] [--federal-only] [--all]

For each parameter file it prints `law_coded_through` and every year-keyed
entry still in effect whose latest year is before the target, so values that
are inflation-indexed or scheduled to change can be checked one by one. By
default only entries changed within the five years before the target are
listed (values fixed for longer rarely change); `--all` lists every one. An
entry ending in `null` (a provision that has ended) is not listed. A value
that legitimately carries forward unchanged needs no new entry; this is a
checklist, not an error report.
"""

import argparse
from pathlib import Path

from taxsim_py.engine.schema import PARAMETERS_ROOT, is_year_keyed, load_yaml


def stale_entries(node, target: int, since: int, path: tuple = ()):
    """Yield (path, latest year) for year-keyed entries in effect, last set in `since`..`target - 1`."""
    if is_year_keyed(node):
        latest = max(node)
        if node[latest] is not None and since <= latest < target:
            yield path, latest
        # Year-keyed values can hold further year-keyed tables (rare); look inside the latest.
        if isinstance(node[latest], dict):
            yield from stale_entries(node[latest], target, since, (*path, latest))
        return
    if isinstance(node, dict):
        for key, value in node.items():
            yield from stale_entries(value, target, since, (*path, key))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("year", type=int, help="the tax year being added")
    parser.add_argument("--states", help="comma-separated state abbreviations (default: all)")
    parser.add_argument("--federal-only", action="store_true", help="only the national files")
    parser.add_argument("--all", action="store_true", help="include entries unchanged for more than five years")
    args = parser.parse_args()

    files = sorted((PARAMETERS_ROOT / "national").glob("*.yaml"))
    if not args.federal_only:
        states = sorted((PARAMETERS_ROOT / "states").glob("*/income_tax.yaml"))
        if args.states:
            wanted = {s.strip().lower() for s in args.states.split(",")}
            states = [f for f in states if f.parent.name in wanted]
        files += states

    for file in files:
        params = load_yaml(file)
        since = 0 if args.all else args.year - 5
        rows = list(stale_entries(params, args.year, since))
        coverage = params.get("law_coded_through")
        if not rows and coverage in (None, args.year):
            continue
        name = Path(file).relative_to(PARAMETERS_ROOT)
        print(f"\n{name}" + (f"  (law_coded_through: {coverage})" if coverage is not None else ""))
        for path, latest in rows:
            print(f"  {'.'.join(map(str, path)):60s} last {latest}")


if __name__ == "__main__":
    main()

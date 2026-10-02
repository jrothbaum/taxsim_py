"""Print a PolicyEngine-US parameter value for a state and date.

Example:
    uv run --group test scripts/export_policyengine_parameter.py \
        gov.states.ca.tax.income.rates.single --year 2024

This is an audit/import aid. PolicyEngine parameter paths are intentionally
kept explicit so a copied value can be traced back to its source path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from policyengine_us.system import system  # noqa: E402


def parameter_at(path: str, year: int):
    node = system.parameters
    for part in path.split("."):
        node = getattr(node, part)
    value = node.get_at_instant(f"{year}-01-01")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "item"):
        return value.item()
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="Dotted PolicyEngine parameter path")
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(parameter_at(args.path, args.year), indent=2, default=str))


if __name__ == "__main__":
    main()

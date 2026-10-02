"""Audit PolicyEngine state parameter trees against this project's state files.

This deliberately reports structure and provenance rather than copying values.
PolicyEngine's state models are not a drop-in replacement for the project's
TAXSIM-shaped YAML files, so an import needs a state-specific review.

Example:
    uv run --group test scripts/audit_policyengine_state_parameters.py
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from policyengine_us.system import system  # noqa: E402
from taxsim_py.calculators.states import STATE_CALCULATOR_PATHS  # noqa: E402


YEARS = (2022, 2023, 2024)
NO_INCOME_TAX = {0, 10, 29, 42, 44, 48, 51}


def _node(path: str):
    node = system.parameters
    for part in path.split("."):
        node = getattr(node, part)
    return node


def _has_path(path: str) -> bool:
    try:
        _node(path)
    except (AttributeError, KeyError):
        return False
    return True


def _date_status(path: str) -> str:
    if not _has_path(path):
        return "missing"
    statuses = []
    for year in YEARS:
        try:
            _node(path).get_at_instant(f"{year}-01-01")
        except Exception as exc:  # PolicyEngine uses several parameter types.
            statuses.append(type(exc).__name__)
        else:
            statuses.append("ok")
    return "/".join(statuses)


def _state_abbreviation(taxsim_state: int) -> str | None:
    module, _ = STATE_CALCULATOR_PATHS[taxsim_state]
    if module == "no_income_tax":
        return None
    return module.rstrip("_")


def _local_keys(abbreviation: str) -> list[str]:
    path = ROOT / "parameters" / "states" / abbreviation / "income_tax.yaml"
    with path.open(encoding="utf-8") as stream:
        values = yaml.safe_load(stream) or {}
    return list(values)


def _candidate_paths(abbreviation: str) -> list[str]:
    prefix = f"gov.states.{abbreviation}.tax.income"
    candidates = []
    for suffix in (
        "rates",
        "main",
        "rate",
        "deductions.standard",
        "standard",
        "exemptions",
        "exemption",
        "credits",
    ):
        path = f"{prefix}.{suffix}"
        if _has_path(path):
            candidates.append(f"{suffix} [{_date_status(path)}]")
    return candidates


def _income_node(abbreviation: str):
    path = f"gov.states.{abbreviation}.tax.income"
    try:
        return _node(path)
    except AttributeError:
        return None


def build_report() -> str:
    lines = [
        "# PolicyEngine state parameter audit",
        "",
        "Generated from the installed `policyengine-us` package using its",
        "`system.parameters` tree. The date status columns are `2022/2023/2024`.",
        "",
        "## Interpretation",
        "",
        "A PolicyEngine parameter tree is useful evidence and a source for current",
        "law, but it is not a direct replacement for this project's state YAML.",
        "The state calculators encode TAXSIM-era definitions, deductions, credits,",
        "phaseouts, and filing-status rules. A parameter can only be imported when",
        "the existing calculator's formula means the same thing. Newer law may",
        "require a calculator change, not just a new YAML value.",
        "",
        "The `ok` entries mean that PolicyEngine can return a dated value at that",
        "node. They do not prove that the value matches the project's parameter",
        "definition or official state forms.",
        "",
        "## State-by-state inventory",
        "",
        "| TAXSIM | State | Local YAML | PolicyEngine income children | Candidate paths | Review |",
        "|---:|:---:|:---|:---|:---|:---|",
    ]
    for taxsim_state in sorted(STATE_CALCULATOR_PATHS):
        abbreviation = _state_abbreviation(taxsim_state)
        if abbreviation is None:
            continue
        local_keys = _local_keys(abbreviation)
        income = _income_node(abbreviation)
        children = ", ".join(income.children) if income is not None else "no income subtree"
        candidates = _candidate_paths(abbreviation)
        review = "Manual mapping" if candidates else "Manual source"
        if income is None:
            review = "Special/manual"
        lines.append(
            f"| {taxsim_state} | {abbreviation.upper()} | "
            f"{len(local_keys)} top-level keys | "
            f"{children} | {', '.join(candidates) or 'none'} | {review} |"
        )

    lines.extend(
        [
            "",
            "## Immediate issues to resolve",
            "",
            "1. `state_cpi_extrapolation.yaml` stops at 2023, so mixed-state runs",
            "   for 2024 currently fail before a PolicyEngine comparison can run.",
            "2. Bracket and rate paths are not uniform. For example, California",
            "   uses `income.rates`, New York uses `income.main`, and Pennsylvania",
            "   uses a flat `income.rate`. There is no safe bulk path-to-YAML copy.",
            "3. Several project calculators use federal or TAXSIM-specific inputs",
            "   alongside state parameters. PolicyEngine values for credits,",
            "   exemptions, retirement income, and itemized deductions need a",
            "   formula-level comparison before adoption.",
            "4. A PolicyEngine value can represent a newer statutory rule than the",
            "   current calculator. We should preserve the source path and verify",
            "   against the state's instructions/forms for each changed rule.",
            "5. States with no project calculator or no income tax should not be",
            "   populated merely because PolicyEngine has a parameter subtree.",
            "",
            "## Recommended import order",
            "",
            "Start with states whose existing model is structurally simple and whose",
            "PolicyEngine path has the same meaning: flat-rate states and ordinary",
            "bracket schedules. Add dated YAML values with a source comment, then",
            "compare realistic CPS units and hand-built form cases. Leave credits,",
            "retirement exclusions, local taxes, and special phaseouts for a second",
            "pass with official forms as the independent check.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report()
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")


if __name__ == "__main__":
    main()

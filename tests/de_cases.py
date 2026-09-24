"""Delaware state-tax test cases - tenth state (alphabetical order). See
calculators/states/de.py for the real mechanisms this exercises: the
17-bracket pre-1997 table (`look`) vs the smaller 1997+ table, the married-
joint earner-split relief mechanic, the own Pease-style itemized-deduction
phaseout (1991-2017), the personal exemption/personal-exemption-credit
transition (dies out after 1995), the Child/Dependent Care Credit, the
2021-only unemployment-compensation exclusion, and the 2021+ EITC's
refundable-vs-nonrefundable choice.
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))  # real DE law 1977-2021; 2022-2023 CPI-extrapolated (see de.py)
STATE_DE = 8

_DEFAULTS: dict[str, Any] = {
    "state": STATE_DE,
    "mstat": 1,
    "depx": 0,
    "dep17": 0,
    "dep18": 0,
    "dep6": 0,
    "pwages": 0,
    "swages": 0,
    "proptax": 0,
    "otheritem": 0,
    "mortgage": 0,
    "dep13": 0,
    "childcare": 0,
    "intrec": 0,
    "psemp": 0,
    "ssemp": 0,
    "dividends": 0,
    "stcg": 0,
    "ltcg": 0,
    "ui": 0,
    "pui": 0,
    "sui": 0,
}


def case(year: int, description: str, **overrides: Any) -> dict[str, Any]:
    row = dict(_DEFAULTS)
    row["year"] = year
    row["description"] = f"{description} [year={year}]"
    row.update(overrides)
    return row


def build_de_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 15000, 30000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"DE wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"DE wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"DE wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"DE wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        for depx in (1, 3):
            rows.append(case(year, f"DE dependents, single, depx={depx}", mstat=1, depx=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"DE dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep18=depx, pwages=25000, swages=15000))

        rows.append(case(year, "DE itemized (proptax/otheritem/mortgage), single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "DE itemized, married_joint, high income", mstat=2, pwages=200000, swages=150000, proptax=10000, otheritem=5000, mortgage=15000))
        rows.append(case(year, "DE dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "DE capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "DE self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "DE self-employment, married_joint", mstat=2, pwages=20000, psemp=40000))
        rows.append(case(year, "DE childcare, single low income", mstat=1, pwages=25000, depx=1, dep17=1, dep18=1, childcare=2000))
        rows.append(case(year, "DE low income, single", mstat=1, pwages=8000))
        rows.append(case(year, "DE very high income, single", mstat=1, pwages=800000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "DE very high income, married_joint", mstat=2, pwages=500000, swages=400000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "DE unemployment, single", mstat=1, pwages=10000, ui=8000))
        rows.append(case(year, "DE unemployment, married_joint", mstat=2, pwages=15000, swages=5000, ui=8000, sui=4000))

    return rows

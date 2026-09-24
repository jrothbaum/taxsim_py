"""Alabama state-tax test cases - second state (alphabetical order). See
calculators/states/al.py for the real mechanisms this exercises: its own
bracket tables (with married_joint income-splitting), the standard-vs-
itemized deduction choice across three formula eras, the federal-tax-paid
deduction across its four eras (including 2021's federal-calculator
lookback), and the exemption phase-down (2007+).
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))  # real AL law 1977-2021; 2022-2023 CPI-extrapolated (see al.py)
STATE_AL = 1

_DEFAULTS: dict[str, Any] = {
    "state": STATE_AL,
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


def build_al_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 15000, 50000, 120000, 260000):
            rows.append(case(year, f"AL wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"AL wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"AL wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"AL wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        for depx in (1, 3):
            rows.append(case(year, f"AL dependents, single, depx={depx}", mstat=1, depx=depx, dep18=depx, pwages=30000))

        rows.append(case(year, "AL itemized (proptax/otheritem/mortgage), single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "AL dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "AL capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "AL self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "AL self-employment, married_joint", mstat=2, pwages=20000, psemp=40000))
        rows.append(case(year, "AL unemployment, single", mstat=1, pwages=10000, ui=8000))
        rows.append(case(year, "AL high income, single", mstat=1, pwages=300000))

        if 1982 <= year <= 1986:
            rows.append(case(year, "AL two-earner deduction, married_joint", mstat=2, pwages=25000, swages=20000))

    return rows

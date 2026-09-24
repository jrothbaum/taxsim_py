"""Arizona state-tax test cases - fifth state (alphabetical order). See
calculators/states/az.py for the real mechanisms this exercises: its 10
bracket vintages, its own itemized-deduction Pease-style phaseout, the
federal-tax-as-AGI-subtraction mechanism (<=1989), the Family Income
Credit (1995+), the 2019+ Dependent Tax Credit, and the Excise Tax Credit
(2001+).
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))  # real AZ law 1977-2021; 2022-2023 CPI-extrapolated (see az.py)
STATE_AZ = 3

_DEFAULTS: dict[str, Any] = {
    "state": STATE_AZ,
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


def build_az_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 15000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"AZ wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"AZ wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"AZ wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"AZ wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        for depx in (1, 3):
            rows.append(case(year, f"AZ dependents, single, depx={depx}", mstat=1, depx=depx, dep17=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"AZ dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep17=depx, dep18=depx, pwages=30000, swages=20000))

        rows.append(case(year, "AZ itemized (proptax/otheritem/mortgage), single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "AZ itemized, married_joint, high income", mstat=2, pwages=200000, swages=150000, proptax=10000, otheritem=5000, mortgage=15000))
        rows.append(case(year, "AZ dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "AZ capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "AZ self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "AZ self-employment, married_joint", mstat=2, pwages=20000, psemp=40000))
        rows.append(case(year, "AZ unemployment, single", mstat=1, pwages=10000, ui=8000))
        rows.append(case(year, "AZ high income, single", mstat=1, pwages=500000))
        rows.append(case(year, "AZ low income, single", mstat=1, pwages=5000))
        rows.append(case(year, "AZ low income, married_joint", mstat=2, pwages=3000, swages=2000))

        if 1982 <= year <= 1986:
            rows.append(case(year, "AZ two-earner deduction, married_joint", mstat=2, pwages=25000, swages=20000))

        if year <= 1990:
            rows.append(case(year, "AZ childcare deduction, single low income", mstat=1, pwages=5000, dep17=1, depx=1, dep18=1, childcare=1000))

    return rows

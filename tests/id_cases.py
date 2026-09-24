"""Idaho state-tax test cases - fourteenth state (alphabetical order).
See calculators/states/id.py for the real mechanisms this exercises: the
pre-1987 through 2021+ bracket eras (all four filing statuses sharing one
table, married_joint/HoH's own halve-then-double mechanic, the 2001+
aif01 inflation-adjustment scaling), the itemized-deduction phaseout, the
1977/1978-1992/1993+ standard-deduction eras, the locally-reconstructed
federal personal exemption, the Child Tax Credit, the Grocery Tax Credit,
and the flat $10 Permanent Building Fund Tax add-on.
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))  # real ID law 1977-2021; 2022-2023 CPI-extrapolated (see id.py)
STATE_ID = 13

_DEFAULTS: dict[str, Any] = {
    "state": STATE_ID,
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


def build_id_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 15000, 30000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"ID wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"ID wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"ID wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"ID wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        for depx in (1, 3):
            rows.append(case(year, f"ID dependents, single, depx={depx}", mstat=1, depx=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"ID dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep18=depx, pwages=25000, swages=15000))

        rows.append(case(year, "ID itemized (proptax/otheritem/mortgage), single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "ID itemized, married_joint, high income", mstat=2, pwages=200000, swages=150000, proptax=10000, otheritem=5000, mortgage=15000))
        rows.append(case(year, "ID dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "ID capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "ID capital gains, high income", mstat=1, pwages=200000, ltcg=100000))
        rows.append(case(year, "ID self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "ID self-employment, married_joint", mstat=2, pwages=20000, psemp=40000))
        rows.append(case(year, "ID childcare, single low income", mstat=1, pwages=25000, depx=1, dep17=1, dep18=1, childcare=2000))
        rows.append(case(year, "ID low income, single", mstat=1, pwages=8000))
        rows.append(case(year, "ID low income, married_joint with dependent", mstat=2, depx=1, dep18=1, pwages=10000))
        rows.append(case(year, "ID very high income, single", mstat=1, pwages=800000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "ID very high income, married_joint", mstat=2, pwages=500000, swages=400000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "ID unemployment, single", mstat=1, pwages=10000, ui=8000))
        rows.append(case(year, "ID unemployment, married_joint", mstat=2, pwages=15000, swages=5000, ui=8000, sui=4000))

    return rows

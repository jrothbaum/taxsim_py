"""District of Columbia state-tax test cases - eleventh state (alphabetical
order). See calculators/states/dc.py for the real mechanisms this
exercises: the pre-1987/1987-2016 bracket eras (including the 2007
married-joint-split bracket staleness quirk), the itemized-deduction
SALT-feedback-scaling technique, the 2015-2017 exemption phaseout, the
Child/Dependent Care Credit, the Property Tax Credit ("Schedule H"), the
Low Income Credit (1987-2017), and the EITC (2000+, including the 2015+
childless-worker track).
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))  # real DC law 1977-2021; 2022-2023 CPI-extrapolated (see dc.py)
STATE_DC = 9

_DEFAULTS: dict[str, Any] = {
    "state": STATE_DC,
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


def build_dc_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 15000, 30000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"DC wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"DC wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"DC wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"DC wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        for depx in (1, 3):
            rows.append(case(year, f"DC dependents, single, depx={depx}", mstat=1, depx=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"DC dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep18=depx, pwages=25000, swages=15000))

        rows.append(case(year, "DC itemized (proptax/otheritem/mortgage), single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "DC itemized, married_joint, high income", mstat=2, pwages=200000, swages=150000, proptax=10000, otheritem=5000, mortgage=15000))
        rows.append(case(year, "DC dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "DC capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "DC self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "DC self-employment, married_joint", mstat=2, pwages=20000, psemp=40000))
        rows.append(case(year, "DC childcare, single low income", mstat=1, pwages=25000, depx=1, dep17=1, dep18=1, childcare=2000))
        rows.append(case(year, "DC low income, single", mstat=1, pwages=8000))
        rows.append(case(year, "DC low income, single with dependent", mstat=1, depx=1, dep18=1, pwages=6000))
        rows.append(case(year, "DC very high income, single", mstat=1, pwages=800000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "DC very high income, married_joint", mstat=2, pwages=500000, swages=400000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "DC unemployment, single", mstat=1, pwages=10000, ui=8000))
        rows.append(case(year, "DC unemployment, married_joint", mstat=2, pwages=15000, swages=5000, ui=8000, sui=4000))
        rows.append(case(year, "DC property tax credit, single low income", mstat=1, pwages=15000, proptax=1500))
        rows.append(case(year, "DC property tax credit, single mid income", mstat=1, pwages=40000, proptax=2500))
        rows.append(case(year, "DC EITC, single, 2 kids, low wages", mstat=1, depx=2, dep18=2, pwages=15000))
        rows.append(case(year, "DC EITC, single, childless, low wages", mstat=1, pwages=12000))

    return rows

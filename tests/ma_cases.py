"""Massachusetts state-tax test cases - twenty-second state (alphabetical
order, counting Illinois which was already built pre-session). See
calculators/states/ma.py for the real mechanisms this exercises: the
separate Part A/B/C income classes (interest, dividends, short- and
long-term gains each land in a different part), the exemption spillover
from Part B into the other parts, the payroll-tax and dependent
deductions, and the No Tax Status / Limited Income Credit.
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))
STATE_MA = 22

_DEFAULTS: dict[str, Any] = {
    "state": STATE_MA,
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


def build_ma_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 15000, 30000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"MA wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"MA wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"MA wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"MA wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        for depx in (1, 3):
            rows.append(case(year, f"MA dependents, single, depx={depx}", mstat=1, depx=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"MA dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep18=depx, pwages=25000, swages=15000))
            rows.append(case(year, f"MA dependents, HoH, depx={depx}", mstat=3, depx=depx, dep18=depx, pwages=25000))

        rows.append(case(year, "MA dependents, HoH, depx=5", mstat=3, depx=5, dep18=5, pwages=40000))
        rows.append(case(year, "MA itemized (proptax/otheritem/mortgage), single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "MA itemized, married_joint, high income", mstat=2, pwages=200000, swages=150000, proptax=10000, otheritem=5000, mortgage=15000))
        rows.append(case(year, "MA dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "MA capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "MA capital gains, high income", mstat=1, pwages=200000, ltcg=100000))
        rows.append(case(year, "MA self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "MA self-employment, married_joint", mstat=2, pwages=20000, psemp=40000))
        rows.append(case(year, "MA two-earner joint, balanced wages", mstat=2, pwages=60000, swages=55000))
        rows.append(case(year, "MA two-earner joint, unbalanced wages", mstat=2, pwages=90000, swages=10000))
        rows.append(case(year, "MA childcare, single low income", mstat=1, pwages=20000, depx=1, dep13=1, dep17=1, dep18=1, childcare=2000))
        rows.append(case(year, "MA childcare, single mid income", mstat=1, pwages=30000, depx=1, dep13=1, dep17=1, dep18=1, childcare=2000))
        rows.append(case(year, "MA childcare, single higher income", mstat=1, pwages=50000, depx=1, dep13=1, dep17=1, dep18=1, childcare=2000))
        rows.append(case(year, "MA childcare, single very high income", mstat=1, pwages=90000, depx=1, dep13=1, dep17=1, dep18=1, childcare=2000))
        rows.append(case(year, "MA low income, single", mstat=1, pwages=8000))
        rows.append(case(year, "MA low income, single with dependent", mstat=1, depx=1, dep17=1, dep18=1, pwages=8000))
        rows.append(case(year, "MA low income, married_joint with dependent", mstat=2, depx=1, dep18=1, pwages=10000))
        rows.append(case(year, "MA very high income, single", mstat=1, pwages=800000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "MA very high income, married_joint", mstat=2, pwages=500000, swages=400000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "MA interest, single", mstat=1, pwages=30000, intrec=4000))
        rows.append(case(year, "MA interest, low income single", mstat=1, pwages=3000, intrec=6000))
        rows.append(case(year, "MA dividends and interest, married_joint", mstat=2, pwages=40000, dividends=3000, intrec=2000))
        rows.append(case(year, "MA short-term gain, single", mstat=1, pwages=30000, stcg=6000))
        rows.append(case(year, "MA investment income only, single", mstat=1, dividends=4000, intrec=3000, ltcg=5000))
        rows.append(case(year, "MA unemployment, single", mstat=1, pwages=10000, ui=8000))
        rows.append(case(year, "MA unemployment, married_joint", mstat=2, pwages=15000, swages=5000, ui=8000, sui=4000))

    return rows

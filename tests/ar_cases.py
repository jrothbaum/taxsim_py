"""Arkansas state-tax test cases - sixth state (alphabetical order). See
calculators/states/ar.py for the real mechanisms this exercises: the
per-year low-income table override, the married earner-split relief
mechanic, the 10 bracket-table eras, the Personal Tax Credit, and the
Child/Dependent Care Credit.
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))  # real AR law 1977-2021; 2022-2023 CPI-extrapolated (see ar.py)
STATE_AR = 4

_DEFAULTS: dict[str, Any] = {
    "state": STATE_AR,
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


def build_ar_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 9500, 12500, 20000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"AR wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"AR wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"AR wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"AR wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        rows.append(case(year, "AR two-earner joint, low", mstat=2, pwages=8000, swages=6000))
        rows.append(case(year, "AR two-earner joint, mid", mstat=2, pwages=40000, swages=25000))
        rows.append(case(year, "AR two-earner joint, high", mstat=2, pwages=150000, swages=90000))
        rows.append(case(year, "AR single-earner joint, low", mstat=2, pwages=14000))

        for depx in (1, 3):
            rows.append(case(year, f"AR dependents, single, depx={depx}", mstat=1, depx=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"AR dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep18=depx, pwages=25000, swages=15000))
            rows.append(case(year, f"AR dependents, HoH, depx={depx}", mstat=3, depx=depx, dep18=depx, pwages=22000))

        rows.append(case(year, "AR itemized (proptax/otheritem/mortgage), single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "AR itemized, married_joint, high income", mstat=2, pwages=200000, swages=150000, proptax=10000, otheritem=5000, mortgage=15000))
        rows.append(case(year, "AR dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "AR capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "AR capital gains, high income", mstat=1, pwages=100000, ltcg=50000, stcg=10000))
        rows.append(case(year, "AR unemployment, single", mstat=1, pwages=10000, ui=8000))
        if 1998 <= year <= 2002:
            rows.append(case(year, "AR working credit, self-employment", mstat=1, psemp=100000))
            rows.append(
                case(
                    year,
                    "AR working credit, two earners and self-employment",
                    mstat=2,
                    pwages=30000,
                    swages=20000,
                    psemp=40000,
                )
            )
        rows.append(case(year, "AR childcare, single low income", mstat=1, pwages=15000, depx=1, dep17=1, dep18=1, childcare=2000))
        rows.append(case(year, "AR capital loss with wages, couple", mstat=2, pwages=50000, swages=30000, ltcg=-3000, dividends=2000))
        rows.append(case(year, "AR large capital loss, single", mstat=1, pwages=42000, ltcg=-8000))
        rows.append(case(year, "AR long-term gain with short-term loss, single", mstat=1, pwages=40000, ltcg=12000, stcg=-4000))

    return rows

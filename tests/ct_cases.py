"""Connecticut state-tax test cases - ninth state (alphabetical order). See
calculators/states/ct.py for the real mechanisms this exercises: the
pre-1991 cap-gains/dividends/interest-only tax, the 1991+ exemption and
bracket tax, the 2011+ 3%-bracket phaseout and Tax Recapture, the
Personal Tax Credit (`tablki` interpolation), the AMT (1993+), the
Property Tax Credit (1996+), and the EITC (2011+).
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))  # real CT law 1977-2021; 2022-2023 CPI-extrapolated (see ct.py)
STATE_CT = 7

_DEFAULTS: dict[str, Any] = {
    "state": STATE_CT,
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


def build_ct_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 15000, 30000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"CT wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"CT wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"CT wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"CT wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        for depx in (1, 3):
            rows.append(case(year, f"CT dependents, single, depx={depx}", mstat=1, depx=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"CT dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep18=depx, pwages=25000, swages=15000))

        rows.append(case(year, "CT itemized (proptax/otheritem/mortgage), single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "CT itemized, married_joint, high income", mstat=2, pwages=200000, swages=150000, proptax=10000, otheritem=5000, mortgage=15000))
        rows.append(case(year, "CT dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "CT dividends only, single (pre-1991-relevant)", mstat=1, dividends=25000))
        rows.append(case(year, "CT interest only, single (pre-1991-relevant)", mstat=1, intrec=25000))
        rows.append(case(year, "CT capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "CT capital gains only, single (pre-1991-relevant)", mstat=1, ltcg=40000))
        rows.append(case(year, "CT self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "CT childcare, single low income", mstat=1, pwages=25000, depx=1, dep17=1, dep18=1, childcare=2000))
        rows.append(case(year, "CT low income, single", mstat=1, pwages=8000))
        rows.append(case(year, "CT property tax credit, single", mstat=1, pwages=40000, proptax=2500))
        rows.append(case(year, "CT property tax credit, married_joint high income", mstat=2, pwages=90000, swages=60000, proptax=6000))
        rows.append(case(year, "CT very high income, single (AMT/recapture-relevant)", mstat=1, pwages=800000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "CT very high income, married_joint (AMT/recapture-relevant)", mstat=2, pwages=500000, swages=400000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "CT very high income, married_separate (AMT/recapture-relevant)", mstat=6, pwages=350000, proptax=15000, otheritem=8000, mortgage=20000))
        rows.append(case(year, "CT very high income, HoH (AMT/recapture-relevant)", mstat=3, depx=1, dep18=1, pwages=400000, proptax=20000, otheritem=10000, mortgage=25000))
        rows.append(case(year, "CT unemployment, single", mstat=1, pwages=10000, ui=8000))

    return rows

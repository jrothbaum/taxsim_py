"""California state-tax test cases - seventh state (alphabetical order).
See calculators/states/ca.py for the real mechanisms this exercises: its
own AMT, exemption credit phaseouts, low income/child care/elderly
credits, and its own EITC.
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))  # real CA law 1977-2021; 2022-2023 CPI-extrapolated (see ca.py)
STATE_CA = 5

_DEFAULTS: dict[str, Any] = {
    "state": STATE_CA,
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


def build_ca_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 15000, 30000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"CA wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"CA wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"CA wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"CA wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        for depx in (1, 3):
            rows.append(case(year, f"CA dependents, single, depx={depx}", mstat=1, depx=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"CA dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep18=depx, pwages=25000, swages=15000))
            rows.append(case(year, f"CA dependents, HoH, depx={depx}", mstat=3, depx=depx, dep18=depx, pwages=22000))
        # Unemployment compensation is exempt.
        rows.append(case(year, "CA unemployment, single", mstat=1, pwages=20000, ui=8000))
        rows.append(case(year, "CA unemployment, low wages", mstat=1, pwages=5000, ui=8000))
        rows.append(case(year, "CA unemployment, married_joint", mstat=2, pwages=40000, swages=10000, ui=12000, pui=4000, sui=8000))

        rows.append(case(year, "CA itemized (proptax/otheritem/mortgage), single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "CA itemized, married_joint, high income", mstat=2, pwages=200000, swages=150000, proptax=10000, otheritem=5000, mortgage=15000))
        rows.append(case(year, "CA dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "CA capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "CA capital gains, high income", mstat=1, pwages=100000, ltcg=50000, stcg=10000))
        rows.append(case(year, "CA self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "CA self-employment, married_joint high", mstat=2, pwages=80000, psemp=100000))
        rows.append(case(year, "CA childcare, single low income", mstat=1, pwages=25000, depx=1, dep17=1, dep18=1, childcare=2000))
        rows.append(case(year, "CA childcare above federal tax, single two kids", mstat=1, pwages=16000, depx=2, dep13=2, dep17=2, dep18=2, childcare=5000))
        rows.append(case(year, "CA childcare, married_joint mid income", mstat=2, pwages=45000, swages=30000, depx=2, dep17=2, dep18=2, childcare=6000))
        rows.append(case(year, "CA low income, single", mstat=1, pwages=8000))
        rows.append(case(year, "CA very high income, single (AMT-relevant)", mstat=1, pwages=800000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "CA AMT, large property tax with rental income", mstat=2, pwages=65000, otherprop=76000, proptax=99997))
        rows.append(case(year, "CA AMT, large property tax with small rental income", mstat=2, pensions=83000, otherprop=1000, proptax=99997))
        rows.append(case(year, "CA very high income, married_joint (AMT-relevant)", mstat=2, pwages=500000, swages=400000, proptax=30000, otheritem=15000, mortgage=40000))

    return rows

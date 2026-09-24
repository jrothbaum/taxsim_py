"""Iowa state-tax test cases - sixteenth state (alphabetical order,
counting Illinois which was already built pre-session). See
calculators/states/ia.py for the real mechanisms this exercises: the
four bracket eras, the "married filing combined" income-split mechanic,
the Alternate Tax, AMT, the Personal Exemption Credit, the Child/
Dependent Care Credit, and the three EITC eras.
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))  # real IA law 1977-2021; 2022-2023 CPI-extrapolated (see ia.py)
STATE_IA = 16

_DEFAULTS: dict[str, Any] = {
    "state": STATE_IA,
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


def build_ia_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 15000, 30000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"IA wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"IA wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"IA wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"IA wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        rows.append(case(year, "IA wages, married_joint, both spouses earn", mstat=2, pwages=60000, swages=40000))
        rows.append(case(year, "IA wages, married_joint, one spouse earns", mstat=2, pwages=100000))

        for depx in (1, 3):
            rows.append(case(year, f"IA dependents, single, depx={depx}", mstat=1, depx=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"IA dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep18=depx, pwages=25000, swages=15000))

        rows.append(case(year, "IA itemized (proptax/otheritem/mortgage), single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "IA itemized, married_joint, high income", mstat=2, pwages=200000, swages=150000, proptax=10000, otheritem=5000, mortgage=15000))
        rows.append(case(year, "IA dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "IA dividends+interest, single, 1981-style", mstat=1, pwages=20000, dividends=2000, intrec=1000))
        rows.append(case(year, "IA capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "IA capital gains, high income", mstat=1, pwages=200000, ltcg=100000))
        rows.append(case(year, "IA capital gains+itemized, very high income, single", mstat=1, pwages=300000, proptax=15000, mortgage=20000, ltcg=50000))
        rows.append(case(year, "IA self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "IA self-employment, married_joint", mstat=2, pwages=20000, psemp=40000))
        rows.append(case(year, "IA childcare, single low income", mstat=1, pwages=25000, depx=1, dep17=1, dep18=1, childcare=2000))
        rows.append(case(year, "IA low income, single", mstat=1, pwages=8000))
        rows.append(case(year, "IA low income, single with dependent", mstat=1, depx=1, dep17=1, dep18=1, pwages=8000))
        rows.append(case(year, "IA low income, married_joint with dependent", mstat=2, depx=1, dep18=1, pwages=10000))
        rows.append(case(year, "IA low income, married_joint with 3 dependents", mstat=2, depx=3, dep17=3, dep18=3, pwages=15000))
        rows.append(case(year, "IA very high income, single", mstat=1, pwages=800000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "IA very high income, married_joint", mstat=2, pwages=500000, swages=400000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "IA unemployment, single", mstat=1, pwages=10000, ui=8000))
        rows.append(case(year, "IA unemployment, married_joint", mstat=2, pwages=15000, swages=5000, ui=8000, sui=4000))

    return rows

"""Indiana state-tax test cases - fifteenth state (alphabetical order,
counting Illinois which was already built pre-session). See
calculators/states/in.py for the real mechanisms this exercises: the flat
tax rate, the multi-era exemption formula, Indiana's own Unemployment
Compensation exclusion (real every year with nonzero UI, not just 2009/
2020), the 1981-only dividend/interest exclusion swap, and the three
distinct EITC-equivalent eras (1999-2002's own formula, 2003-2008's flat
percentage of federal EITC, 2009+'s percentage-of-federal-EITC with a
2011+ own schedule floor).
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))  # real IN law 1977-2021; 2022-2023 CPI-extrapolated (see in.py)
STATE_IN = 15

_DEFAULTS: dict[str, Any] = {
    "state": STATE_IN,
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


def build_in_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 15000, 30000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"IN wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"IN wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"IN wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"IN wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        for depx in (1, 3):
            rows.append(case(year, f"IN dependents, single, depx={depx}", mstat=1, depx=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"IN dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep18=depx, pwages=25000, swages=15000))

        rows.append(case(year, "IN itemized-style (proptax/mortgage), single", mstat=1, pwages=60000, proptax=4000, mortgage=8000))
        rows.append(case(year, "IN itemized-style, married_joint, high income", mstat=2, pwages=200000, swages=150000, proptax=10000, mortgage=15000))
        rows.append(case(year, "IN dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "IN dividends+interest, single, 1981-style", mstat=1, pwages=20000, dividends=2000, intrec=1000))
        rows.append(case(year, "IN capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "IN capital gains, high income", mstat=1, pwages=200000, ltcg=100000))
        rows.append(case(year, "IN self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "IN self-employment, married_joint", mstat=2, pwages=20000, psemp=40000))
        rows.append(case(year, "IN childcare, single low income", mstat=1, pwages=25000, depx=1, dep17=1, dep18=1, childcare=2000))
        rows.append(case(year, "IN low income, single", mstat=1, pwages=8000))
        # TAXSIM leaves a two-cent single-precision residue in taxable income.
        rows.append(case(year, "IN low income, single with dependent", mstat=1, depx=1, dep17=1, dep18=1, pwages=8000, oracle_divergent=year in {1997, 1998}))
        rows.append(case(year, "IN low income, married_joint with dependent", mstat=2, depx=1, dep18=1, pwages=10000))
        rows.append(case(year, "IN low income, married_joint with 3 dependents", mstat=2, depx=3, dep17=3, dep18=3, pwages=15000))
        rows.append(case(year, "IN very high income, single", mstat=1, pwages=800000, proptax=30000, mortgage=40000))
        rows.append(case(year, "IN very high income, married_joint", mstat=2, pwages=500000, swages=400000, proptax=30000, mortgage=40000))
        rows.append(case(year, "IN unemployment, single", mstat=1, pwages=10000, ui=8000))
        rows.append(case(year, "IN unemployment, married_joint", mstat=2, pwages=15000, swages=5000, ui=8000, sui=4000))
        rows.append(case(year, "IN unemployment, high income, single", mstat=1, pwages=100000, ui=8000))

    return rows

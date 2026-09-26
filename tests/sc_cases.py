"""South Carolina state-tax test cases."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))
STATE_SC = 41

_DEFAULTS: dict[str, Any] = {
    "state": STATE_SC,
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


def build_sc_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 9000, 14000, 20000, 30000, 50000, 90000, 150000, 260000, 500000):
            rows.append(case(year, f"SC wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"SC wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"SC wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"SC wages, HoH, wages={wages}", mstat=3, depx=1, dep17=1, dep18=1, pwages=wages))

        # Joint returns split between spouses (through 1988) and income splitting (1989-1990).
        for wages in ((20000, 20000), (30000, 25000), (45000, 5000), (70000, 60000), (150000, 1000), (6000, 4000)):
            rows.append(case(year, f"SC two-earner joint, wages={wages}", mstat=2, pwages=wages[0], swages=wages[1]))
        for depx in (1, 2, 3, 4):
            rows.append(case(year, f"SC dependents, single, depx={depx}", mstat=1, depx=depx, dep17=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"SC dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep17=depx, dep18=depx, pwages=25000, swages=15000))
            rows.append(case(year, f"SC dependents, married_separate, depx={depx}", mstat=6, depx=depx, dep17=depx, dep18=depx, pwages=30000))

        # Itemized deductions and the state-tax addback.
        rows.append(case(year, "SC itemized, single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "SC itemized, married_joint", mstat=2, pwages=90000, swages=40000, proptax=6000, otheritem=3000, mortgage=14000))
        rows.append(case(year, "SC itemized, married_joint, high income", mstat=2, pwages=300000, swages=150000, proptax=12000, otheritem=6000, mortgage=25000))
        rows.append(case(year, "SC itemized, married_separate", mstat=6, pwages=90000, proptax=6000, mortgage=12000))
        rows.append(case(year, "SC itemized, HoH", mstat=3, depx=1, dep17=1, dep18=1, pwages=80000, proptax=5000, mortgage=10000))
        rows.append(case(year, "SC itemized, very high income single", mstat=1, pwages=800000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "SC itemized, low income", mstat=1, pwages=15000, proptax=1500, mortgage=4000))
        rows.append(case(year, "SC mortgage and property tax above cap, married_joint", mstat=2, pwages=150000, proptax=12000, mortgage=18000))

        # Other income types.
        rows.append(case(year, "SC dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "SC small dividends, married_joint", mstat=2, pwages=30000, dividends=150))
        rows.append(case(year, "SC interest, single", mstat=1, pwages=20000, intrec=3000))
        rows.append(case(year, "SC interest, married_joint", mstat=2, pwages=20000, swages=10000, intrec=500))
        rows.append(case(year, "SC capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "SC capital gains, married_joint", mstat=2, pwages=50000, swages=20000, ltcg=30000))
        rows.append(case(year, "SC short-term gain, married_joint", mstat=2, pwages=40000, stcg=8000))
        rows.append(case(year, "SC capital loss, single", mstat=1, pwages=30000, ltcg=-5000))
        rows.append(case(year, "SC self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "SC self-employment, low", mstat=1, psemp=15000))
        rows.append(case(year, "SC self-employment, married_joint", mstat=2, pwages=20000, psemp=40000, ssemp=70000))
        rows.append(case(year, "SC self-employment, high", mstat=1, psemp=250000))
        rows.append(case(year, "SC unemployment, single", mstat=1, pwages=20000, ui=8000))
        rows.append(case(year, "SC unemployment mostly, single", mstat=1, pwages=4000, ui=9000))
        rows.append(case(year, "SC unemployment, married_joint", mstat=2, pwages=15000, swages=5000, ui=8000, sui=4000))

        # Credits: child care, child credit and deduction, EITC match.
        for wages in (8000, 15000, 25000, 40000, 70000, 110000):
            rows.append(case(year, f"SC children, single, wages={wages}", mstat=1, depx=2, dep17=2, dep18=2, pwages=wages))
            rows.append(case(year, f"SC children, married_joint, wages={wages}", mstat=2, depx=3, dep17=3, dep18=3, pwages=wages, swages=wages // 2))
            rows.append(case(year, f"SC children, married_separate, wages={wages}", mstat=6, depx=1, dep17=1, dep18=1, pwages=wages))
        for wages in (12000, 20000, 30000, 45000, 90000):
            rows.append(case(year, f"SC childcare, married_joint, wages={wages}", mstat=2, depx=2, dep13=2, dep17=2, dep18=2, pwages=wages, swages=12000, childcare=5000))
            rows.append(case(year, f"SC childcare, single, wages={wages}", mstat=1, depx=1, dep13=1, dep17=1, dep18=1, pwages=wages, childcare=3000))
            rows.append(case(year, f"SC childcare, HoH three children, wages={wages}", mstat=3, depx=3, dep13=3, dep17=3, dep18=3, pwages=wages, childcare=8000))
        for wages in (20000, 35000, 50000, 70000, 95000, 130000):
            rows.append(case(year, f"SC child deduction, married_joint, wages={wages}", mstat=2, depx=2, dep17=2, dep18=2, pwages=wages))
            rows.append(case(year, f"SC child deduction, HoH, wages={wages}", mstat=3, depx=1, dep17=1, dep18=1, pwages=wages))
        for wages in (4000, 9000, 15000, 22000, 30000):
            rows.append(case(year, f"SC low income, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"SC low income, married_joint, depx=2, wages={wages}", mstat=2, depx=2, dep17=2, dep18=2, pwages=wages))
            rows.append(case(year, f"SC low income, HoH, depx=1, wages={wages}", mstat=3, depx=1, dep17=1, dep18=1, pwages=wages))

        # High income: exemption phaseouts, top brackets and the 2009-2010 surtax.
        for wages in (120000, 180000, 240000, 330000, 700000):
            rows.append(case(year, f"SC high income, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"SC high income, married_joint, wages={wages}", mstat=2, depx=2, dep17=2, dep18=2, pwages=wages, swages=wages // 4))
            rows.append(case(year, f"SC high income, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"SC high income, HoH, wages={wages}", mstat=3, depx=1, dep17=1, dep18=1, pwages=wages))

        # Two-earner credit.
        for wages in ((40000, 40000), (60000, 45000), (90000, 85000), (150000, 120000), (300000, 250000)):
            rows.append(case(year, f"SC two earners, wages={wages}", mstat=2, pwages=wages[0], swages=wages[1]))
        rows.append(case(year, "SC dividends and gains, single", mstat=1, pwages=40000, dividends=6000, ltcg=12000, stcg=2000))
        rows.append(case(year, "SC long gain with short loss, married_joint", mstat=2, pwages=60000, ltcg=20000, stcg=-5000))

        # Itemizers around the federal high-income limits, and young children.
        for wages in (90000, 180000, 320000, 700000):
            rows.append(case(year, f"SC itemizer, joint, wages={wages}", mstat=2, depx=2, dep17=2, dep18=2, pwages=wages, swages=wages // 3, proptax=9000, otheritem=6000, mortgage=20000))
            rows.append(case(year, f"SC itemizer, single, wages={wages}", mstat=1, pwages=wages, proptax=6000, mortgage=15000))
        for wages in (20000, 50000):
            rows.append(case(year, f"SC young children, joint, wages={wages}", mstat=2, depx=2, dep6=1, dep13=2, dep17=2, dep18=2, pwages=wages, childcare=4000))
        rows.append(case(year, "SC self-employed couple", mstat=2, psemp=40000, pwages=30000, swages=25000))

    return rows

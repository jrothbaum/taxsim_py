"""New Mexico state-tax test cases."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))
STATE_NM = 32

_DEFAULTS: dict[str, Any] = {
    "state": STATE_NM,
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


def build_nm_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 12000, 20000, 30000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"NM wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"NM wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"NM wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"NM wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        for depx in (1, 2, 4):
            rows.append(case(year, f"NM dependents, single, depx={depx}", mstat=1, depx=depx, dep17=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"NM dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep17=depx, dep18=depx, pwages=25000, swages=15000))
            rows.append(case(year, f"NM dependents, married_separate, depx={depx}", mstat=6, depx=depx, dep17=depx, dep18=depx, pwages=30000))

        # Married couples taxed as separate returns when lower.
        for wages in ((20000, 20000), (30000, 25000), (45000, 5000), (70000, 60000), (150000, 1000)):
            rows.append(case(year, f"NM two-earner joint, wages={wages}", mstat=2, pwages=wages[0], swages=wages[1]))
        rows.append(case(year, "NM two-earner joint with investment income", mstat=2, pwages=30000, swages=10000, intrec=8000, dividends=4000))
        rows.append(case(year, "NM two-earner joint, itemized", mstat=2, pwages=40000, swages=30000, proptax=4000, mortgage=12000))

        # Itemized deductions, the state-tax exclusion and the high-income limit.
        rows.append(case(year, "NM itemized, single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "NM itemized, married_joint, high income", mstat=2, pwages=200000, swages=150000, proptax=10000, otheritem=5000, mortgage=15000))
        rows.append(case(year, "NM itemized, married_separate", mstat=6, pwages=90000, proptax=6000, mortgage=12000))
        rows.append(case(year, "NM itemized, HoH", mstat=3, depx=1, dep18=1, pwages=80000, proptax=5000, mortgage=10000))
        rows.append(case(year, "NM itemized, very high income single", mstat=1, pwages=800000, proptax=30000, otheritem=15000, mortgage=40000))
        rows.append(case(year, "NM itemized, low income", mstat=1, pwages=15000, proptax=1500, mortgage=4000))

        # Other income types.
        rows.append(case(year, "NM dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "NM small dividends, married_joint", mstat=2, pwages=30000, dividends=150))
        rows.append(case(year, "NM dividends and interest, single", mstat=1, pwages=20000, dividends=300, intrec=500))
        rows.append(case(year, "NM interest, low income single", mstat=1, pwages=3000, intrec=6000))
        rows.append(case(year, "NM capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "NM capital gains, high income", mstat=1, pwages=200000, ltcg=100000))
        rows.append(case(year, "NM short-term gain, married_joint", mstat=2, pwages=40000, stcg=8000))
        rows.append(case(year, "NM capital loss with dividends, single", mstat=1, pwages=30000, dividends=3000, ltcg=-5000))
        rows.append(case(year, "NM short-term loss offsetting long-term gain, single", mstat=1, pwages=30000, stcg=-8000, ltcg=20000))
        rows.append(case(year, "NM capital loss, married_separate", mstat=6, pwages=40000, stcg=-5000))
        rows.append(case(year, "NM self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "NM self-employment, low", mstat=1, psemp=15000))
        rows.append(case(year, "NM self-employment, married_joint", mstat=2, pwages=20000, psemp=40000))
        rows.append(case(year, "NM self-employment, high", mstat=1, psemp=250000))
        rows.append(case(year, "NM unemployment mostly, single", mstat=1, pwages=4000, ui=9000))
        rows.append(case(year, "NM unemployment, single", mstat=1, pwages=20000, ui=8000))
        rows.append(case(year, "NM unemployment, married_joint", mstat=2, pwages=15000, swages=5000, ui=8000, sui=4000))

        # Federal credits reduce the deductible federal tax.
        for wages in (8000, 15000, 25000, 40000, 70000):
            rows.append(case(year, f"NM children, single, wages={wages}", mstat=1, depx=2, dep17=2, dep18=2, pwages=wages))
            rows.append(case(year, f"NM children, married_joint, wages={wages}", mstat=2, depx=3, dep17=3, dep18=3, pwages=wages, swages=wages // 2))
        for wages in (20000, 45000, 90000):
            rows.append(case(year, f"NM childcare, married_joint, wages={wages}", mstat=2, depx=2, dep13=2, dep17=2, dep18=2, pwages=wages, swages=12000, childcare=5000))
            rows.append(case(year, f"NM childcare, single, wages={wages}", mstat=1, depx=1, dep13=1, dep17=1, dep18=1, pwages=wages, childcare=3000))
        rows.append(case(year, "NM low income, single", mstat=1, pwages=15000))
        rows.append(case(year, "NM low income, married_joint two earners", mstat=2, pwages=15000, swages=12000))
        rows.append(case(year, "NM wages above payroll wage base, married_joint", mstat=2, pwages=180000, swages=160000, proptax=9000, mortgage=20000))
        rows.append(case(year, "NM self-employment itemizer", mstat=1, psemp=90000, proptax=6000, mortgage=15000))
        rows.append(case(year, "NM homeowner, low income", mstat=1, pwages=18000, proptax=1200))
        # Low income rebate by household size and the low/middle income exemption.
        for wages in (0, 3000, 6000, 9000, 14000, 20000, 30000, 45000):
            for depx in (0, 2, 5):
                rows.append(case(year, f"NM low income, married_joint, depx={depx}, wages={wages}", mstat=2, depx=depx, dep17=depx, dep18=depx, pwages=wages))
            rows.append(case(year, f"NM low income, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"NM low income, married_separate, wages={wages}", mstat=6, pwages=wages))
        rows.append(case(year, "NM capital gains, married_separate", mstat=6, pwages=30000, ltcg=20000))
        rows.append(case(year, "NM small capital gain, single", mstat=1, pwages=30000, ltcg=1500))
        # Interest and dividends around the exemption.
        for amount in (500, 1000, 2000, 3000, 5000, 20000, 100000):
            rows.append(case(year, f"NM interest and dividends, single, amount={amount}", mstat=1, pwages=30000, intrec=amount // 2, dividends=amount - amount // 2))
            rows.append(case(year, f"NM interest and dividends, married_joint, amount={amount}", mstat=2, pwages=30000, intrec=amount // 2, dividends=amount - amount // 2))
            rows.append(case(year, f"NM interest and dividends, married_separate, amount={amount}", mstat=6, intrec=amount))
            rows.append(case(year, f"NM dividends, HoH, amount={amount}", mstat=3, depx=2, dep18=2, dividends=amount))
        # High-income additional tax bands (1993-2017).
        for wages in (150000, 200000, 350000, 700000, 1200000):
            rows.append(case(year, f"NM high income, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"NM high income, married_joint, wages={wages}", mstat=2, pwages=wages, swages=wages // 4))
            rows.append(case(year, f"NM high income, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"NM high income, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))
        rows.append(case(year, "NM high AGI, low taxable income", mstat=1, pwages=40000, ltcg=150000, stcg=-3000, proptax=20000, mortgage=90000))
        for wages in (12000, 18000, 24000, 28000):
            rows.append(case(year, f"NM refundable childcare, single, wages={wages}", mstat=1, depx=1, dep13=1, dep17=1, dep18=1, pwages=wages, childcare=3000))
        rows.append(case(year, "NM capital gains, married_joint", mstat=2, pwages=50000, swages=20000, ltcg=30000))
        rows.append(case(year, "NM childcare with large expense, low income", mstat=1, depx=3, dep13=3, dep17=3, dep18=3, pwages=16000, childcare=6000))

    return rows

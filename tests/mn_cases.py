"""Minnesota state-tax test cases."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))
STATE_MN = 24

# Minnesota's head-of-household standard deduction for 2019 on uses the
# real law amounts, which TAXSIM's table sets equal to the single amounts.
_REAL_LAW_HOH_YEARS = range(2019, 2024)

_DEFAULTS: dict[str, Any] = {
    "state": STATE_MN,
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


def _real_law_hoh(row: dict[str, Any]) -> bool:
    head_of_household = row["mstat"] == 3 or (row["mstat"] == 1 and row.get("depx", 0) > 0)
    return head_of_household and row["year"] in _REAL_LAW_HOH_YEARS


def shared_case_divergent(row: dict[str, Any]) -> bool:
    """Shared new-input cases affected by the head-of-household divergence."""
    return _real_law_hoh(row)


def case(year: int, description: str, **overrides: Any) -> dict[str, Any]:
    row = dict(_DEFAULTS)
    row["year"] = year
    row["description"] = f"{description} [year={year}]"
    row.update(overrides)
    row["oracle_divergent"] = row.get("oracle_divergent", False) or _real_law_hoh(row)
    return row


def build_mn_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 15000, 30000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"MN wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"MN wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"MN wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"MN wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        for depx in (1, 2, 3):
            rows.append(case(year, f"MN dependents, single, depx={depx}", mstat=1, depx=depx, dep17=depx, dep18=depx, pwages=30000))
            rows.append(case(year, f"MN dependents, married_joint, depx={depx}", mstat=2, depx=depx, dep17=depx, dep18=depx, pwages=25000, swages=15000))
            rows.append(case(year, f"MN dependents, HoH, depx={depx}", mstat=3, depx=depx, dep17=depx, dep18=depx, pwages=25000))

        # Working Family Credit across its phase-in, plateau and phase-out.
        for wages in (3000, 6000, 9000, 12000, 16000, 20000, 25000, 32000):
            for depx in (0, 1, 2, 3):
                rows.append(case(year, f"MN working family, single, depx={depx}, wages={wages}", mstat=1, depx=depx, dep17=depx, dep18=depx, pwages=wages))
            rows.append(case(year, f"MN working family, married_joint, depx=2, wages={wages}", mstat=2, depx=2, dep17=2, dep18=2, pwages=wages))
        rows.append(case(year, "MN working family, married_joint, depx=3", mstat=2, depx=3, dep17=3, dep18=3, pwages=28000))
        rows.append(case(year, "MN working family, married_joint, no children", mstat=2, pwages=9000))

        # Marriage credit: two earners with similar wages.
        for wages in ((30000, 25000), (45000, 40000), (70000, 60000), (120000, 110000)):
            rows.append(case(year, f"MN two-earner joint, wages={wages}", mstat=2, pwages=wages[0], swages=wages[1]))
        rows.append(case(year, "MN two-earner joint, unbalanced wages", mstat=2, pwages=90000, swages=10000))
        rows.append(case(year, "MN two-earner joint, self-employed spouse", mstat=2, pwages=40000, ssemp=35000))

        # Property tax refund for homeowners.
        for wages, proptax in ((8000, 1500), (15000, 2500), (25000, 3000), (40000, 4000), (60000, 5000), (80000, 6000)):
            rows.append(case(year, f"MN property tax, single, wages={wages}, proptax={proptax}", mstat=1, pwages=wages, proptax=proptax))
            rows.append(case(year, f"MN property tax, married_joint with kids, wages={wages}, proptax={proptax}", mstat=2, depx=2, dep17=2, dep18=2, pwages=wages, proptax=proptax))
        rows.append(case(year, "MN property tax, capital loss", mstat=1, pwages=20000, ltcg=-6000, proptax=2500))

        # Child and dependent care credit.
        for wages in (12000, 20000, 28000, 40000, 70000):
            rows.append(case(year, f"MN childcare, single, wages={wages}", mstat=1, pwages=wages, depx=1, dep13=1, dep17=1, dep18=1, childcare=3000))
            rows.append(case(year, f"MN childcare, married_joint two kids, wages={wages}", mstat=2, pwages=wages, swages=8000, depx=2, dep13=2, dep17=2, dep18=2, childcare=6000))

        # Itemized deductions, state-tax addback and minimum tax.
        rows.append(case(year, "MN itemized, single", mstat=1, pwages=60000, proptax=4000, otheritem=2000, mortgage=8000))
        rows.append(case(year, "MN itemized, married_joint, high income", mstat=2, pwages=200000, swages=150000, proptax=10000, otheritem=5000, mortgage=15000))
        rows.append(case(year, "MN itemized, married_separate", mstat=6, pwages=90000, proptax=6000, mortgage=12000))
        rows.append(case(year, "MN itemized, HoH", mstat=3, depx=1, dep18=1, pwages=80000, proptax=5000, mortgage=10000))
        rows.append(case(year, "MN minimum tax, large mortgage", mstat=1, pwages=150000, proptax=15000, mortgage=60000))
        rows.append(case(year, "MN minimum tax, married_joint, large deductions", mstat=2, pwages=250000, proptax=25000, otheritem=10000, mortgage=90000))
        rows.append(case(year, "MN very high income, single", mstat=1, pwages=800000, proptax=30000, otheritem=15000, mortgage=40000))

        # Other income types.
        rows.append(case(year, "MN dividends, single", mstat=1, pwages=30000, dividends=5000))
        rows.append(case(year, "MN interest, low income single", mstat=1, pwages=3000, intrec=6000))
        rows.append(case(year, "MN capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(
            year,
            "MN capital gains, high income",
            mstat=1,
            pwages=200000,
            ltcg=100000,
            # In 2005 TAXSIM leaves v34/v35 from the rejected forced-
            # itemize branch while liability and v36 use standard.
            oracle_divergent=year == 2005,
        ))
        rows.append(case(year, "MN short-term gain, married_joint", mstat=2, pwages=40000, stcg=8000))
        rows.append(case(year, "MN capital loss with dividends, single", mstat=1, pwages=30000, dividends=3000, ltcg=-5000))
        rows.append(case(year, "MN short-term loss offsetting long-term gain, single", mstat=1, pwages=30000, stcg=-8000, ltcg=20000))
        rows.append(case(year, "MN capital loss, married_separate", mstat=6, pwages=40000, stcg=-5000))
        rows.append(case(year, "MN self-employment, single", mstat=1, psemp=50000))
        rows.append(case(year, "MN self-employment, married_joint", mstat=2, pwages=20000, psemp=40000))
        rows.append(case(year, "MN unemployment mostly, single", mstat=1, pwages=4000, ui=9000))
        rows.append(case(year, "MN unemployment, single", mstat=1, pwages=20000, ui=8000))
        rows.append(case(year, "MN unemployment, married_joint", mstat=2, pwages=15000, swages=5000, ui=8000, sui=4000))

    return rows

"""Illinois state-tax test cases - the first state this project has built.
Mirrors tests/federal_cases.py's table-driven pattern (one growing table,
one runner) but scoped to a single state, across its full real range
(1977-2021, plus 2022 which exercises the shared CPI-extrapolation
mechanism in engine/state_extrapolation.py - real IL law is only ever coded
through 2021, see parameters/states/il/income_tax.yaml).
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))  # real IL law 1977-2021; 2022-2023 CPI-extrapolated (see il.py)
STATE_IL = 14

_DEFAULTS: dict[str, Any] = {
    "state": STATE_IL,
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


def build_il_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        # Plain wage sweep, single and married_joint - IL is flat-rate, so
        # this mainly exercises the exemption amount and (2022 only) the
        # CPI-extrapolation deflate/inflate round-trip at different income
        # levels.
        for wages in (1, 15000, 50000, 120000, 260000, 500000):
            rows.append(case(year, f"IL wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"IL wages, married_joint, wages={wages}", mstat=2, pwages=wages))

        # Dependents (personal exemption count, and EITC eligibility via
        # the federal credit it multiplies).
        for depx in (1, 3):
            rows.append(
                case(
                    year,
                    f"IL dependents, single, depx={depx}, wages=30000",
                    mstat=1,
                    depx=depx,
                    dep18=depx,
                    pwages=30000,
                )
            )

        # Property tax: three real formula shapes over time (see
        # income_tax.yaml) - none at all before 1983, an AGI-reducing
        # itemized deduction 1983-1990 (doubled 1989-1990), a nonrefundable
        # credit from 1991 on (with a $250,000/$500,000 AGI cliff added
        # 2017+). Exercise all three shapes plus the era boundaries
        # themselves (1982/1983, 1988/1989, 1990/1991, 2016/2017).
        for proptax, wages in ((0, 30000), (3000, 30000), (10000, 30000)):
            rows.append(
                case(year, f"IL property tax, single, proptax={proptax}, wages={wages}", mstat=1, proptax=proptax, pwages=wages)
            )
        if year in (1982, 1983, 1988, 1989, 1990, 1991):
            rows.append(case(year, "IL property tax era boundary, single", mstat=1, proptax=3000, pwages=30000))
        if year >= 2017:
            for wages in (249000, 251000):
                rows.append(case(year, f"IL property tax cliff, single, wages={wages}", mstat=1, proptax=3000, pwages=wages))
            for wages in (498000, 502000):
                rows.append(
                    case(year, f"IL property tax cliff, married_joint, wages={wages}", mstat=2, proptax=3000, pwages=wages)
                )

        # Dividends - exercises the federal AGI dividend-exclusion addback
        # (real and non-negligible for 1977-1986's law79-era federal years,
        # negligible from 1987 on - see income_tax.yaml's scope note).
        rows.append(case(year, "IL dividends, single", mstat=1, pwages=30000, dividends=5000))

        # Illinois EITC: real 2000+ only, non-refundable 2000-2002
        # (a real double-subtraction quirk in its own clamp ceiling, found
        # and fixed via a live oracle probe - see calculators/states/il.py),
        # refundable 2003+. Low-income with qualifying children so the
        # federal credit itself is nonzero; also probe the exact
        # non-refundable clamp case (low wages + a property tax credit
        # that consumes nearly all the pre-credit tax) for 2000-2002
        # specifically.
        if year >= 2000:
            for wages in (10000, 15000, 25000):
                rows.append(case(year, f"IL EITC, single, 2 kids, wages={wages}", mstat=1, depx=2, dep18=2, pwages=wages))
        if 2000 <= year <= 2002:
            rows.append(
                case(
                    year,
                    "IL EITC non-refundable clamp, single, 1 kid",
                    mstat=1,
                    depx=1,
                    dep18=1,
                    pwages=8000,
                    proptax=2000,
                )
            )

        # married_separate and other income sources (interest, capital
        # gains, self-employment) - AGI should pass through all of these
        # the same way federal AGI does.
        rows.append(case(year, "IL married_separate, wages=30000", mstat=6, pwages=30000))
        rows.append(case(year, "IL interest income, single", mstat=1, pwages=20000, intrec=8000))
        rows.append(case(year, "IL capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "IL self-employment, single", mstat=1, psemp=40000))

    return rows

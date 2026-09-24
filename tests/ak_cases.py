"""Alaska state-tax test cases - third state (alphabetical order). Real
only 1977-1979 (repealed 1980+) - see calculators/states/ak.py.
"""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = [1977, 1978, 1979, 1980, 1990, 2022, 2023]  # 1980/1990/2022/2023 confirm the post-repeal $0 case
STATE_AK = 2

_DEFAULTS: dict[str, Any] = {
    "state": STATE_AK,
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


def build_ak_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for wages in (1, 5000, 9000, 11000, 30000, 80000, 260000):
            rows.append(case(year, f"AK wages, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"AK wages, married_joint, wages={wages}", mstat=2, pwages=wages))
            rows.append(case(year, f"AK wages, married_separate, wages={wages}", mstat=6, pwages=wages))
            rows.append(case(year, f"AK wages, HoH, wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))

        rows.append(case(year, "AK dividends, single", mstat=1, pwages=20000, dividends=5000))
        rows.append(case(year, "AK capital gains, single", mstat=1, pwages=20000, ltcg=15000))
        rows.append(case(year, "AK self-employment, single", mstat=1, psemp=40000))
        rows.append(case(year, "AK itemized, single", mstat=1, pwages=60000, proptax=4000, mortgage=8000))

    return rows
